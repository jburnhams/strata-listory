// The one question: "Is this the right book?" The finding: how many proposed matches are waiting, and how many you have confirmed.
// Why it is shaped this way: the identification model (src/app/linkage) proposes a work for every listen with a probability. The fastest way to many confirmed matches is to start with the
// easy ones, so the queue is one endless list, most certain first, and each card shows everything known side by side (what the files say against the work and its recordings, with the
// evidence the model used) so one look and one key is enough. Answers are saved after a short Undo window; every few dozen answers the model is refitted with them and re-decides the rest,
// so confirming easy matches teaches it to accept more on its own and the harder cards further down change as you go.
// The order and the filters (skip what the model already accepted, hardest or newest first, a kind of listen, a certainty, with or without an Audible recording, words) live in the URL,
// so a way of working the queue can be bookmarked; the keys work on whatever the filters leave, and each filter says how many cards it holds so a choice never leads to nothing.
import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router'
import { Ban, Check, X } from 'lucide-react'
import { AsyncButton, Button, Done, EmptyState, Field, InfiniteList, Input, KeysLegend, Metric, MetricStrip, NativeSelect, PageHeader, Skeleton, Status, UndoBar, format, useActiveItem, useAsyncAction, useHotkeys, useInfiniteLoad, useUndoable, type ActiveItem, type Tone } from 'strata-react'
import { matchApi, type QueueAction, type QueueCandidate, type QueueFacets, type QueueFilters, type QueueItem, type QueueRecording } from '../api'
import { KIND_LABEL, hours } from './ListensPage'

const PAGE = 20

export const SORTS: Record<QueueFilters['sort'], { label: string; scope: string; finding: string }> = {
  certain: { label: 'Most certain first', scope: 'Easiest first', finding: 'most certain first' },
  uncertain: { label: 'Least certain first', scope: 'Hardest first', finding: 'least certain first' },
  recent: { label: 'Most recently added first', scope: 'Newest first', finding: 'most recently added first' },
  oldest: { label: 'Oldest first', scope: 'Oldest first', finding: 'oldest first' },
}
const CERTAINTY: Record<string, string> = { sure: 'Sure (95% or more)', likely: 'Likely (70% or more)', close: 'Close call', unsure: 'Unsure (under 70%)' }
const FILTER_KEYS = ['auto', 'kind', 'status', 'certainty', 'audible', 'q'] as const

/** The queue's order and filters from the page URL; anything unknown reads as the default. */
export function readFilters(params: URLSearchParams): QueueFilters {
  const pick = <T extends string>(key: string, ok: readonly T[], fallback: T): T => { const v = params.get(key) ?? ''; return (ok as readonly string[]).includes(v) ? v as T : fallback }
  return {
    sort: pick('sort', Object.keys(SORTS) as QueueFilters['sort'][], 'certain'), auto: pick('auto', ['skip', 'only'] as const, ''), certainty: pick('certainty', ['sure', 'likely', 'close', 'unsure'] as const, ''),
    audible: pick('audible', ['yes', 'no'] as const, ''), kind: params.get('kind') ?? '', status: params.get('status') ?? '', q: (params.get('q') ?? '').trim(),
  }
}

/** A select option's label with how many cards it would show, "Book (12)". */
const counted = (label: string, n: number | undefined) => n == null ? label : `${label} (${format.integer.format(n)})`

