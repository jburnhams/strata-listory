import { useEffect, useState } from 'react'
import { ingestApi, withBase, type ListeningSample } from '../api'
import { btnQuiet, Err } from './ui'

const position = (seconds: number) => `${Math.floor(seconds / 3600)}:${String(Math.floor(seconds / 60) % 60).padStart(2, '0')}:${String(Math.floor(seconds) % 60).padStart(2, '0')}`

export default function QualitySamples({ runId }: { runId: string }) {
  const [samples, setSamples] = useState<ListeningSample[] | null>(null)
  const [busy, setBusy] = useState(false), [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<ListeningSample | null>(null), [attempt, setAttempt] = useState(0)
  const [retry, setRetry] = useState(0)
  useEffect(() => {
    let active = true, timer: ReturnType<typeof setTimeout>
    setBusy(true); setError(null)
    async function update(prepare = false) {
      try {
        const result = await (prepare ? ingestApi.prepareSamples(runId) : ingestApi.samples(runId))
        if (!active) return
        setSamples(result.samples); setBusy(false)
        if (result.samples.some(s => s.status === 'generating')) timer = setTimeout(() => { void update() }, 1500)
      } catch (e) { if (active) { setError((e as Error).message); setBusy(false) } }
    }
    void update(true)
    return () => { active = false; clearTimeout(timer) }
  }, [runId, retry])
  return <section aria-label="Listening samples" className="my-4 rounded border border-stone-300 dark:border-stone-700 p-3">
    <h4 className="font-semibold">Listen to the selected source</h4>
    <p className="text-sm text-stone-500">Five excerpts spread across the book, up to 30 seconds each. Playback volume is normalized. Samples prepare in the background and are cached for later visits.</p>
    <Err msg={error} />
    {busy && <p role="status">Preparing listening samples…</p>}
    {(error || samples?.some(s => s.status === 'failed')) && <button className={btnQuiet} disabled={busy} onClick={() => setRetry(n => n + 1)}>Retry listening samples</button>}
    {samples && <div className="flex flex-wrap gap-2 my-2">{samples.map(s => <button key={s.index} className={btnQuiet} disabled={s.status !== 'ready'} aria-pressed={selected?.index === s.index} onClick={() => { setSelected(s); setAttempt(n => n + 1); setError(null) }}>Sample {s.index + 1} · {position(s.start_seconds)}{s.status === 'generating' ? ' · preparing…' : s.status === 'failed' ? ' · failed' : ''}</button>)}</div>}
    {selected && <><p className="text-sm">Sample {selected.index + 1} · {position(selected.start_seconds)} · {selected.duration_seconds}s</p><audio key={attempt} aria-label={`Listening sample ${selected.index + 1}`} className="w-full mt-2" controls autoPlay preload="none" src={withBase(selected.url)} onError={() => setError('Could not load this sample. Select it again to retry.')} /></>}
  </section>
}
