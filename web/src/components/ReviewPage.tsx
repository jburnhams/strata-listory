// The one question: "Which of the model's guesses do I need to confirm?" The finding: how many books are waiting and how many files one answer settles.
// Why it is shaped this way: the identify model decides everything it is sure of (Audible and Open Library together) and leaves only the doubtful groups, already ordered by how much a single answer is worth (files settled, model doubt, other books by the same author).
// So the screen is one endless list of those guesses with two actions per card, Accept and Not this book, both on the keyboard, and answering never leaves the list: the card folds away with an Undo window and the next one is already under it.
// Built from strata-react's useInfiniteLoad/InfiniteList (cursor "after the last group held", never an offset, because answered groups drop out of the queue), useUndoable (answers are saved when the window closes, and flush() before a retrain), useActiveItem and useHotkeys.
import { useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, X } from 'lucide-react'
import { AsyncButton, Button, Done, EmptyState, InfiniteList, KeysLegend, Metric, MetricStrip, PageHeader, Skeleton, Status, UndoBar, format, useActiveItem, useAsyncAction, useHotkeys, useInfiniteLoad, useUndoable, type ActiveItem } from 'strata-react'
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

function Card({ it, active, itemProps, onAccept, onReject }: { it: IdentifyItem; active: boolean; itemProps: ReturnType<ActiveItem<IdentifyItem>['itemProps']>; onAccept: () => void; onReject: () => void }) {
  const c = confidence(it.score, it.margin), local = it.item
  const authors = it.authors?.join(', ') || 'unknown author'
  return (
    <article {...itemProps} aria-label={it.title ?? 'Match'} className={`mb-3 rounded-surface border bg-card p-4 transition-state ${active ? 'border-ring' : 'border-border'}`}>
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
    </article>
  )
}

function Skeletons() {
  return <div className="space-y-3" aria-hidden>{[0, 1, 2].map(i => <Skeleton key={i} className="h-40 w-full" />)}</div>
}

export default function ReviewPage() {
  const qc = useQueryClient()
  const undoable = useUndoable()
  const load = useInfiniteLoad({
    queryKey: ['identify-queue'],
    queryFn: ({ cursor }) => identifyApi.queue(cursor, PAGE),
    items: page => page.items, itemKey: item => item.group, hasMore: page => page.items.length >= PAGE,
    isRemoved: undoable.isRemoved,
    staleTime: 0,          // coming back to the page refetches, so cards answered meanwhile do not return
  })
  const [note, setNote] = useState<string | null>(null)       // what happened to the pipeline when the last answer was saved, if it could not be approved
  const summaryQuery = useQuery({ queryKey: ['identify-summary'], queryFn: async () => (await identifyApi.queue(undefined, 1)).summary, staleTime: 0 })
  const summary = summaryQuery.data
  const items = useMemo(() => (load.status === 'ready' ? load.data : []), [load])
  const active = useActiveItem(items, i => i.group, { noun: 'match' })

  const answer = (it: IdentifyItem, decision: 'approve' | 'reject') => undoable.schedule({
    id: it.group,
    message: `${decision === 'approve' ? 'Accepted' : 'Rejected'} ${it.title ?? 'the match'} for ${format.count(it.files, 'file')}.`,
    failure: `${it.title ?? 'The match'} is back in the list: the answer was not saved.`,
    onError: () => undefined,          // the hook restores the card and says so in the bar; this only marks the failure as handled when the page was left meanwhile
    commit: async () => {
      const { pipeline } = await identifyApi.decide(it.group, decision, it.work ?? '')
      setNote(pipeline.status === 'skipped' ? `Saved for training, but ${it.title ?? 'the book'} was not approved in the pipeline: ${pipeline.note}.` : null)
      await qc.invalidateQueries({ queryKey: ['identify-summary'] })
    },
  })
  const accept = () => active.activeItem && answer(active.activeItem, 'approve')
  const reject = () => active.activeItem && answer(active.activeItem, 'reject')
  const shortcuts = [
    { keys: ['a'], label: 'Accept', run: accept, disabled: !active.activeItem },
    { keys: ['r'], label: 'Not this book', run: reject, disabled: !active.activeItem },
    ...active.shortcuts,
    { keys: ['u'], label: 'Undo', run: undoable.undo, disabled: !undoable.pending },
  ]
  useHotkeys(shortcuts)

  const retrain = useAsyncAction(async () => {
    await undoable.flush()          // the retrain learns from every answer given, including one still inside its undo window
    await identifyApi.refresh(true)
    await Promise.all([qc.invalidateQueries({ queryKey: ['identify-queue'] }), qc.invalidateQueries({ queryKey: ['identify-summary'] })])
  }, { settleMs: 4000 })
  const retrainButton = (variant: 'outline' | 'default') => <AsyncButton variant={variant} phase={retrain.phase} error={retrain.error} onClick={() => void retrain.run()} labels={{ idle: 'Retrain with my answers', pending: 'Retraining…', done: 'Retrained', error: 'Retrain failed' }} />

  const pendingCount = undoable.pending ? 1 : 0
  const remaining = summary ? Math.max(0, summary.remaining - pendingCount) : 0
  const answeredNow = (summary?.decided ?? 0) + pendingCount
  const finding = summary ? { text: remaining > 0 ? `${format.count(remaining, 'book')} to check. The first one settles ${format.count(active.activeItem?.files ?? items[0]?.files ?? 0, 'file')}.` : 'Every doubtful match has an answer.' } : undefined

  return (
    <div className="space-y-5">
      <PageHeader title="Review" scope="Doubtful matches, most useful first" finding={finding} actions={retrainButton('outline')} />
      {summary && (
        <MetricStrip label="Where identification stands">
          <Metric label="Waiting for you" value={format.integer.format(remaining)} note={`${format.count(summary.queue_files, 'file')} in the last scoring run`} />
          <Metric label="Answered here" value={format.integer.format(answeredNow)} note="Used as training labels at the next retrain" />
          <Metric label="Accepted automatically" value={format.integer.format(summary.accepted)} note={`${format.count(summary.accepted_files, 'file')} the model was sure of`} />
        </MetricStrip>
      )}
      <InfiniteList state={load} label="Matches to review" skeleton={<Skeletons />} moreSkeleton={<Skeletons />} moreLabel="Show more matches" estimateSize={230} height={Math.max(480, Math.round(window.innerHeight * 0.75))} keepAtLeast={4}
        empty={!summary
          ? <EmptyState title="Nothing has been scored yet" action={<Button variant="default" onClick={() => identifyApi.refresh(false).then(() => { void qc.invalidateQueries({ queryKey: ['identify-queue'] }); void qc.invalidateQueries({ queryKey: ['identify-summary'] }) })}>Score the library</Button>}>
              The review list is built from the model's scores for every book in the library. Score the library once and the doubtful matches appear here.
            </EmptyState>
          : <Done title={`Answered ${format.count(answeredNow, 'doubtful match', 'doubtful matches')}`} next="Retrain so the model learns from your answers and the next scoring run shows what is still unsure." actions={retrainButton('default')} />}>
        {it => <Card it={it} active={it.group === active.activeKey} itemProps={active.itemProps(it.group)} onAccept={() => answer(it, 'approve')} onReject={() => answer(it, 'reject')} />}
      </InfiniteList>
      {note && <p role="status" className="type-body-sm text-warning">{note}</p>}
      <KeysLegend shortcuts={shortcuts} />
      <UndoBar undoable={undoable} />
    </div>
  )
}