/** The order and the filters, written to the URL as they change (the search box after a short pause). */
function QueueControls({ filters, facets, set, clear }: { filters: QueueFilters; facets: QueueFacets | undefined; set: (key: keyof QueueFilters, value: string) => void; clear: (() => void) | null }) {
  const [typed, setTyped] = useState(filters.q)
  const latestSet = useRef(set); latestSet.current = set      // the pause can span another choice: write the search through the newest URL, not the one it started from
  useEffect(() => { setTyped(filters.q) }, [filters.q])
  useEffect(() => {
    if (typed.trim() === filters.q) return
    const t = setTimeout(() => latestSet.current('q', typed.trim()), 250)
    return () => clearTimeout(t)
  }, [typed]) // eslint-disable-line react-hooks/exhaustive-deps
  const options = (facet: keyof QueueFacets, labels: (v: string) => string, current: string) => {
    const values = Object.keys(facets?.[facet] ?? {}).filter(v => facets![facet][v] > 0)
    if (current && !values.includes(current)) values.push(current)          // a value from the URL that has no cards now stays choosable
    return values.map(v => <option key={v} value={v}>{counted(labels(v), facets?.[facet][v] ?? 0)}</option>)
  }
  return (
    <section aria-label="Order and filters" className="flex flex-wrap items-end gap-3">
      <Field label="Search matches" className="min-w-56 flex-1"><Input type="search" value={typed} onChange={e => setTyped(e.target.value)} placeholder="Title, author, narrator or folder" /></Field>
      <Field label="Order"><NativeSelect value={filters.sort} onChange={e => set('sort', e.target.value)}>{Object.entries(SORTS).map(([v, s]) => <option key={v} value={v}>{s.label}</option>)}</NativeSelect></Field>
      <Field label="Accepted by the model"><NativeSelect value={filters.auto} onChange={e => set('auto', e.target.value)}>
        <option value="">Show them</option><option value="skip">{counted('Skip them', facets?.auto.skip)}</option><option value="only">{counted('Only these', facets?.auto.only)}</option>
      </NativeSelect></Field>
      <Field label="Certainty"><NativeSelect value={filters.certainty} onChange={e => set('certainty', e.target.value)}><option value="">Any certainty</option>{['sure', 'likely', 'close', 'unsure'].map(v => <option key={v} value={v}>{counted(CERTAINTY[v], facets?.certainty[v] ?? 0)}</option>)}</NativeSelect></Field>
      <Field label="Kind of listen"><NativeSelect value={filters.kind} onChange={e => set('kind', e.target.value)}><option value="">Any kind</option>{options('kind', v => KIND_LABEL[v] ?? v, filters.kind)}</NativeSelect></Field>
      <Field label="Audible"><NativeSelect value={filters.audible} onChange={e => set('audible', e.target.value)}>
        <option value="">Any</option><option value="yes">{counted('Has an Audible recording', facets?.audible.yes)}</option><option value="no">{counted('No Audible recording', facets?.audible.no)}</option>
      </NativeSelect></Field>
      <Field label="Status"><NativeSelect value={filters.status} onChange={e => set('status', e.target.value)}><option value="">Any status</option>{options('status', v => v.replaceAll('_', ' '), filters.status)}</NativeSelect></Field>
      {clear && <Button variant="ghost" onClick={clear}>Clear filters</Button>}
    </section>
  )
}

/** How sure the model is, in words. */
export function certainty(p: number, second = 0): { tone: Tone; word: string } {
  const pct = `${Math.round(p * 100)}%`
  if (p >= 0.95) return { tone: 'success', word: `Sure · ${pct}` }
  if (p - second < 0.15 && second > 0.05) return { tone: 'warning', word: `Close call · ${pct}` }
  if (p >= 0.7) return { tone: 'info', word: `Likely · ${pct}` }
  return { tone: 'warning', word: `Unsure · ${pct}` }
}

const EVIDENCE: Record<string, Record<string, [Tone, string]>> = {
  title: { exact_rare: ['success', 'title exact'], exact_common: ['success', 'title exact (common title)'], exact_generic: ['warning', 'title exact but very common'], main: ['info', 'main title matches'], close: ['info', 'title close'], partial: ['warning', 'title partly matches'], weak: ['danger', 'title differs'] },
  author: { all: ['success', 'author matches'], some: ['info', 'some authors match'], surname: ['info', 'surname matches'], conflict: ['danger', 'author differs'] },
  narrator: { match: ['success', 'narrator matches'], conflict: ['danger', 'narrator differs'] },
  runtime: { '1pct': ['success', 'runtime within 1.5%'], '4pct': ['success', 'runtime within 4%'], '12pct': ['info', 'runtime within 12%'], '30pct': ['warning', 'runtime within 30%'], far: ['danger', 'runtime far off'] },
  asin: { match: ['success', 'ASIN matches'], other: ['danger', 'ASIN differs'] },
  year: { same: ['success', 'same year'], close: ['info', 'year within 5'], later: ['neutral', 'recorded later'], earlier: ['danger', 'year before the book'] },
  numbers: { agree: ['success', 'numbers agree'], local_only: ['warning', 'number only in your title'], other_only: ['warning', 'number only in theirs'], differ: ['danger', 'numbers differ'] },
  sources: { both: ['success', 'Open Library and Audible'], openlibrary: ['neutral', 'Open Library only'], audible: ['neutral', 'Audible only'] },
}

