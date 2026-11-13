// The one question: "Which cover does each listen get?" The finding: how many listens are waiting for a search, how many have pictures to choose from and how many found none.
// Why it is shaped this way: the cover job (src/app/covers) searches every source for each confirmed or accepted listen and ranks what it found by fit (covers/fit.py: audiobook over print, the same recording, resolution, shape...). Most listens only need a yes, so a card opens on the best fit already selected with its sources and an Accept button; the other choices are one Expand away, where a click highlights an alternative and Save keeps it. Any picture can be magnified in the Lightbox (arrow keys step through the choices). The saved cover is for the next stage.
import { useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useSearchParams } from 'react-router'
import { ZoomIn } from 'lucide-react'
import { AsyncButton, Button, DataView, Disclosure, EmptyState, Field, IconButton, Input, Lightbox, PageHeader, Skeleton, Status, Tabs, format, useAsyncAction, useJob, type Tone } from 'strata-react'
import { api, coverApi, type CoverJob, type CoverMatch, type CoverRecording, type CoverFiles, type CoverPicture, type CoverRow, type CoverStatus } from '../api'
import { size } from './FilesPage'
import { hours } from './ListensPage'

const SOURCE: Record<string, string> = { audible: 'Audible', audnexus: 'Audnexus', openlibrary: 'Open Library', apple: 'Apple Books', google: 'Google Books', folder: 'Your folder', embedded: 'In the audio', local: 'Your files', bbc: 'BBC', podcast: 'Apple Podcasts', wikidata: 'Wikimedia' }
const name = (s: string) => SOURCE[s] ?? s
function FailedSearch({ f }: { f: CoverJob['failed'][number] }) {
  const qc = useQueryClient()
  const retry = useAsyncAction(async () => { await api.retryJob(f.job_id); await qc.invalidateQueries({ queryKey: ['covers'] }) })
  return (
    <div role="alert" className="flex items-center gap-2 type-body-sm text-danger">
      <span>Search for {f.label} failed: {f.error || 'no reason given'}</span>
      <AsyncButton variant="outline" size="sm" phase={retry.phase} error={retry.error} onClick={() => void retry.run()} labels={{ idle: `Retry ${f.label}`, pending: 'Queuing…', done: 'Queued' }} />
    </div>
  )
}

const TONE: Record<CoverStatus['status'], Tone> = { found: 'success', none: 'neutral', not_found: 'neutral', error: 'danger', limited: 'warning', skipped: 'neutral' }
const WORD: Record<CoverStatus['status'], string> = { found: 'found', none: 'nothing', not_found: 'not found', error: 'failed', limited: 'rate limited', skipped: 'not tried' }
const when = (iso: string | null) => iso ? new Date(iso + (iso.endsWith('Z') ? '' : 'Z')).toLocaleString() : ''

function Sources({ statuses }: { statuses: CoverStatus[] }) {
  return (
    <Disclosure summary="What each source said">
      <ul className="space-y-1 type-body-sm">
        {statuses.map(s => <li key={s.source} className="flex flex-wrap items-baseline gap-2"><Status tone={TONE[s.status]}>{WORD[s.status]}</Status><b>{name(s.source)}</b><span className="text-muted-foreground">{s.detail}</span></li>)}
      </ul>
    </Disclosure>
  )
}

/** What offered a picture, one line per source and reference: "Audible · B0ABC · 2400×2400 · 1.5 MB". */
function Offered({ p }: { p: CoverPicture }) {
  const seen = new Set<string>()
  const lines = p.sources.filter(x => { const k = `${x.source}:${x.ref}`; if (seen.has(k)) return false; seen.add(k); return true })
  return (
    <ul className="space-y-0.5 type-caption text-muted-foreground" aria-label="Sources of this picture">
      {lines.map(x => <li key={`${x.source}:${x.ref}`}><b className="font-medium text-foreground">{name(x.source)}</b>{x.ref && ` · ${x.ref}`}{x.label && x.label !== x.ref ? ` · ${x.label}` : ''} · {x.width}×{x.height} · {size(x.bytes)}</li>)}
    </ul>
  )
}

