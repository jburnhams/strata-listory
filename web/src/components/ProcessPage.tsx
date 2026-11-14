import SourceFiles from './SourceFiles'
import { useState } from 'react'
import { api, ingestApi, type PlanState, type QueueRow } from '../api'
import { href, readOf } from '../route'
import { Button, Checkbox, Disclosure, Input, StaleNotice, useJob } from 'strata-react'
import { Card, Err } from './ui'
import { formatBytes } from './BookPage'

const TABS: { id: string; label: string; states: PlanState[] }[] = [
  { id: 'proposed', label: 'To review', states: ['proposed'] },
  { id: 'rejected', label: 'Rejected', states: ['rejected'] },
]

// Operations are filtered by what state they are in: failures, work in progress and pending work show by default; completed work and anything else is a toggle away.
const GROUPS = [
  { id: 'failed', label: 'Failed', states: ['failed'] },
  { id: 'running', label: 'In progress', states: ['running', 'planning'] },
  { id: 'pending', label: 'Pending', states: ['queued', 'approved'] },
  { id: 'waiting', label: 'Awaiting approval', states: ['waiting'] },   // the old per-book ingest stages (book, audiobook, quality) waiting for a person: hundreds of them, and not work in the queue
  { id: 'done', label: 'Completed', states: ['done', 'completed'] },
] as const
const DEFAULT_GROUPS = ['failed', 'running', 'pending']
const inGroups = (shown: string[], state: string) => { const g = GROUPS.find(x => (x.states as readonly string[]).includes(state)); return !g || shown.includes(g.id) }  // an unknown state is never hidden

function FailureReason({ error, failed }: { error: string; failed: boolean }) {
  return error || failed ? <p className="mt-2 type-body-sm text-danger whitespace-pre-wrap break-words"><strong>{failed ? 'Failure reason: ' : ''}</strong>{error || 'No failure reason was recorded.'}</p> : null
}

function Row({ r, selected, onToggle, result }: { r: QueueRow; selected: boolean; onToggle: () => void; result?: { out_bytes?: number; in_bytes?: number; loudness_after?: number; true_peak_after?: number; seconds?: number } | null }) {
  const s = r.summary
  const tiers = s && Object.entries(s.tiers).map(([k, v]) => `${v}× ${k}`).join(' · ')
  return (
    <Card className="type-body-sm">
      <div className="flex flex-wrap items-baseline gap-2">
        {r.state === 'proposed' || r.state === 'rejected' ? <Checkbox label={`Select ${r.title}`} hideLabel checked={selected} onCheckedChange={() => onToggle()} /> : null}
        <a className="font-medium hover:underline" href={`#/book/${r.book_id}`}>{r.title}</a>
        <span className="text-muted-foreground">{r.author}</span>
        <span className="ml-auto rounded-control bg-muted px-1.5 type-caption">{r.state}</span>
      </div>
      <SourceFiles bookId={r.book_id} />
      {s && (
        <div className="mt-1 text-muted-foreground">
          {s.hours.toFixed(1)} h · {s.chapters} chapters (from {s.chapter_source}) · {tiers} · {formatBytes(s.in_bytes)} → about {formatBytes(s.est_bytes)} · loudness {s.loudness_before.toFixed(1)} → {s.loudness_after_est.toFixed(1)} LUFS
          {s.enhancement && <p>Enhancement: Diamond restoration and gradual levelling (confirmed medium quality)</p>}
          {s.book_metadata?.length && s.audiobook_metadata?.length ? <div aria-label={`Metadata for ${r.title}`} className="my-2 space-y-3">{[{ label: 'Book', rows: s.book_metadata }, { label: 'Audiobook / performance', rows: s.audiobook_metadata }].map(group => <section key={group.label} aria-label={`${group.label} metadata for ${r.title}`}><h4 className="font-semibold">{group.label}</h4><dl className="grid grid-cols-[7rem_1fr] gap-x-3 gap-y-1">{group.rows.map(row => <div key={row.field} className="contents"><dt className="text-muted-foreground">{row.field === 'Year' ? 'Original publication year' : row.field}</dt><dd>{row.value} <span className="type-caption text-muted-foreground">· {row.source}</span>{row.note && <p className="type-caption text-warning">{row.note}</p>}</dd></div>)}</dl></section>)}</div> : <>
          <dl className="my-1 grid grid-cols-[6rem_1fr] gap-x-3 gap-y-0.5" aria-label={`Metadata for ${r.title}`}>
            {s.metadata.map(m => (
              <div key={m.field} className="contents">
                <dt className="text-muted-foreground">{m.field}</dt>
                <dd>{m.value} <span className="type-caption text-muted-foreground">· {m.source}</span>{m.note && <div className="type-caption text-warning">⚠ {m.note}</div>}</dd>
              </div>
            ))}
          </dl>
          </>}

          {s.warnings.map((w, i) => <div key={i} className="text-warning">⚠ {w}</div>)}
          {s.advisories?.map((note, i) => <div key={i} className="text-muted-foreground">Note: {note}</div>)}
          <Disclosure className="mt-1" summary="Chapters and output">
            <div className="font-mono type-caption break-all my-1">{s.out_dir}/</div>
            <ol className="space-y-0.5">{s.chapter_list.map((c, i) => <li key={i} className="flex justify-between gap-2"><span>{c.title}</span><span className="text-muted-foreground">{c.minutes} min · {c.mode === 'copy' ? 'kept as is' : c.tier}</span></li>)}</ol>
          </Disclosure>
        </div>
      )}
      <FailureReason error={r.error} failed={r.state === 'failed'} />
      {result?.out_bytes != null && <div className="mt-1 text-muted-foreground">Finished: {formatBytes(result.in_bytes ?? 0)} → {formatBytes(result.out_bytes)}{result.loudness_after != null ? ` · ${result.loudness_after.toFixed(1)} LUFS` : ''}{result.true_peak_after != null ? ` · peak ${result.true_peak_after.toFixed(1)} dBTP` : ''}</div>}
    </Card>
  )
}