/** The comparison levels the model used, as words with a tone; missing fields are left out (they were no evidence either way). */
export function evidence(e: QueueCandidate['evidence']): { key: string; tone: Tone; word: string }[] {
  return Object.entries(EVIDENCE).flatMap(([field, levels]) => { const lvl = e[field as keyof typeof e]; const hit = lvl ? levels[lvl] : undefined; return hit ? [{ key: field, tone: hit[0], word: hit[1] }] : [] })
}

/** A recording's runtime against the listen's, as a signed percentage. */
export function runtimeDelta(rec: QueueRecording, minutes: number | null | undefined) {
  if (!rec.minutes || !minutes) return ''
  const d = Math.round(((rec.minutes - minutes) / minutes) * 100)
  return d === 0 ? 'same length' : `${d > 0 ? '+' : ''}${d}%`
}

/** The subtitle Audible gives a recording of this work, which Open Library's work title lacks: the recording "David Mitchell: Back Story" of the work "David Mitchell" gives "Back Story". The chosen recording is read first; '' when no recording continues the work title after a colon. */
export function subtitleOf(c: Pick<QueueCandidate, 'title' | 'recordings' | 'recording'>): string {
  const t = (c.title ?? '').trim().toLowerCase()
  if (!t) return ''
  for (const r of [...c.recordings].sort((a, b) => Number(b.asin === c.recording) - Number(a.asin === c.recording))) {
    const rt = (r.title ?? '').trim()
    if (rt.toLowerCase().startsWith(`${t}:`)) { const rest = rt.slice(t.length + 1).replace(/\s*\((un)?abridged\)\s*$/i, '').trim(); if (rest) return rest }
  }
  return ''
}

/** The title to show for a proposed work: the matched Audible recording's own title when there is one ("A Night to Remember", where Open Library's work is titled "La Nuit du Titanic"; "David Mitchell: Back Story" for the work "David Mitchell"), else the work title with any Audible subtitle added, else "Untitled". */
export function displayTitle(c: Pick<QueueCandidate, 'title' | 'recordings' | 'recording'>): string {
  const rec = c.recordings.find(r => r.asin === c.recording)
  if (rec?.title?.trim()) return rec.title.trim()
  const sub = subtitleOf(c)
  return (c.title ?? 'Untitled') + (sub ? `: ${sub}` : '')
}

/** Names with spellings that differ only in case, accents or spacing collapsed to one ("Walter Lord" from Open Library and "Walter LORD" from Audible), keeping the spelling with the fewest all-capital words, then the first. */
export function uniqueNames(names: string[]): string[] {
  const key = (n: string) => n.normalize('NFKD').replace(/\p{M}/gu, '').toLowerCase().replace(/\s+/g, ' ').trim()
  const shouting = (n: string) => n.split(/\s+/).filter(w => w.length > 1 && w === w.toUpperCase() && w !== w.toLowerCase()).length
  const best = new Map<string, string>()
  for (const n of names) { const k = key(n), cur = best.get(k); if (k && (cur === undefined || shouting(n) < shouting(cur))) best.set(k, n) }
  return [...best.values()]
}

function Fact({ label, value }: { label: string; value: unknown }) {
  if (value == null || value === '') return null
  return <><dt className="text-muted-foreground">{label}</dt><dd className="min-w-0 break-words">{String(value)}</dd></>
}

function Files({ it }: { it: QueueItem }) {
  const c = it.chosen, t = it.tags, first = it.copies[0]
  return (
    <section aria-label="What your files say" className="min-w-0 flex-1 basis-72 space-y-2">
      <p className="type-caption text-muted-foreground">Your files</p>
      <p className="type-heading break-words">{String(c.title ?? it.label)}{c.author ? <span className="type-body text-muted-foreground"> by {String(c.author)}</span> : <span className="type-body text-muted-foreground"> · no author</span>}</p>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 type-body-sm">
        <Fact label="Read by" value={c.narrator} />
        <Fact label="Year" value={c.year} />
        <Fact label="Length" value={it.copies.map(x => hours(x.minutes ?? 0)).filter(Boolean).join(' · ')} />
        <Fact label="Folder" value={first?.folder} />
        <Fact label="Album tag" value={t.album !== c.title ? t.album : null} />
        <Fact label="Artist tag" value={t.artist !== c.author ? t.artist : null} />
        <Fact label="Composer tag" value={t.composer !== c.narrator ? t.composer : null} />
        <Fact label="Publisher" value={c.publisher ?? t.publisher} />
        <Fact label="ASIN" value={c.asin} />
        <Fact label="Files" value={first ? `${first.files ?? '?'} files${it.copies.length > 1 ? ` · ${it.copies.length} copies` : ''}` : null} />
      </dl>
      {first?.group_id ? <Link className="type-caption underline" to={`/files?group=${first.group_id}`}>Open the files</Link> : null}
    </section>
  )
}

