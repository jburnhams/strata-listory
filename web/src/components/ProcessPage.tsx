import { useEffect, useState } from 'react'
import { api, type PlanState, type QueueRow } from '../api'
import { useLoad } from '../route'
import { Card, Err, btn, btnQuiet } from './ui'
import { formatBytes } from './BookPage'

const TABS: { id: string; label: string; states: PlanState[] }[] = [
  { id: 'proposed', label: 'To review', states: ['proposed'] },
  { id: 'queued', label: 'Approved', states: ['approved', 'running'] },
  { id: 'done', label: 'Done', states: ['done'] },
  { id: 'rejected', label: 'Rejected', states: ['rejected'] },
  { id: 'failed', label: 'Failed', states: ['failed', 'planning'] },
]

function Row({ r, selected, onToggle, result }: { r: QueueRow; selected: boolean; onToggle: () => void; result?: { out_bytes?: number; in_bytes?: number; loudness_after?: number; true_peak_after?: number; seconds?: number } | null }) {
  const s = r.summary
  const tiers = s && Object.entries(s.tiers).map(([k, v]) => `${v}× ${k}`).join(' · ')
  return (
    <Card className="text-sm">
      <div className="flex flex-wrap items-baseline gap-2">
        {r.state === 'proposed' || r.state === 'rejected' ? <input type="checkbox" aria-label={`Select ${r.title}`} checked={selected} onChange={onToggle} /> : null}
        <a className="font-medium hover:underline" href={`#/book/${r.book_id}`}>{r.title}</a>
        <span className="text-stone-500">{r.author}</span>
        <span className="ml-auto rounded bg-stone-200 dark:bg-stone-800 px-1.5 text-xs">{r.state}</span>
      </div>
      {s && (
        <div className="mt-1 text-stone-600 dark:text-stone-300">
          {s.hours.toFixed(1)} h · {s.chapters} chapters (from {s.chapter_source}) · {tiers} · {formatBytes(s.in_bytes)} → about {formatBytes(s.est_bytes)} · loudness {s.loudness_before.toFixed(1)} → {s.loudness_after_est.toFixed(1)} LUFS
          {s.warnings.map((w, i) => <div key={i} className="text-amber-700 dark:text-amber-400">⚠ {w}</div>)}
          <details className="mt-1"><summary className="cursor-pointer text-stone-500">Chapters and output</summary>
            <div className="font-mono text-xs break-all my-1">{s.out_dir}</div>
            <ol className="space-y-0.5">{s.chapter_list.map((c, i) => <li key={i} className="flex justify-between gap-2"><span>{c.title}</span><span className="text-stone-500">{c.minutes} min · {c.mode === 'copy' ? 'kept as is' : c.tier}</span></li>)}</ol>
          </details>
        </div>
      )}
      {r.error && <div className="mt-1 text-red-600">{r.error}</div>}
      {result?.out_bytes != null && <div className="mt-1 text-stone-500">Finished: {formatBytes(result.in_bytes ?? 0)} → {formatBytes(result.out_bytes)}{result.loudness_after != null ? ` · ${result.loudness_after.toFixed(1)} LUFS` : ''}{result.true_peak_after != null ? ` · peak ${result.true_peak_after.toFixed(1)} dBTP` : ''}</div>}
    </Card>
  )
}

export default function ProcessPage({ pollMs = 3000 }: { pollMs?: number }) {
  const { data, error, reload } = useLoad(() => api.queue(), [])
  const [tab, setTab] = useState('proposed')
  const [sel, setSel] = useState<number[]>([])
  const [err, setErr] = useState<string | null>(null)
  const busy = !!data?.worker.busy || (data?.rows ?? []).some(r => r.state === 'running' || r.state === 'planning')
  useEffect(() => { if (!busy) return; const t = setInterval(reload, pollMs); return () => clearInterval(t) }, [busy])      // eslint-disable-line react-hooks/exhaustive-deps

  async function act(fn: () => Promise<unknown>) {
    setErr(null)
    try { await fn(); setSel([]); reload() } catch (x) { setErr(String((x as Error).message || x)) }
  }
  const rows = (data?.rows ?? []).filter(r => TABS.find(t => t.id === tab)!.states.includes(r.state))
  const count = (states: PlanState[]) => (data?.rows ?? []).filter(r => states.includes(r.state)).length
  const approved = count(['approved'])
  const toggle = (id: number) => setSel(s => s.includes(id) ? s.filter(x => x !== id) : [...s, id])

  return (
    <div>
      <Card className="mb-4">
        <h2 className="text-base font-medium mb-1">Processing</h2>
        <p className="text-sm text-stone-500 mb-2">Nothing is processed until you approve it here. Proposals are made for books you have confirmed; each shows the plan, the estimated size and any warnings.</p>
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" className={btn} onClick={() => act(() => api.propose(5))}>Find candidates (5 more)</button>
          <button type="button" className={btn} disabled={approved === 0} onClick={() => act(() => api.runApproved())}>Process approved ({approved})</button>
          {busy && <span className="text-sm text-stone-500">Working… {data?.worker.busy ?? ''}</span>}
        </div>
        <Err msg={err || error} />
      </Card>

      <div className="flex gap-1 mb-3 text-sm" role="tablist">
        {TABS.map(t => <button key={t.id} type="button" role="tab" aria-selected={tab === t.id} className={`${btnQuiet} ${tab === t.id ? 'border-blue-500' : ''}`} onClick={() => { setTab(t.id); setSel([]) }}>{t.label} ({count(t.states)})</button>)}
      </div>

      {(tab === 'proposed' || tab === 'rejected') && rows.length > 0 && (
        <div className="flex flex-wrap gap-2 mb-3 text-sm">
          <button type="button" className={btnQuiet} onClick={() => setSel(rows.map(r => r.book_id))}>Select all</button>
          <button type="button" className={btn} disabled={sel.length === 0} onClick={() => act(() => api.approve(sel))}>Approve selected ({sel.length})</button>
          {tab === 'proposed' && <button type="button" className={btnQuiet} disabled={sel.length === 0} onClick={() => act(() => api.reject(sel))}>Reject selected</button>}
          <button type="button" className={btnQuiet} disabled={sel.length === 0} onClick={() => act(() => api.repropose(sel))}>Plan again</button>
        </div>
      )}
      {tab === 'failed' && rows.length > 0 && <div className="mb-3"><button type="button" className={btnQuiet} onClick={() => act(() => api.repropose(rows.map(r => r.book_id)))}>Plan these again</button></div>}

      <div className="space-y-2">
        {rows.length === 0 && <p className="text-sm text-stone-500">{tab === 'proposed' ? 'Nothing to review. Use “Find candidates”.' : 'Nothing here.'}</p>}
        {rows.map(r => <Row key={r.book_id} r={r} selected={sel.includes(r.book_id)} onToggle={() => toggle(r.book_id)} result={data?.result[String(r.book_id)]} />)}
      </div>
    </div>
  )
}