/** Why a picture ranks where it does: each signal with its weight (a learned one says what it was before). */
function Why({ p }: { p: CoverPicture }) {
  return (
    <Disclosure summary={`Why this fit (score ${p.score.toFixed(1)})`}>
      <ul className="space-y-0.5 type-caption">
        {p.signals.map(g => <li key={g.name}><b className="font-medium">{g.weight > 0 ? '+' : ''}{g.weight.toFixed(1)}</b> {g.name}{g.detail && <span className="text-muted-foreground"> · {g.detail}</span>}{g.learned && g.prior != null && <span className="text-muted-foreground"> · learned from your choices (was {g.prior > 0 ? '+' : ''}{g.prior.toFixed(1)})</span>}</li>)}
      </ul>
    </Disclosure>
  )
}

const from = (p: CoverPicture) => [...new Set(p.sources.map(s => name(s.source)))].join(', ')

/** One picture to look at, with a button that magnifies it; `children` is the tile's own control. */
function Thumb({ p, big, onZoom }: { p: CoverPicture; big?: boolean; onZoom: () => void }) {
  return (
    <div className={`relative ${big ? 'w-48' : 'w-full'} shrink-0`}>
      <img src={coverApi.image(p.image_id, big ? 480 : 320)} alt="" loading="lazy" className="aspect-[2/3] w-full rounded-control bg-muted object-contain" />
      <IconButton icon={ZoomIn} label={`Magnify cover ${p.width} by ${p.height}, from ${from(p)}`} variant="ghost" size="sm" className="absolute right-1 top-1 bg-card/80" onClick={e => { e.stopPropagation(); onZoom() }} />
    </div>
  )
}

/** What a picture says about the recording it belongs to (an Audible or Audnexus picture is of one recording: its reader and length), against what the files say. Other sources know only the book. */
function PictureMeta({ p, row }: { p: CoverPicture; row: CoverRow }) {
  // pictures that look alike are merged into one, so it can stand for several recordings: show each
  const seen = new Set<string>()
  const recs: CoverRecording[] = []
  for (const s of p.sources) {
    const r = ['audible', 'audnexus'].includes(s.source) ? row.match?.all_recordings.find(x => x.asin === s.ref) : undefined
    if (r && !seen.has(r.asin)) { seen.add(r.asin); recs.push(r) }
  }
  if (!recs.length) return <p className="type-caption text-muted-foreground">{from(p)}: no reader or length, only the book</p>
  return (
    <ul aria-label="Recordings of this picture" className="space-y-0.5 type-caption">
      {recs.map(rec => {
        const reader = agree(row.source.narrator, rec.narrator), len = lengthDelta(row.source.minutes, rec.minutes)
        return (
          <li key={rec.asin}>
            <span className="text-muted-foreground">Recording {rec.asin}{rec.format && rec.format !== 'unabridged' ? ` · ${rec.format.replace('_', ' ')}` : ''}{rec.year ? ` · ${rec.year}` : ''}: </span>
            read by {rec.narrator || 'unknown'}{reader && <> <Status tone={MARK[reader][0]}>{MARK[reader][1]}</Status></>} · {rec.minutes ? hours(rec.minutes) : 'length unknown'}{len.tone && <> <Status tone={MARK[len.tone][0]}>{len.text || MARK[len.tone][1]}</Status></>}
          </li>
        )
      })}
    </ul>
  )
}