function Cover({ isbn, title }: { isbn?: string; title: string }) {
  if (!isbn) return null
  return <img src={`https://covers.openlibrary.org/b/isbn/${isbn}-M.jpg?default=false`} alt={`Cover of ${title}`} loading="lazy" className="h-28 w-auto shrink-0 rounded-control border border-border object-contain" onError={e => { e.currentTarget.style.display = 'none' }} />
}

function Work({ c, minutes }: { c: QueueCandidate; minutes: number | null | undefined }) {
  const title = displayTitle(c), asin = c.recording || c.recordings[0]?.asin
  return (
    <section aria-label="The proposed work" className="min-w-0 flex-1 basis-72 space-y-2">
      <p className="type-caption text-muted-foreground">Proposed work</p>
      <div className="flex gap-3">
        <Cover isbn={c.isbns[0]} title={title} />
        <div className="min-w-0 space-y-1">
          <p className="type-heading break-words">{title} <span className="type-body text-muted-foreground">by {uniqueNames(c.authors).join(', ') || 'unknown author'}</span></p>
          <p className="type-caption text-muted-foreground tabular-nums">
            {[c.year, c.editions ? format.count(c.editions, 'edition') : null, c.recordings.length ? format.count(c.recordings.length, 'Audible recording') : null].filter(Boolean).join(' · ')}
            {c.ol[0] && <> · <a className="underline" href={`https://openlibrary.org${c.ol[0]}`} target="_blank" rel="noreferrer">Open Library{c.title && c.title !== title ? ` (${c.title})` : ''}</a></>}
            {asin && <> · <a className="underline" href={`https://www.audible.co.uk/pd/${asin}`} target="_blank" rel="noreferrer">Audible</a></>}
          </p>
        </div>
      </div>
      {c.recordings.length > 0 && (
        <ul aria-label="Audible recordings of this work" className="type-body-sm space-y-0.5">
          {c.recordings.slice(0, 4).map(r => (
            <li key={r.asin} className={r.asin === c.recording ? 'font-medium' : 'text-muted-foreground'}>
              {r.narrators.join(', ') || 'unknown narrator'} · {hours(r.minutes ?? 0)} <span className="tabular-nums">({runtimeDelta(r, minutes)})</span>{r.fmt && r.fmt !== 'unabridged' ? ` · ${r.fmt.replace('_', ' ')}` : ''}{r.year ? ` · ${r.year}` : ''}
            </li>
          ))}
        </ul>
      )}
      <div className="flex flex-wrap gap-1" aria-label="Evidence">{evidence(c.evidence).map(e => <Status key={e.key} tone={e.tone}>{e.word}</Status>)}</div>
    </section>
  )
}

