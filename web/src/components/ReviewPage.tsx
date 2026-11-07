// The one question: "Which of the model's guesses do I need to confirm?" The finding: how many books are waiting and how many files one answer settles.
// Why it is shaped this way: the identify model decides everything it is sure of (Audible and Open Library together) and leaves only the doubtful groups, already ordered by how much a single answer is worth (files settled, model doubt, other books by the same author).
// So the screen is one endless list of those guesses with two actions per card, Accept and Not this book, both on the keyboard, and answering never leaves the list: the card folds away with an Undo window and the next one is already under it.
// Pages are requested "after" the last group held, never by offset, because answered groups drop out of the queue and would shift an offset.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useInfiniteQuery } from '@tanstack/react-query'
import { Check, X } from 'lucide-react'
import { AsyncButton, Button, Done, EmptyState, Metric, MetricStrip, PageHeader, Skeleton, Status, UndoBar, format, useAsyncAction, useUndoable } from 'strata-react'
import { identifyApi, type IdentifyItem } from '../api'

const PAGE = 20

export function folderOf(path: string) {
  const parts = path.split(/[\\/]/).filter(Boolean)
  return parts.length > 2 ? parts.slice(-3, -1).join(' / ') : parts.slice(0, -1).join(' / ')
}

/** How sure the model is, in words: the score alone hides a near tie with the runner-up, which is the case that needs a person. */
export function confidence(score: number, margin: number): { tone: 'success' | 'info' | 'warning'; word: string } {
  if (margin < 0.1) return { tone: 'warning', word: `Close call · ${Math.round(score * 100)}%` }
  if (score >= 0.9) return { tone: 'success', word: `Likely · ${Math.round(score * 100)}%` }
  return { tone: 'info', word: `Unsure · ${Math.round(score * 100)}%` }
}

function Card({ it, active, onAccept, onReject, onFocus }: { it: IdentifyItem; active: boolean; onAccept: () => void; onReject: () => void; onFocus: () => void }) {
  const c = confidence(it.score, it.margin), local = it.item
  const authors = it.authors?.join(', ') || 'unknown author'
  const ref = useRef<HTMLLIElement>(null)
  useEffect(() => { if (active) ref.current?.scrollIntoView?.({ block: 'nearest' }) }, [active])
  return (
    <li ref={ref} onClick={onFocus} aria-current={active ? 'true' : undefined} className={`rounded-surface border bg-card p-4 transition-state ${active ? 'border-ring' : 'border-border'}`}>
      <div className="flex flex-wrap items-start gap-x-4 gap-y-3">
        <div className="min-w-0 flex-1 basis-72 space-y-3">
          <section aria-label="What the files say">
            <p className="type-caption text-muted-foreground">Your files{local.path ? ` · ${folderOf(local.path)}` : ''}</p>
            <p className="type-body break-words">{local.title}{local.author ? <span className="text-muted-foreground"> by {local.author}</span> : <span className="text-muted-foreground"> · no author in the tags</span>}</p>
          </section>
          <section aria-label="The model's match">
            <p className="type-caption text-muted-foreground">Matched to</p>
            <p className="type-heading break-words">{it.title ?? 'No title'} <span className="type-body text-muted-foreground">by {authors}</span></p>
            <p className="type-caption text-muted-foreground tabular-nums">
              {it.editions != null && <>{format.count(it.editions, 'edition')} · </>}{format.count(it.candidates, 'candidate')} considered
              {it.work && <> · <a className="underline" href={`https://openlibrary.org${it.work}`} target="_blank" rel="noreferrer">Open Library<span className="sr-only"> page for {it.title}</span></a></>}
            </p>
          </section>
        </div>
        <div className="flex flex-col items-start gap-2 sm:items-end">
          <Status tone={c.tone}>{c.word}</Status>
          <p className="type-caption text-muted-foreground tabular-nums">Settles {format.count(it.files, 'file')}</p>
        </div>
      </div>
      <div className="mt-4 flex flex-wrap gap-2">
        <Button variant={active ? 'default' : 'outline'} onClick={onAccept} aria-label={`Accept ${it.title}`}><Check aria-hidden className="size-4" /> Accept</Button>
        <Button variant="outline" onClick={onReject} aria-label={`Not ${it.title}`}><X aria-hidden className="size-4" /> Not this book</Button>
      </div>
    </li>
  )
}

