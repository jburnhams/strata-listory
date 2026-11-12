// The one question: "Which cover does each listen get?" The finding: how many listens are waiting for a search, how many have pictures to choose from and how many found none.
// Why it is shaped this way: the cover job (src/app/covers) searches every source for each confirmed or accepted listen and keeps what it found; this page shows that outcome (thumbnails of the distinct pictures with their sources, or the reason each source gave when there were none) and saves the preferred one for the next stage.
import { useEffect, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useSearchParams } from 'react-router'
import { AsyncButton, Button, DataView, Disclosure, EmptyState, Field, Input, PageHeader, Skeleton, Status, Tabs, format, useAsyncAction, useJob, type Tone } from 'strata-react'
import { coverApi, type CoverPicture, type CoverRow, type CoverStatus } from '../api'
import { size } from './FilesPage'

const SOURCE: Record<string, string> = { audible: 'Audible', audnexus: 'Audnexus', openlibrary: 'Open Library', apple: 'Apple Books', google: 'Google Books', folder: 'Your folder', embedded: 'In the audio', local: 'Your files' }
const name = (s: string) => SOURCE[s] ?? s
const TONE: Record<CoverStatus['status'], Tone> = { found: 'success', none: 'neutral', not_found: 'neutral', error: 'danger', skipped: 'neutral' }
const WORD: Record<CoverStatus['status'], string> = { found: 'found', none: 'nothing', not_found: 'not found', error: 'failed', skipped: 'not tried' }
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

function Picture({ p, chosen, onChoose }: { p: CoverPicture; chosen: boolean; onChoose: () => void }) {
  const from = [...new Set(p.sources.map(s => name(s.source)))].join(', ')
  return (
    <button type="button" aria-pressed={chosen} aria-label={`${chosen ? 'Chosen cover' : 'Choose cover'} ${p.width} by ${p.height}, from ${from}`} onClick={onChoose}
      className={`flex w-40 flex-col gap-1 rounded-surface border p-2 text-left transition-state hover:border-ring ${chosen ? 'border-ring bg-muted' : 'border-border'}`}>
      <img src={coverApi.image(p.image_id, 320)} alt="" loading="lazy" className="aspect-[2/3] w-full rounded-control bg-muted object-contain" />
      <span className="type-body-sm">{p.width}×{p.height}{chosen && <> · <b>chosen</b></>}</span>
      <span className="type-caption text-muted-foreground">{size(p.bytes)} · {from}</span>
    </button>
  )
}

function Card({ row, onChanged }: { row: CoverRow; onChanged: () => void }) {
  const qc = useQueryClient()
  const pick = useAsyncAction(async (id: number | null) => { await coverApi.choose(row.listen_id, id); onChanged(); void qc.invalidateQueries({ queryKey: ['covers'] }) }, { settleMs: 800 })
  const again = useAsyncAction(async () => { await coverApi.run([row.listen_id]); onChanged() }, { settleMs: 800 })
  return (
    <article aria-label={row.label} className="space-y-3 rounded-surface border border-border bg-card p-4">
      <header className="flex flex-wrap items-baseline gap-2">
        <h2 className="type-body font-medium">{row.label}</h2>
        <Status tone={row.state === 'chosen' ? 'success' : row.state === 'choose' ? 'info' : 'warning'}>{row.state === 'chosen' ? 'cover chosen' : row.state === 'choose' ? `${row.covers.length} to choose from` : 'no cover found'}</Status>
        <span className="type-caption text-muted-foreground">searched {when(row.searched)}</span>
        <span className="ml-auto flex gap-2">
          {row.chosen != null && <Button variant="ghost" onClick={() => void pick.run(null)}>Clear choice</Button>}
          <AsyncButton variant="outline" phase={again.phase} error={again.error} onClick={() => void again.run()} labels={{ idle: 'Search again', pending: 'Searching…', done: 'Started' }} />
        </span>
      </header>
      {row.covers.length > 0
        ? <div className="flex flex-wrap gap-3" role="group" aria-label={`Covers for ${row.label}`}>{row.covers.map(p => <Picture key={p.image_id} p={p} chosen={row.chosen != null && p.members.includes(row.chosen)} onChoose={() => void pick.run(p.image_id)} />)}</div>
        : <p className="type-body-sm text-muted-foreground">No source had a picture for this book. The reasons are below.</p>}
      {(pick.error || again.error) && <p role="alert" className="type-body-sm text-danger">{pick.error || again.error}</p>}
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
        <AsyncButton variant="default" phase={start.phase} error={start.error} disabled={running || (d?.waiting ?? 0) === 0} onClick={() => void start.run()} labels={{ idle: `Search for covers (${d?.waiting ?? 0})`, pending: 'Starting…', done: 'Started' }} />
        {running && d && <span role="status" className="type-body-sm text-muted-foreground">Searching {d.job.done} of {d.job.total}{d.job.current ? ` · ${d.job.current}` : ''}</span>}
        {d?.job.error && <span role="alert" className="type-body-sm text-danger">{d.job.error}</span>}
        <span className="type-caption text-muted-foreground">Looks for every confirmed or accepted book without a chosen cover that has not been searched since it last changed. From a shell: <code>./app covers</code>.</span>
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