function Card({ it, active, itemProps, onAnswer }: { it: QueueItem; active: boolean; itemProps: ReturnType<ActiveItem<QueueItem>['itemProps']>; onAnswer: (action: QueueAction, c?: QueueCandidate) => void }) {
  const top = it.candidates[0], others = it.candidates.slice(1), sure = certainty(top.p, top.second ?? 0)
  return (
    <article {...itemProps} aria-label={it.label || 'Match'} className={`mb-3 rounded-surface border bg-card p-4 transition-state ${active ? 'border-ring' : 'border-border'}`}>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Status tone={sure.tone}>{sure.word}</Status>
        {top.auto && <Status tone="neutral">Accepted automatically, not yet confirmed</Status>}
        {it.kind && it.kind !== 'book' && <Status tone="neutral">{KIND_LABEL[it.kind] ?? it.kind}</Status>}
        {it.added != null && <span className="type-caption text-muted-foreground">Added {format.shortDay.format(new Date(it.added * 1000))}</span>}
      </div>
      <div className="flex flex-wrap gap-x-6 gap-y-4">
        <Files it={it} />
        <Work c={top} minutes={it.copies[0]?.minutes} />
      </div>
      {others.length > 0 && (
        <div className="mt-3 border-t border-border pt-2">
          <p className="type-caption text-muted-foreground">Other candidates</p>
          <ul className="type-body-sm">
            {others.map(o => (
              <li key={o.part_id} className="flex flex-wrap items-center gap-2 py-0.5">
                <span className="tabular-nums text-muted-foreground">{Math.round(o.p * 100)}%</span>
                <span className="min-w-0 break-words">{o.title} <span className="text-muted-foreground">by {o.authors.join(', ') || 'unknown'}{o.year ? ` · ${o.year}` : ''}</span></span>
                <Button size="sm" variant="ghost" onClick={() => onAnswer('approve', o)} aria-label={`Approve ${o.title}`}>This one</Button>
              </li>
            ))}
          </ul>
        </div>
      )}
      <div className="mt-4 flex flex-wrap gap-2">
        <Button variant={active ? 'default' : 'outline'} onClick={() => onAnswer('approve', top)} aria-label={`Approve ${top.title}`}><Check aria-hidden className="size-4" /> Approve</Button>
        <Button variant="outline" onClick={() => onAnswer('reject', top)} aria-label={`Not ${top.title}`}><X aria-hidden className="size-4" /> Not this book</Button>
        <Button variant="ghost" onClick={() => onAnswer('none')} aria-label="None of these"><Ban aria-hidden className="size-4" /> None of these</Button>
      </div>
    </article>
  )
}

function Skeletons() {
  return <div className="space-y-3" aria-hidden>{[0, 1, 2].map(i => <Skeleton key={i} className="h-56 w-full" />)}</div>
}

