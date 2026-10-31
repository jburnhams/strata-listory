import { useEffect, useState } from 'react'
import { ingestApi, type IngestPolicy } from '../api'
import { useLoad } from '../route'
import { Card, Err, btn, btnQuiet, input } from './ui'

export default function IngestPage() {
  const { data, error, reload } = useLoad(ingestApi.dashboard, [])
  const [ids, setIds] = useState(''), [online, setOnline] = useState(true), [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null), [policy, setPolicy] = useState(''), [message, setMessage] = useState('')
  const [details, setDetails] = useState<object | null>(null)
  useEffect(() => { if (data) setPolicy(JSON.stringify(data.policy, null, 2)) }, [data?.policy.revision])
  useEffect(() => {
    if (!data?.worker.busy && !data?.worker.queued && !data?.runs.some(r => r.state === 'running')) return
    const timer = window.setInterval(reload, 3000)
    return () => window.clearInterval(timer)
  }, [data?.worker.busy, data?.worker.queued, data?.runs.some(r => r.state === 'running')])
  async function act(fn: () => Promise<unknown>, success: string) {
    setBusy(true); setErr(null); setMessage('')
    try { await fn(); setMessage(success); reload() } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }
  function start() {
    const selected = ids.trim() ? ids.trim().split(/[ ,]+/).map(Number) : null
    if (selected?.some(n => !Number.isInteger(n) || n < 1)) { setErr('Enter positive book IDs separated by commas or spaces.'); return }
    return act(() => ingestApi.start(selected, online), 'Ingest queued')
  }
  function save() {
    try { const value = JSON.parse(policy) as IngestPolicy; return act(() => ingestApi.savePolicy(value), 'Thresholds saved as a new revision') }
    catch { setErr('Thresholds must be valid JSON.') }
  }
  return <div className="space-y-4">
    <h2 className="text-lg font-semibold">Ingest</h2>
    <p className="text-sm text-stone-500">Compare evidence, recordings and source quality before processing. Missing measurements require review. Scores describe measurements, not probabilities.</p>
    <Card><div className="flex flex-wrap gap-3 items-center">
      <label>Book IDs <input aria-label="Book IDs" className={input} value={ids} onChange={e => setIds(e.target.value)} placeholder="Blank: next five unanalysed entries" /></label>
      <label className="text-sm"><input type="checkbox" checked={online} onChange={e => setOnline(e.target.checked)} /> Query metadata providers</label>
      <button className={btn} disabled={busy} onClick={start}>Analyse</button>
      <button className={btnQuiet} onClick={reload}>Refresh</button>
    </div>{data && <p className="text-sm mt-2">Worker: {data.worker.busy || 'idle'} · {data.worker.queued} queued · Automatic approval {data.policy.auto_approve ? 'enabled' : 'disabled'} · Automatic processing {data.policy.auto_process ? 'enabled' : 'disabled'}</p>}</Card>
    <Err msg={err || error} />{message && <p role="status">{message}</p>}
    {!data && !error && <p>Loading ingest…</p>}
    {data?.runs.length === 0 && <p>No ingest runs yet.</p>}
    {data?.runs.map(run => <Card key={run.id}>
      <div className="flex flex-wrap justify-between gap-2"><h3 className="font-medium">{run.title || `Book ${run.book_id}`} · {run.state}</h3><span className="text-xs text-stone-500">{run.id.slice(0, 8)} · {run.members.length} catalogue entries</span></div>
      <p className="text-sm">Current thresholds: {run.current_evaluation.eligible ? 'pass' : 'review required'}{!run.current_evaluation.kind_allowed && ' · content kind requires review'}</p>
      {run.selection && <details className="text-sm my-2"><summary>Selected source: {run.selection.files.length} files</summary><p>{run.selection.reason}</p><ul>{run.selection.files.map(f => <li key={f}>{f}</li>)}</ul><ul>{run.selection.ranking.map(r => <li key={r.id}>{r.id}: coverage {r.reasons.coverage}, true peak {r.reasons.true_peak ?? 'unknown'} dBTP, high-band energy {r.reasons.band4k_relative_db ?? 'unknown'} dB</li>)}</ul></details>}
      <ol className="text-sm my-2 grid gap-1">{run.stages.map(s => <li key={s.stage}><strong>{s.stage}</strong>: {s.status} · {s.elapsed.toFixed(1)}s {s.error && <span className="text-red-600">{s.error}</span>}<div className="text-xs text-stone-500 flex flex-wrap gap-x-3">{Object.entries(s.metrics).map(([name, value]) => <span key={name}>{name}: {value ?? 'unknown'}</span>)}</div></li>)}</ol>
      <details><summary className="cursor-pointer text-sm">Metrics and thresholds</summary><table className="text-xs w-full"><thead><tr><th className="text-left">Metric</th><th>Value</th><th>Threshold</th><th>Result</th></tr></thead><tbody>{run.current_evaluation.checks.map(c => <tr key={c.metric}><td>{c.metric}</td><td className="text-center">{c.value ?? 'unknown'}</td><td className="text-center">{JSON.stringify(c.threshold)}</td><td className="text-center">{c.passed ? 'pass' : c.reason}</td></tr>)}</tbody></table></details>
      <div className="flex flex-wrap gap-2 mt-3">
        <button className={btnQuiet} disabled={busy} onClick={() => act(() => ingestApi.evaluate(run.id), 'Saved metrics evaluated against current thresholds')}>Evaluate thresholds</button>
        {(run.state === 'failed' || run.state === 'running') && <button className={btnQuiet} disabled={busy} onClick={() => act(() => ingestApi.resume(run.id), 'Resume queued')}>Resume</button>}
        {(run.state === 'review' || run.state === 'eligible') && <button className={btnQuiet} disabled={busy} onClick={() => act(() => ingestApi.enqueue(run.id), 'Plan proposed in Processing for manual approval')}>Propose processing</button>}
        <button className={btnQuiet} disabled={busy} onClick={() => act(async () => setDetails(await ingestApi.details(run.id)), 'Evidence loaded')}>View evidence</button>
      </div>
    </Card>)}
    {details && <Card><h3>Run evidence</h3><pre className="text-xs whitespace-pre-wrap max-h-96 overflow-auto">{JSON.stringify(details, null, 2)}</pre><button className={btnQuiet} onClick={() => setDetails(null)}>Close evidence</button></Card>}
    <Card><h3 className="font-medium">Threshold policy</h3><p className="text-sm my-2">Edit thresholds, then evaluate existing runs without decoding audio again. Saving creates a revision; earlier decisions retain their policy. Enabling automation affects subsequent ingest runs. Proposed plans remain subject to input checks.</p>
      <textarea aria-label="Threshold policy" className={`${input} w-full font-mono mt-2`} rows={12} value={policy} onChange={e => setPolicy(e.target.value)} />
      <button className={btn} disabled={busy || !data} onClick={save}>Save thresholds</button>
    </Card>
  </div>
}