function Option({ p, row, rank, highlighted, saved, onHighlight, onZoom }: { p: CoverPicture; row: CoverRow; rank: number; highlighted: boolean; saved: boolean; onHighlight: () => void; onZoom: () => void }) {
  return (
    <div onClick={onHighlight} className={`flex w-48 flex-col gap-1 rounded-surface border p-2 transition-state ${highlighted ? 'border-ring bg-muted ring-2 ring-ring' : 'cursor-pointer border-border hover:bg-muted/50'}`}>
      <Thumb p={p} onZoom={onZoom} />
      <button type="button" aria-pressed={highlighted} aria-label={`${highlighted ? 'Selected cover' : 'Select cover'} ${p.width} by ${p.height}, from ${from(p)}`} onClick={onHighlight} className="space-y-0.5 rounded-control text-left transition-state hover:text-foreground">
        <span className="type-body-sm">{rank === 0 ? <b>Best fit</b> : `Fit ${rank + 1}`} · {Math.round(p.share * 100)}%{saved && <> · <b>saved</b></>}</span>
      </button>
      <Offered p={p} />
      <PictureMeta p={p} row={row} />
      <Why p={p} />
    </div>
  )
}

const words = (s: string) => new Set(s.toLowerCase().normalize('NFKD').replace(/[^a-z0-9 ]+/g, ' ').split(/\s+/).filter(w => w.length > 1))
/** Agree when every word of the shorter reading is in the longer (initials and order do not matter), differ when both say something, nothing to say when one is empty. */
export function agree(a: string, b: string): 'same' | 'differs' | null {
  const x = words(a), y = words(b)
  if (!x.size || !y.size) return null
  const [small, big] = x.size <= y.size ? [x, y] : [y, x]
  return [...small].every(w => big.has(w)) ? 'same' : 'differs'
}
export function lengthDelta(a: number | null, b: number | null): { tone: 'same' | 'differs' | null; text: string } {
  if (!a || !b) return { tone: null, text: '' }
  const pct = (a - b) / b * 100
  return { tone: Math.abs(pct) <= 3 ? 'same' : 'differs', text: Math.abs(pct) < 0.5 ? 'same length' : `${pct > 0 ? '+' : '−'}${Math.abs(pct).toFixed(0)}%` }
}
const MARK = { same: ['success', 'same'], differs: ['danger', 'differs'] } as const