export default function MatchQueuePage() {
  const qc = useQueryClient()
  const undoable = useUndoable()
  const [params, setParams] = useSearchParams()
  const filters = useMemo(() => readFilters(params), [params])
  const filtered = FILTER_KEYS.some(k => filters[k])
  const set = (key: keyof QueueFilters, value: string) => setParams(p => { const n = new URLSearchParams(p); if (value && !(key === 'sort' && value === 'certain')) n.set(key, value); else n.delete(key); return n }, { replace: true })
  const clear = () => setParams(p => { const n = new URLSearchParams(p); FILTER_KEYS.forEach(k => n.delete(k)); return n }, { replace: true })
  const load = useInfiniteLoad({
    queryKey: ['match-queue', filters],
    queryFn: ({ cursor }) => matchApi.queue(cursor, PAGE, filters),
    items: (page): QueueItem[] => page.items, itemKey: item => String(item.listen_id), hasMore: page => page.items.length >= PAGE,
    cursorAfter: item => item.cursor,          // "sort key:listen", so answers given meanwhile cannot skip or repeat cards
    isRemoved: undoable.isRemoved,
    staleTime: 0,
  })
  const summaryQuery = useQuery({             // the whole queue's summary and facets, and how many cards the filters leave
    queryKey: ['match-summary', filters], queryFn: () => matchApi.queue(undefined, 1, filters), staleTime: 0, placeholderData: previous => previous,
    refetchInterval: q => (q.state.data?.summary.training.running ? 3000 : false),          // while the model relearns, watch for it to finish
  })
  const summary = summaryQuery.data?.summary, facets = summaryQuery.data?.facets, total = summaryQuery.data?.total
  const items = useMemo(() => (load.status === 'ready' ? load.data : []), [load])
  const active = useActiveItem(items, i => String(i.listen_id), { noun: 'match' })

  const answer = (it: QueueItem, action: QueueAction, c?: QueueCandidate) => undoable.schedule({
    id: String(it.listen_id),
    message: action === 'approve' ? `Approved ${c?.title ?? 'the match'}.` : action === 'reject' ? `Not ${c?.title ?? 'that book'}: the next candidate comes back after the model relearns.` : 'None of these: left for a search.',
    failure: `${it.label || 'The match'} is back in the list: the answer was not saved.`,
    onError: () => undefined,
    commit: async () => {
      const r = await matchApi.decide(it.listen_id, action, c?.part_id)
      await qc.invalidateQueries({ queryKey: ['match-summary'] })
      if (r.retraining) void qc.invalidateQueries({ queryKey: ['match-summary'] })
    },
  })
  const top = active.activeItem?.candidates[0]
  const shortcuts = [
    { keys: ['a'], label: 'Approve', run: () => active.activeItem && answer(active.activeItem, 'approve', top), disabled: !active.activeItem },
    { keys: ['r'], label: 'Not this book', run: () => active.activeItem && answer(active.activeItem, 'reject', top), disabled: !active.activeItem },
    { keys: ['n'], label: 'None of these', run: () => active.activeItem && answer(active.activeItem, 'none'), disabled: !active.activeItem },
    ...active.shortcuts,
    { keys: ['u'], label: 'Undo', run: undoable.undo, disabled: !undoable.pending },
  ]
  useHotkeys(shortcuts)

  const retrain = useAsyncAction(async () => {
    await undoable.flush()
    await matchApi.retrain()
    await qc.invalidateQueries({ queryKey: ['match-summary'] })
  }, { settleMs: 3000 })
  const training = summary?.training
  const running = training?.running ?? false, wasRunning = useRef(false)
  useEffect(() => {                  // relearned: the order and the candidates below have changed
    if (running) wasRunning.current = true
    else if (wasRunning.current) { wasRunning.current = false; void qc.invalidateQueries({ queryKey: ['match-queue'] }) }
  }, [running, qc])
  const retrainButton = (variant: 'outline' | 'default') => <AsyncButton variant={variant} phase={training?.running ? 'pending' : retrain.phase} error={retrain.error} onClick={() => void retrain.run()} labels={{ idle: 'Learn from my answers', pending: 'Learning…', done: 'Learned', error: 'Learning failed' }} />

  const pending = undoable.pending ? 1 : 0
  const waiting = summary ? Math.max(0, summary.waiting - pending) : 0
  const shown = Math.max(0, (total ?? 0) - pending)
  const order = SORTS[filters.sort]
  const finding = summary ? { text: waiting === 0 ? 'Every proposed match has an answer.'
    : filtered ? `${format.integer.format(shown)} of ${format.count(waiting, 'match', 'matches')} fit the filters, ${order.finding}. ${format.count(summary.confirmed + pending, 'listen')} confirmed so far.`
    : `${format.count(waiting, 'match', 'matches')} to check, ${order.finding}. ${format.count(summary.confirmed + pending, 'listen')} confirmed so far.` } : undefined

  return (
    <div className="space-y-5">
      <PageHeader title="Matches" scope={`Is this the right book? ${order.scope}`} finding={finding} actions={retrainButton('outline')} />
      {summary && (
        <MetricStrip label="Where identification stands">
          <Metric label="Waiting for you" value={format.integer.format(waiting)} note={`${format.integer.format(summary.auto_unconfirmed)} already accepted by the model`} />
          <Metric label="Confirmed by you" value={format.integer.format(summary.confirmed)} note={summary.since_train != null ? `${summary.since_train} answers since the model last learned (it relearns every ${summary.auto_retrain})` : `The model relearns every ${summary.auto_retrain} answers`} />
          <Metric label="Not found" value={format.integer.format(summary.not_found)} note="None of the candidates was right" />
        </MetricStrip>
      )}
      {training?.error && <p role="alert" className="type-body-sm text-danger">Learning failed: {training.error}</p>}
      <QueueControls filters={filters} facets={facets} set={set} clear={filtered ? clear : null} />
      <InfiniteList state={load} label="Matches to check" skeleton={<Skeletons />} moreSkeleton={<Skeletons />} moreLabel="Show more matches" estimateSize={320} height={Math.max(480, Math.round(window.innerHeight * 0.75))} keepAtLeast={4}
        empty={filtered && waiting > 0
          ? <EmptyState title="No matches fit these filters" action={<Button onClick={clear}>Clear filters</Button>}>{`${format.count(waiting, 'match', 'matches')} ${waiting === 1 ? 'is' : 'are'} waiting outside them.`}</EmptyState>
          : summary?.model == null
          ? <EmptyState title="Nothing identified yet">Run <code>./app listens-identify-prepare</code>, <code>listens-identify-train</code> and <code>listens-identify</code> and the proposed matches appear here.</EmptyState>
          : <Done title="Every proposed match has an answer" next="Let the model learn from your answers: it will re-decide the rest." actions={retrainButton('default')} />}>
        {it => <Card it={it} active={String(it.listen_id) === active.activeKey} itemProps={active.itemProps(String(it.listen_id))} onAnswer={(action, c) => answer(it, action, c)} />}
      </InfiniteList>
      <KeysLegend shortcuts={shortcuts} />
      <UndoBar undoable={undoable} />
    </div>
  )
}
