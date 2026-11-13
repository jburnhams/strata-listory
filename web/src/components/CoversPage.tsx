// The one question: "Which cover does each listen get?" The finding: how many listens are waiting for a search, how many have pictures to choose from and how many found none.
// Why it is shaped this way: the cover job (src/app/covers) searches every source for each confirmed or accepted listen and ranks what it found by fit (covers/fit.py: audiobook over print, the same recording, resolution, shape...). Most listens only need a yes, so a card opens on the best fit already selected with its sources and an Accept button; the other choices are one Expand away, where a click highlights an alternative and Save keeps it. Any picture can be magnified in the Lightbox (arrow keys step through the choices). The saved cover is for the next stage.
import { useEffect, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useSearchParams } from 'react-router'
import { ZoomIn } from 'lucide-react'
import { AsyncButton, Button, DataView, Disclosure, EmptyState, Field, IconButton, Input, Lightbox, PageHeader, Skeleton, Status, Tabs, format, useAsyncAction, useJob, type Tone } from 'strata-react'
import { api, coverApi, type CoverJob, type CoverPicture, type CoverRow, type CoverStatus } from '../api'
import { size } from './FilesPage'

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
      <IconButton icon={ZoomIn} label={`Magnify cover ${p.width} by ${p.height}, from ${from(p)}`} variant="ghost" size="sm" className="absolute right-1 top-1 bg-card/80" onClick={onZoom} />
    </div>
  )
}

function Option({ p, rank, highlighted, saved, onHighlight, onZoom }: { p: CoverPicture; rank: number; highlighted: boolean; saved: boolean; onHighlight: () => void; onZoom: () => void }) {
  return (
    <div className={`flex w-48 flex-col gap-1 rounded-surface border p-2 transition-state ${highlighted ? 'border-ring bg-muted' : 'border-border'}`}>
      <Thumb p={p} onZoom={onZoom} />
      <button type="button" aria-pressed={highlighted} aria-label={`${highlighted ? 'Selected cover' : 'Select cover'} ${p.width} by ${p.height}, from ${from(p)}`} onClick={onHighlight} className="space-y-0.5 rounded-control text-left transition-state hover:text-foreground">
        <span className="type-body-sm">{rank === 0 ? <b>Best fit</b> : `Fit ${rank + 1}`} · {Math.round(p.share * 100)}%{saved && <> · <b>saved</b></>}</span>
      </button>
      <Offered p={p} />
      <Why p={p} />
    </div>
  )
}

function Card({ row, onChanged }: { row: CoverRow; onChanged: () => void }) {
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)   // all the choices listed, or only the one that stands
  const [highlight, setHighlight] = useState<number | null>(null)   // an alternative clicked, not yet saved
  const [zoom, setZoom] = useState<number | null>(null)
  const pick = useAsyncAction(async ({ id, via }: { id: number | null; via: 'pick' | 'accept' }) => { await coverApi.choose(row.listen_id, id, via); setHighlight(null); setOpen(false); onChanged(); void qc.invalidateQueries({ queryKey: ['covers'] }) }, { settleMs: 800 })
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
          <AsyncButton variant="outline" phase={again.phase} error={again.error} onClick={() => void again.run()} labels={{ idle: 'Search again', pending: 'Queuing…', done: 'Queued' }} />
        </span>
      </header>
      {shown && !open && (
        <div className="flex flex-wrap gap-4" role="group" aria-label={`${saved ? 'Saved' : 'Best fit'} cover for ${row.label}`}>
          <Thumb p={shown} big onZoom={() => setZoom(row.covers.indexOf(shown))} />
          <div className="min-w-0 flex-1 space-y-2">
            <p className="type-body-sm">{saved ? <b>Saved cover</b> : <b>Best fit</b>} · {shown.width}×{shown.height} · {size(shown.bytes)}</p>
            <Offered p={shown} />
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
          <div className="flex flex-wrap gap-3">{row.covers.map((p, i) => <Option key={p.image_id} p={p} rank={i} highlighted={selected === p} saved={saved === p} onHighlight={() => setHighlight(p.image_id)} onZoom={() => setZoom(i)} />)}</div>
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="outline" disabled={selected === saved || pick.phase === 'pending'} onClick={() => void pick.run({ id: selected.image_id, via: selected === best ? 'accept' : 'pick' })}>Save</Button>
            <Button variant="ghost" aria-expanded onClick={() => { setOpen(false); setHighlight(null) }}>Collapse</Button>
          </div>
        </div>
      )}
      {!shown && <p className="type-body-sm text-muted-foreground">No source had a picture for this {row.kind === 'book' ? 'book' : 'programme'}. The reasons are below.</p>}
      {(pick.error || again.error) && <p role="alert" className="type-body-sm text-danger">{pick.error || again.error}</p>}
      {row.covers.length > 0 && <Lightbox title={`Covers for ${row.label}`} items={items} index={Math.max(0, zoom ?? 0)} onIndexChange={setZoom} open={zoom != null} onOpenChange={o => { if (!o) setZoom(null) }} missing="This picture could not be loaded."
        details={it => { const p = row.covers.find(c => String(c.image_id) === it.id); return p ? <div className="space-y-2"><Offered p={p} /><Why p={p} /></div> : null }} />}
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
  const d = load.status === 'ready' ? load.data : null
  const running = !!d?.job.running
  const finding = d ? `${format.count(d.waiting, 'listen')} waiting for a search · ${d.counts.choose} to choose · ${d.counts.chosen} chosen · ${d.counts.none} with none found` : undefined
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
      <Tabs param="state" label="Cover state" items={[{ value: '', label: `To choose${d ? ` (${d.counts.choose})` : ''}` }, { value: 'chosen', label: `Chosen${d ? ` (${d.counts.chosen})` : ''}` }, { value: 'none', label: `None found${d ? ` (${d.counts.none})` : ''}` }, { value: 'all', label: 'All searched' }]}>
        {() => (
          <DataView state={load} isEmpty={x => x.rows.length === 0} skeleton={<div className="space-y-3">{[0, 1].map(i => <Skeleton key={i} className="h-48 w-full" />)}</div>}
            empty={<EmptyState title="Nothing here yet">{(d?.waiting ?? 0) > 0 ? 'Press Search for covers to look for the pictures.' : 'No search has run on a listen like this.'}</EmptyState>}>
            {x => <div className="space-y-4">{x.rows.map(r => <Card key={r.listen_id} row={r} onChanged={reload} />)}{x.total > x.rows.length && <p className="type-caption text-muted-foreground">Showing {x.rows.length} of {x.total}; search to narrow it.</p>}</div>}
          </DataView>
        )}
      </Tabs>
    </div>
  )
}