/** The listen's own files against the match it was accepted with, so one look says whether the best fit is the right recording (same reader, same length). */
function Compare({ source, match }: { source: CoverFiles; match: CoverMatch | null }) {
  const len = lengthDelta(source.minutes, match?.minutes ?? null)
  const rows: { label: string; mine: string; theirs: string; tone: 'same' | 'differs' | null; note?: string }[] = [
    { label: 'Title', mine: source.title, theirs: match?.title ?? '', tone: match ? agree(source.title, match.title) : null },
    { label: 'Author', mine: source.author, theirs: match?.author ?? '', tone: match ? agree(source.author, match.author) : null },
    { label: 'Read by', mine: source.narrator, theirs: match?.narrator ?? '', tone: match ? agree(source.narrator, match.narrator) : null },
    { label: 'Length', mine: source.minutes ? hours(source.minutes) : '', theirs: match?.minutes ? hours(match.minutes) : '', tone: len.tone, note: len.text },
    { label: 'Year', mine: source.year.slice(0, 4), theirs: match?.year.slice(0, 4) ?? '', tone: match ? agree(source.year.slice(0, 4), match.year.slice(0, 4)) : null },
  ]
  return (
    <table aria-label="Your files against the match" className="type-caption">
      <thead><tr className="text-left text-muted-foreground"><th className="pr-3 font-normal" /><th className="pr-3 font-normal">Your files</th><th className="pr-3 font-normal">Matched work</th><th className="font-normal" /></tr></thead>
      <tbody>
        {rows.map(r => (
          <tr key={r.label} className="align-baseline">
            <th scope="row" className="pr-3 text-left font-normal text-muted-foreground">{r.label}</th>
            <td className="pr-3">{r.mine || '–'}</td>
            <td className="pr-3">{r.theirs || (match ? '–' : '')}</td>
            <td>{r.tone && <Status tone={MARK[r.tone][0]}>{r.note || MARK[r.tone][1]}</Status>}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function Card({ row, onPick, onBack, onChanged }: { row: CoverRow; onPick: (row: CoverRow, id: number | null, via: 'pick' | 'accept') => Promise<void>; onBack: (row: CoverRow) => void; onChanged: () => void }) {
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)   // all the choices listed, or only the one that stands
  const [highlight, setHighlight] = useState<number | null>(null)   // an alternative clicked, not yet saved
  const [zoom, setZoom] = useState<number | null>(null)
  const pick = useAsyncAction(async ({ id, via }: { id: number | null; via: 'pick' | 'accept' }) => { setHighlight(null); setOpen(false); await onPick(row, id, via) }, { settleMs: 800 })   // the page decides whether the card goes at once and the write follows in the background
  const again = useAsyncAction(async () => { await coverApi.run([row.listen_id]); onChanged() }, { settleMs: 800 })
  const best = row.covers[0]   // best fit first: the server ranks them
  const saved = row.covers.find(p => row.chosen != null && p.members.includes(row.chosen))
  const shown = saved ?? best   // what stands: the saved cover, or the suggestion that Accept saves
  const selected = row.covers.find(p => p.image_id === highlight) ?? shown
  const others = row.covers.length - 1
  const items = row.covers.map(p => ({ id: String(p.image_id), src: coverApi.image(p.image_id), label: `Cover ${p.width} by ${p.height}, from ${from(p)}`, natural: [p.width, p.height] as const }))
  return (
    <article aria-label={row.label} className="space-y-3 rounded-surface border border-border bg-card p-4">
      <header className="flex flex-wrap items-baseline gap-2">
        <h2 className="type-body font-medium">{row.label}</h2>
        <Status tone={row.state === 'chosen' ? 'success' : row.state === 'choose' ? 'info' : 'warning'}>{row.state === 'chosen' ? 'cover saved' : row.state === 'choose' ? `${row.covers.length} to choose from` : 'no cover found'}</Status>
        <span className="type-caption text-muted-foreground">searched {when(row.searched)}</span>
        <span className="ml-auto flex gap-2">
          {row.chosen != null && <Button variant="ghost" onClick={() => void pick.run({ id: null, via: 'pick' })}>Clear choice</Button>}
          <Button variant="ghost" title="Take the match back: this listen returns to the Matches page to be decided again" onClick={() => onBack(row)}>Back to matches</Button>
          <AsyncButton variant="outline" phase={again.phase} error={again.error} onClick={() => void again.run()} labels={{ idle: 'Search again', pending: 'Queuing…', done: 'Queued' }} />
        </span>
      </header>
      {shown && !open && (
        <div className="flex flex-wrap gap-4" role="group" aria-label={`${saved ? 'Saved' : 'Best fit'} cover for ${row.label}`}>
          <Thumb p={shown} big onZoom={() => setZoom(row.covers.indexOf(shown))} />
          <div className="min-w-0 flex-1 space-y-2">
            <p className="type-body-sm">{saved ? <b>Saved cover</b> : <b>Best fit</b>} · {shown.width}×{shown.height} · {size(shown.bytes)}</p>
            <Offered p={shown} />
            <PictureMeta p={shown} row={row} />
            <Compare source={row.source} match={row.match} />
            <Why p={shown} />
            <div className="flex flex-wrap items-center gap-2">
              {!saved && <Button variant="outline" onClick={() => void pick.run({ id: shown.image_id, via: 'accept' })}>Accept</Button>}
              {others > 0 && <Button variant="ghost" aria-expanded={false} onClick={() => { setOpen(true); setHighlight(shown.image_id) }}>{`Expand ${format.count(others, 'other choice')}`}</Button>}
            </div>
          </div>
        </div>
      )}
      {shown && open && (
        <div className="space-y-3" role="group" aria-label={`Covers for ${row.label}`}>
          <Compare source={row.source} match={row.match} />
          <div className="flex flex-wrap gap-3">{row.covers.map((p, i) => <Option key={p.image_id} p={p} row={row} rank={i} highlighted={selected === p} saved={saved === p} onHighlight={() => setHighlight(p.image_id)} onZoom={() => setZoom(i)} />)}</div>
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="outline" disabled={selected === saved || pick.phase === 'pending'} onClick={() => void pick.run({ id: selected.image_id, via: selected === best ? 'accept' : 'pick' })}>Save</Button>
            <Button variant="ghost" aria-expanded onClick={() => { setOpen(false); setHighlight(null) }}>Collapse</Button>
          </div>
        </div>
      )}
      {!shown && <p className="type-body-sm text-muted-foreground">No source had a picture for this {row.kind === 'book' ? 'book' : 'programme'}. The reasons are below.</p>}
      {(pick.error || again.error) && <p role="alert" className="type-body-sm text-danger">{pick.error || again.error}</p>}
      {row.covers.length > 0 && <Lightbox title={`Covers for ${row.label}`} items={items} index={Math.max(0, zoom ?? 0)} onIndexChange={setZoom} open={zoom != null} onOpenChange={o => { if (!o) setZoom(null) }} missing="This picture could not be loaded."
        details={it => { const p = row.covers.find(c => String(c.image_id) === it.id); return p ? <div className="space-y-2"><Offered p={p} /><PictureMeta p={p} row={row} /><Why p={p} /></div> : null }} />}
      <Sources statuses={row.sources} />
    </article>
  )
}

export default function CoversPage({ pollMs = 2000 }: { pollMs?: number }) {
  const [params, setParams] = useSearchParams()
  const state = params.get('state') ?? 'choose', q = params.get('q') ?? ''
  const [typed, setTyped] = useState(q)
  useEffect(() => { setTyped(q) }, [q])
  useEffect(() => {
    if (typed.trim() === q.trim()) return
    const t = setTimeout(() => setParams(p => { const n = new URLSearchParams(p); if (typed.trim()) n.set('q', typed.trim()); else n.delete('q'); return n }, { replace: true }), 250)
    return () => clearTimeout(t)
  }, [typed]) // eslint-disable-line react-hooks/exhaustive-deps
  const load = useJob({ queryKey: ['covers', state, q], queryFn: () => coverApi.list(state === 'all' ? '' : state, q), staleTime: 0, every: pollMs, isRunning: x => x.job.running })   // polled while a search runs: the page fills as each listen is finished
  const reload = load.poll
  const start = useAsyncAction(async () => { await coverApi.run(); reload() }, { settleMs: 800 })
  // a choice shows at once and the write follows in the background: the page answers from what was clicked, never from a refetch of the whole list
  const [over, setOver] = useState<Record<number, { image: number | null; from: CoverRow['state'] }>>({})
  const [failed, setFailed] = useState<Record<number, string>>({})
  const pending = useRef(0)   // writes still on their way: the clicked state stands until the last one has been read back
  const qc = useQueryClient()
  const onPick = async (row: CoverRow, id: number | null, via: 'pick' | 'accept') => {
    setOver(o => ({ ...o, [row.listen_id]: { image: id, from: o[row.listen_id]?.from ?? row.state } }))
    setFailed(f => { const { [row.listen_id]: _, ...rest } = f; return rest })
    pending.current++
    coverApi.choose(row.listen_id, id, via).then(async () => {
      pending.current--
      if (pending.current > 0) return
      await qc.refetchQueries({ queryKey: ['covers'], type: 'active' })   // in the background: brings in the next listens and the server's own counts
      if (pending.current === 0) setOver({})
    }, (e: unknown) => {
      pending.current--
      setOver(o => { const { [row.listen_id]: _, ...rest } = o; return rest })
      setFailed(f => ({ ...f, [row.listen_id]: `${row.label}: ${e instanceof Error ? e.message : 'could not be saved'}` }))
    })
  }
  const [sentBack, setSentBack] = useState<Set<number>>(new Set())   // listens whose match was taken back leave the page at once
  const onBack = (row: CoverRow) => {
    setSentBack(b => new Set(b).add(row.listen_id))
    setFailed(f => { const { [row.listen_id]: _, ...rest } = f; return rest })
    coverApi.unaccept(row.listen_id).then(() => { void qc.invalidateQueries({ queryKey: ['match-summary'] }) }, (e: unknown) => {
      setSentBack(b => { const n = new Set(b); n.delete(row.listen_id); return n })
      setFailed(f => ({ ...f, [row.listen_id]: `${row.label}: ${e instanceof Error ? e.message : 'could not be sent back'}` }))
    })
  }
  const shownRow = (r: CoverRow): CoverRow => { const o = over[r.listen_id]; return o ? { ...r, chosen: o.image, state: o.image != null ? 'chosen' : r.covers.length ? 'choose' : 'none' } : r }
  const d = load.status === 'ready' ? load.data : null
  const running = !!d?.job.running
  const counts = { ...(d?.counts ?? { choose: 0, chosen: 0, none: 0 }) }
  for (const o of Object.values(over)) { const now = o.image != null ? 'chosen' : o.from === 'chosen' ? 'choose' : o.from; if (now !== o.from) { counts[o.from]--; counts[now]++ } }
  const finding = d ? `${format.count(d.waiting, 'listen')} waiting for a search · ${counts.choose} to choose · ${counts.chosen} chosen · ${counts.none} with none found` : undefined
  return (
    <div className="space-y-5">
      <PageHeader title="Covers" scope="A picture for each listen, from every source that knows the book" finding={finding ? { text: finding } : undefined} />
      <section aria-label="Cover search" className="flex flex-wrap items-center gap-3 rounded-surface border border-border bg-card p-3">
        <AsyncButton variant="default" phase={start.phase} error={start.error} disabled={running || (d?.waiting ?? 0) === 0} onClick={() => void start.run()} labels={{ idle: `Search for covers (${d?.waiting ?? 0})`, pending: 'Queuing…', done: 'Queued' }} />
        {running && d && <span role="status" className="type-body-sm text-muted-foreground">Searching{d.job.current ? ` ${d.job.current}` : ''} · {d.job.queued} more queued</span>}
        {d?.job.error && <span role="alert" className="type-body-sm text-danger">{d.job.error}</span>}
        {d?.job.failed.map(f => <FailedSearch key={f.job_id} f={f} />)}
        <span className="type-caption text-muted-foreground">Looks for every confirmed or accepted book without a chosen cover that has not been searched since it last changed. Each search is a job on the queue, run one at a time with the other jobs. From a shell: <code>./app covers --queue</code>.</span>
      </section>
      <Field label="Search covers" hint="Every word must appear in the title">
        <Input type="search" value={typed} onChange={e => setTyped(e.target.value)} placeholder="e.g. glass orchard" />
      </Field>
      <Tabs param="state" label="Cover state" items={[{ value: '', label: `To choose${d ? ` (${counts.choose})` : ''}` }, { value: 'chosen', label: `Chosen${d ? ` (${counts.chosen})` : ''}` }, { value: 'none', label: `None found${d ? ` (${counts.none})` : ''}` }, { value: 'all', label: 'All searched' }]}>
        {() => (
          <DataView state={load} isEmpty={x => x.rows.length === 0} skeleton={<div className="space-y-3">{[0, 1].map(i => <Skeleton key={i} className="h-48 w-full" />)}</div>}
            empty={<EmptyState title="Nothing here yet">{(d?.waiting ?? 0) > 0 ? 'Press Search for covers to look for the pictures.' : 'No search has run on a listen like this.'}</EmptyState>}>
            {x => <div className="space-y-4">{Object.entries(failed).map(([id, m]) => <p key={id} role="alert" className="type-body-sm text-danger">Not saved — {m}</p>)}{x.rows.map(shownRow).filter(r => !sentBack.has(r.listen_id)).filter(r => state === 'all' || r.state === (state || 'choose')).map(r => <Card key={r.listen_id} row={r} onPick={onPick} onBack={onBack} onChanged={reload} />)}{x.total > x.rows.length && <p className="type-caption text-muted-foreground">Showing {x.rows.length} of {x.total}; search to narrow it.</p>}</div>}
          </DataView>
        )}
      </Tabs>
    </div>
  )
}