function Skeletons() {
  return <div className="space-y-3" aria-hidden>{[0, 1, 2].map(i => <Skeleton key={i} className="h-40 w-full" />)}</div>
}

export default function ReviewPage() {
  const query = useInfiniteQuery({
    queryKey: ['identify-queue'],
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) => identifyApi.queue(pageParam, PAGE),
    getNextPageParam: last => last.items.length >= PAGE ? last.items[last.items.length - 1].group : undefined,
    staleTime: Infinity,
    gcTime: 0,          // leaving the page drops the list, so coming back never shows cards that were answered meanwhile
  })
  const [gone, setGone] = useState<Set<string>>(new Set())            // answered (or waiting out the undo window), hidden at once
  const [answered, setAnswered] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [activeGroup, setActiveGroup] = useState<string | null>(null)
  const { pending, schedule, undo } = useUndoable(5000)
  // An answer still inside its undo window is saved by `flush` before a retrain, so the retrain learns from every answer given.
  const flush = useRef<(() => Promise<void>) | null>(null)
  const [retrainPhase, retrain] = useAsyncAction(async () => { await flush.current?.(); if (flush.current) undo(); flush.current = null; await identifyApi.refresh(true); setGone(new Set()); setAnswered(0); await query.refetch() }, 4000)

  const items = useMemo(() => {
    const seen = new Set<string>(), out: IdentifyItem[] = []
    for (const p of query.data?.pages ?? []) for (const i of p.items) if (!seen.has(i.group) && !gone.has(i.group)) { seen.add(i.group); out.push(i) }
    return out
  }, [query.data, gone])
  const summary = query.data?.pages[0]?.summary
  const active = items.find(i => i.group === activeGroup) ?? items[0]

  const answer = useCallback((it: IdentifyItem, decision: 'approve' | 'reject') => {
    setError(null)
    // Next card takes the focus so the keyboard flow never stops.
    const idx = items.findIndex(i => i.group === it.group); setActiveGroup(items[idx + 1]?.group ?? items[idx - 1]?.group ?? null)
    setGone(g => new Set(g).add(it.group))
    let saved: Promise<void> | null = null
    const save = () => saved ??= identifyApi.decide(it.group, decision, it.work ?? '').then(() => setAnswered(n => n + 1), e => { setError(`Could not save that answer for ${it.title}: ${e.message}`); setGone(g => { const n = new Set(g); n.delete(it.group); return n }) })
    flush.current = save
    schedule({ id: it.group, message: `${decision === 'approve' ? 'Accepted' : 'Rejected'} ${it.title ?? 'the match'} for ${format.count(it.files, 'file')}.`, commit: () => { void save() } })
  }, [items, schedule])
  const undoAnswer = () => { flush.current = null; if (pending) setGone(g => { const n = new Set(g); n.delete(pending.id); return n }); setActiveGroup(pending?.id ?? null); undo() }

  // Keep the list full: load the next page when the end of the list scrolls near.
  const sentinel = useRef<HTMLDivElement>(null)
  const { hasNextPage, isFetchingNextPage, fetchNextPage } = query
  useEffect(() => {
    const el = sentinel.current
    if (!el || !hasNextPage || typeof IntersectionObserver === 'undefined') return
    const io = new IntersectionObserver(e => { if (e[0].isIntersecting && !isFetchingNextPage) fetchNextPage() }, { rootMargin: '600px' })
    io.observe(el); return () => io.disconnect()
  }, [hasNextPage, isFetchingNextPage, fetchNextPage, items.length])
  // Fewer cards left than a screen but more on the server: fetch rather than wait for a scroll that cannot happen.
  useEffect(() => { if (items.length < 4 && hasNextPage && !isFetchingNextPage) fetchNextPage() }, [items.length, hasNextPage, isFetchingNextPage, fetchNextPage])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null
      if (e.metaKey || e.ctrlKey || e.altKey || t?.closest('input, textarea, select, [contenteditable]')) return
      const i = active ? items.findIndex(x => x.group === active.group) : -1
      if (e.key === 'j') setActiveGroup(items[Math.min(i + 1, items.length - 1)]?.group ?? null)
      else if (e.key === 'k') setActiveGroup(items[Math.max(i - 1, 0)]?.group ?? null)
      else if (e.key === 'a' && active) answer(active, 'approve')
      else if (e.key === 'r' && active) answer(active, 'reject')
      else if (e.key === 'u' && pending) undoAnswer()
    }
    window.addEventListener('keydown', onKey); return () => window.removeEventListener('keydown', onKey)
  })

  const answeredNow = (summary?.decided ?? 0) + answered + (pending ? 1 : 0)
  const remaining = summary ? Math.max(0, summary.remaining - gone.size) : 0
  const finding = query.status === 'success' && summary
    ? { text: remaining > 0 ? `${format.count(remaining, 'book')} to check. The first one settles ${format.count(active?.files ?? 0, 'file')}.` : 'Every doubtful match has an answer.' }
    : undefined

  return (
    <div className="space-y-5">
      <PageHeader title="Review" scope="Doubtful matches, most useful first" finding={finding}
        actions={<AsyncButton variant="outline" phase={retrainPhase} onClick={() => retrain()} labels={{ idle: 'Retrain with my answers', pending: 'Retraining…', done: 'Retrained', error: 'Retrain failed' }} />} />
      {summary && (
        <MetricStrip label="Where identification stands">
          <Metric label="Waiting for you" value={format.integer.format(remaining)} note={`${format.count(summary.queue_files, 'file')} in the last scoring run`} />
          <Metric label="Answered here" value={format.integer.format(answeredNow)} note="Used as training labels at the next retrain" />
          <Metric label="Accepted automatically" value={format.integer.format(summary.accepted)} note={`${format.count(summary.accepted_files, 'file')} the model was sure of`} />
        </MetricStrip>
      )}
      {error && <p role="alert" className="type-body text-danger">{error}</p>}
      {query.status === 'pending' && <Skeletons />}
      {query.status === 'error' && <div role="alert" className="space-y-2"><p className="type-body">Could not load the review queue: {query.error.message}</p><Button onClick={() => query.refetch()}>Try again</Button></div>}
      {query.status === 'success' && !summary && (
        <EmptyState title="Nothing has been scored yet" action={<Button variant="default" onClick={() => identifyApi.refresh(false).then(() => query.refetch())}>Score the library</Button>}>
          The review list is built from the model's scores for every book in the library. Score the library once and the doubtful matches appear here.
        </EmptyState>
      )}
      {query.status === 'success' && summary && items.length === 0 && !hasNextPage && (
        <Done title={`Answered ${format.count(answeredNow, 'doubtful match', 'doubtful matches')}`} next="Retrain so the model learns from your answers and the next scoring run shows what is still unsure."
          actions={<AsyncButton variant="default" phase={retrainPhase} onClick={() => retrain()} labels={{ idle: 'Retrain with my answers', pending: 'Retraining…', done: 'Retrained' }} />} />
      )}
      {items.length > 0 && (
        <ul aria-label="Matches to review" className="space-y-3">
          {items.map(it => <Card key={it.group} it={it} active={it.group === active?.group} onFocus={() => setActiveGroup(it.group)} onAccept={() => answer(it, 'approve')} onReject={() => answer(it, 'reject')} />)}
        </ul>
      )}
      <div ref={sentinel} aria-hidden className="h-px" />
      {isFetchingNextPage && <Skeletons />}
      {items.length > 0 && <p className="type-caption text-muted-foreground">Keys: <kbd>A</kbd> accept, <kbd>R</kbd> not this book, <kbd>J</kbd>/<kbd>K</kbd> next or previous, <kbd>U</kbd> undo.</p>}
      <UndoBar pending={pending} onUndo={undoAnswer} />
    </div>
  )
}