export default function ProcessPage({ pollMs = 3000, mode = 'operations' }: { pollMs?: number; mode?: 'operations' | 'proposed' }) {
  const [shown, setShown] = useState<string[]>(DEFAULT_GROUPS)
  const showCompleted = shown.includes('done')
  const operationsJob = useJob({ queryKey: ['operations', mode, showCompleted], queryFn: () => mode === 'operations' ? api.operations(showCompleted) : Promise.resolve({ rows: [], busy: false }), staleTime: 0, every: pollMs, isRunning: o => o.busy })
  // Both reads are polled while either worker (processing or ingest) has something running or queued, and left alone otherwise: an ingest job ends by creating the plan the queue shows.
  const queueJob = useJob({ queryKey: ['queue'], queryFn: () => api.queue(), staleTime: 0, every: pollMs, isRunning: q => operationsJob.running || !!q.worker.busy || !!q.worker.queued || q.rows.some(r => r.state === 'running' || r.state === 'planning') })
  const { data, error } = readOf(queueJob)
  const [query, setQuery] = useState('')
  const matches = (...values: (string | number | null | undefined)[]) => query.toLocaleLowerCase().trim().split(/\s+/).every(term => values.join(' ').toLocaleLowerCase().includes(term))
  const { data: operations, error: operationsError } = readOf(operationsJob)
  const reload = queueJob.poll, reloadOperations = operationsJob.poll
  const [tab, setTab] = useState('proposed')
  const [sel, setSel] = useState<number[]>([])
  const [err, setErr] = useState<string | null>(null)
  const busy = !!data?.worker.busy || !!data?.worker.queued || !!operations?.busy || (data?.rows ?? []).some(r => r.state === 'running' || r.state === 'planning')

  async function act(fn: () => Promise<unknown>) {
    setErr(null)
    try { await fn(); setSel([]); reload(); reloadOperations() } catch (x) { setErr(String((x as Error).message || x)) }
  }
  const states = TABS.find(t => t.id === tab)!.states
  const allowed = (r: QueueRow) => mode === 'proposed' ? ['proposed','rejected'].includes(r.state) : !['proposed','rejected'].includes(r.state)
  const rows = (data?.rows ?? []).filter(r => matches(r.title, r.author, r.book_id, r.state, r.error) && allowed(r) && (mode === 'operations' ? inGroups(shown, r.state) : states.includes(r.state)))
  const tasks = (operations?.rows ?? []).filter(r => matches(r.title, r.book_id, r.stage, r.current_step, r.state, r.error, ...(r.logs ?? []).map(entry => entry.message))).filter(r => inGroups(shown, r.state))
  const selectable = rows.filter(r => r.state === 'proposed' || r.state === 'rejected')
  const count = (states: PlanState[]) => (data?.rows ?? []).filter(r => allowed(r) && states.includes(r.state)).length
  const groupCount = (id: string) => { const states = GROUPS.find(g => g.id === id)!.states as readonly string[]; return (data?.rows ?? []).filter(r => allowed(r) && states.includes(r.state)).length + (operations?.rows ?? []).filter(r => states.includes(r.state)).length }
  const failedOnly = mode === 'operations' && shown.length === 1 && shown[0] === 'failed'
  const approved = count(['approved'])
  const toggle = (id: number) => setSel(s => s.includes(id) ? s.filter(x => x !== id) : [...s, id])

  return (
    <div>
      <Card className="mb-4">
        {mode === 'proposed' ? <p className="type-body-sm text-muted-foreground mb-2">After quality approval, review the final processing plan here: source files, chapters, audio changes and output. Approve a plan to make it ready for Processing.</p> : <p className="type-body-sm text-muted-foreground mb-2">All queued, running and failed work, from ingest analysis to final output. Final plans awaiting approval are on <a className="underline" href={href.proposed()}>Plans to approve ({(data?.rows ?? []).filter(r => r.state === 'proposed').length})</a>.</p>}
        <div className="flex flex-wrap items-center gap-2">
          <a className="rounded-control border border-border px-2 py-0.5 type-caption hover:border-ring" href={href.library()}>Choose books in Library</a>
          {mode === 'operations' && <Button disabled={approved === 0} onClick={() => act(() => api.runApproved())}>Process approved ({approved})</Button>}
          {mode === 'proposed' && <a className="rounded-control border border-border px-2 py-0.5 type-caption hover:border-ring" href={href.process()}>Open processing queue</a>}
          <Button variant="outline" size="sm" onClick={() => { reload(); reloadOperations() }}>Refresh</Button>
          {busy && <span className="type-body-sm text-muted-foreground">Working… {data?.worker.busy ?? ''}</span>}
        </div>
        <Err msg={err || error || operationsError} />
        {queueJob.status === 'ready' && queueJob.stale && <StaleNotice stale={queueJob.stale} />}
      </Card>

      {mode === 'operations' && <div className="flex flex-wrap items-center gap-1 mb-3 type-body-sm" role="group" aria-label="Show operations that are">
        {GROUPS.map(g => <button key={g.id} type="button" aria-pressed={shown.includes(g.id)} className={`rounded-control border px-2 py-0.5 type-caption transition-state hover:border-ring ${shown.includes(g.id) ? 'border-ring bg-muted' : 'border-border text-muted-foreground'}`} onClick={() => { setShown(cur => cur.includes(g.id) ? cur.filter(x => x !== g.id) : [...cur, g.id]); setSel([]) }}>{g.label} ({groupCount(g.id)})</button>)}
        <button type="button" className="ml-1 type-caption underline" onClick={() => setShown(GROUPS.map(g => g.id))}>Show all</button>
        <button type="button" className="type-caption underline" onClick={() => setShown(DEFAULT_GROUPS)}>Reset</button>
      </div>}
      {mode === 'proposed' && <div className="flex flex-wrap gap-1 mb-3 type-body-sm" role="tablist" aria-label="Operation status">
        {TABS.map(t => <button key={t.id} type="button" role="tab" aria-selected={tab === t.id} className={`rounded-control border px-2 py-0.5 type-caption transition-state hover:border-ring ${tab === t.id ? 'border-ring bg-muted' : 'border-border'}`} onClick={() => { setTab(t.id); setSel([]) }}>{t.label} ({count(t.states)})</button>)}
      </div>}

      {mode === 'operations' && <div className="mb-3 space-y-2">
        <Input type="search" aria-label="Search operations" className="w-full max-w-lg" placeholder="Search title, book ID, stage or failure reason" value={query} onChange={e => setQuery(e.target.value)} />
        {failedOnly && <p className="type-body-sm text-muted-foreground">Failed work stays stopped until you choose to retry. Each entry shows its last step and recorded failure reason.</p>}
      </div>}

      {selectable.length > 0 && (
        <div className="flex flex-wrap gap-2 mb-3 type-body-sm">
          <Button variant="outline" size="sm" onClick={() => setSel(selectable.map(r => r.book_id))}>Select all</Button>
          <Button disabled={sel.length === 0} onClick={() => act(() => api.approve(sel))}>Approve selected ({sel.length})</Button>
          {tab !== 'rejected' && <Button variant="outline" size="sm" disabled={sel.length === 0} onClick={() => act(() => api.reject(sel))}>Reject selected</Button>}
          <Button variant="outline" size="sm" disabled={sel.length === 0} onClick={() => act(() => api.repropose(sel))}>Plan again</Button>
        </div>
      )}
      {failedOnly && rows.length > 0 && <div className="mb-3"><Button variant="outline" size="sm" onClick={() => act(() => api.repropose(rows.map(r => r.book_id)))}>Plan these again</Button></div>}

      <div className="space-y-2">
        {rows.length === 0 && tasks.length === 0 && <p className="type-body-sm text-muted-foreground">No operations in this view.</p>}
        {tasks.map(r => <Card key={r.id}><div className="flex flex-wrap gap-2 items-baseline"><strong>{r.title}</strong><span className="ml-auto type-body-sm">{r.state}</span></div>{r.book_id != null && <SourceFiles bookId={r.book_id} />}{r.progress && <p className="type-body-sm break-all">{r.state === 'failed' ? 'Stopped at: ' : ''}{r.progress.total ? `${(r.progress.done ?? 0).toLocaleString()} of ${r.progress.total.toLocaleString()} done, ${(r.progress.total - (r.progress.done ?? 0)).toLocaleString()} remaining` : null}{r.progress.total ? ' · ' : ''}<span className="font-mono">{r.progress.current}</span></p>}<p className="type-body-sm break-all">{r.state === 'failed' ? 'Last step' : 'Current step'}: {r.current_step || r.stage.replaceAll('_',' ')}{r.step_elapsed != null && ` · ${Math.floor(r.step_elapsed / 60)}m ${Math.floor(r.step_elapsed % 60)}s`}{r.run_id && ` · ${r.run_id.slice(0,8)}`}</p>{!!r.logs?.length && <Disclosure className="mt-2 type-caption" summary="Activity log"><ol className="mt-2 space-y-1 font-mono break-all">{r.logs.map((entry, index) => <li key={index}><time dateTime={entry.created}>{new Date(entry.created).toLocaleTimeString()}</time> · {entry.message}</li>)}</ol></Disclosure>}<FailureReason error={r.error || r.logs?.slice().reverse().find(entry => entry.status === 'failed')?.message || ''} failed={r.state === 'failed'} /><div className="flex flex-wrap gap-3 type-body-sm mt-2">{r.phase === 'metadata' && <a className="underline" href={href.metadata()}>Review book identification</a>}{r.phase === 'audiobook' && <a className="underline" href={href.audiobook()}>Review audiobook identification</a>}{r.phase === 'quality' && <a className="underline" href={href.quality()}>Review quality</a>}{r.book_id != null && <a className="underline" href={href.book(r.book_id)}>Open book</a>}{r.state === 'failed' && r.job_id && <Button variant="outline" size="sm" onClick={() => act(() => api.retryJob(r.job_id!))}>Retry job</Button>}{r.state === 'failed' && r.job_id && <Button variant="ghost" size="sm" aria-label={`Clear ${r.title}`} onClick={() => act(() => api.dismissJob(r.job_id!))}>Clear</Button>}{r.state === 'failed' && r.run_id && <Button variant="outline" size="sm" onClick={() => act(() => ingestApi.resume(r.run_id!))}>Retry analysis</Button>}</div></Card>)}
        {rows.map(r => <Row key={r.book_id} r={r} selected={sel.includes(r.book_id)} onToggle={() => toggle(r.book_id)} result={data?.result[String(r.book_id)]} />)}
      </div>
    </div>
  )
}
