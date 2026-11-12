import SourceFiles from './SourceFiles'
import QualitySamples from './QualitySamples'
import { useEffect, useState } from 'react'
import { ingestApi, type IngestPolicy, type StageReview, type MetaRow, type MetadataEvidence, type ReviewSort, type WorkEvidence } from '../api'
import { href, readOf } from '../route'
import { Button, Checkbox, Disclosure, Input, NativeSelect, StaleNotice, Textarea, useJob } from 'strata-react'
import { Card, Err } from './ui'


const metricLabels: Record<string, string> = {
  'review.manual_hold': 'Approval withdrawn — manual review required',
  'metadata.original_work_link': 'Original book identified', 'metadata.work_families': 'Metadata source families', 'metadata.critical_conflicts': 'Conflicting metadata',
  'recording.score': 'Title and author similarity', 'recording.runtime_error': 'Recording runtime comparison', 'recording.narrator_agrees': 'Narrator corroboration', 'recording.language_agrees': 'Language corroboration', 'recording.abridgement_known': 'Abridgement verified', 'recording.ambiguous': 'Recording ambiguity',
  'identity.conflicts': 'Conflicting source tags', 'inventory.probe_errors': 'Unreadable source files', 'alignment.coverage': 'Recording coverage', 'alignment.missing_tracks': 'Missing or duplicate tracks', 'alignment.unresolved_representations': 'Unresolved source alternatives',
  'work.score': 'Book title and author similarity', 'work.conflicts': 'Conflicting book metadata', 'recording.conflicts': 'Conflicting performance metadata', 'recording.matched': 'Recording matches the source performance',
  'quality.complete': 'Audio measurements complete', 'quality.decode_errors': 'Audio decoding errors', 'quality.true_peak': 'True peak level', 'quality.phase_loss_db': 'Channel cancellation', 'selection.ambiguous': 'Source selection ambiguity',
}
const sourceLabels: Record<string, string> = { hardcover: 'Hardcover', openlibrary: 'Open Library', audible: 'Audible', audnexus: 'Audnexus', audible_file: 'Audible metadata file', audiobookdb: 'AudiobookDB', applebooks: 'Apple Books', googlebooks: 'Google Books' }
function ReviewChecks({ review, work }: { review?: Pick<StageReview, 'complete' | 'checks'>; work?: WorkEvidence }) {
  if (!review?.complete) return <p className="type-body-sm text-muted-foreground my-2">Analysis is not complete yet.</p>
  const failed = review.checks.filter(c => !c.passed)
  const sources = Object.keys(work?.records ?? {}).filter(name => name !== 'catalogue').map(name => sourceLabels[name] || name)
  const familyCheck = failed.find(c => c.metric === 'metadata.work_families')
  const missing = work?.rows.filter(row => ['Title','Author','Year','Publisher'].includes(row.field) && (!row.value.trim() || row.value.toLowerCase() === 'unknown')).map(row => row.field === 'Year' ? 'Original publication year' : row.field) ?? []
  return <div className="type-body-sm my-2">
    <p>{review.checks.length - failed.length} of {review.checks.length} checks passed{failed.length > 0 && ` · ${failed.length} need review`}</p>
    {failed.length > 0 && <ul className="list-disc pl-5 text-warning">{failed.map(c => <li key={c.metric}>{metricLabels[c.metric] || c.metric}: {c.metric === 'recording.runtime_error' && c.value != null ? `${(c.value * 100).toFixed(1)}% difference` : c.value ?? 'unknown'} · {c.reason}</li>)}</ul>}
    {work && familyCheck && <div className="mt-2">
      <p>{familyCheck.value ?? 'Unknown'} of {familyCheck.threshold.min ?? 2} required independent sources confirm this book’s title and author.</p>
      <p>Confirming sources: {sources.join(' + ') || 'none'}. The catalogue’s folder names and tags do not count as an independent source.</p>
      <ul className="list-disc pl-5">{work.lookups?.filter(lookup => lookup.status !== 'confirmed').map(lookup => <li key={lookup.source}>{sourceLabels[lookup.source] || lookup.source}: {lookup.status === 'error' ? 'Lookup failed. ' : ''}{lookup.reason}</li>)}</ul>
    </div>}
    {missing.length > 0 && <p className="mt-2">Missing book metadata (informational): {missing.join(', ')}.</p>}
  </div>
}

function MetadataRows({ rows }: { rows: MetaRow[] }) {
  return <dl className="my-2 grid grid-cols-[7rem_1fr] gap-x-3 gap-y-1 type-body-sm">{rows.map(row => <div key={row.field} className="contents"><dt className="text-muted-foreground">{row.field === 'Year' ? 'Original publication year' : row.field === 'Runtime' ? 'Catalogue runtime' : row.field}</dt><dd>{row.value}<span className="type-caption text-muted-foreground"> · {row.source}</span>{row.note && <p className="type-caption text-warning">{row.note}</p>}</dd></div>)}</dl>
}

function EvidenceSources({ evidence, scope }: { evidence: MetadataEvidence; scope: 'book' | 'audiobook' }) {
  const fields = scope === 'book' ? ['title','subtitle','subtitle_edition','title_match','authors','additional_contributors','publisher','publisher_year','publisher_edition','original_year','year_kind'] : ['narrators','publisher','release_date','runtime_minutes','language','abridged','asin']
  return <Disclosure className="type-body-sm my-2" summary={<>{scope === 'book' ? 'Book' : 'Audiobook'} sources</>}><ul className="space-y-2 mt-2">{Object.entries(evidence.records).map(([name, record]) => <li key={name}><strong>{name}</strong><dl className="type-caption">{fields.filter(field => record[field] != null && record[field] !== '' && (!Array.isArray(record[field]) || (record[field] as unknown[]).length > 0)).map(field => <div key={field}><dt className="inline">{field.replaceAll('_',' ')}: </dt><dd className="inline">{Array.isArray(record[field]) ? (record[field] as unknown[]).join(', ') : String(record[field])}</dd></div>)}</dl></li>)}</ul></Disclosure>
}

export default function IngestPage({ phase = 'metadata' }: { phase?: 'metadata' | 'audiobook' | 'quality' }) {
  const [page, setPage] = useState(0)
  const [sort, setSort] = useState<ReviewSort>('strongest')
  // Polled while the worker is busy or has work queued, and left alone otherwise; the Refresh button reads it now.
  const job = useJob({ queryKey: ['ingest', page, phase, sort], queryFn: () => ingestApi.dashboard(page * 50, phase, phase === 'metadata' ? sort : undefined), staleTime: 0, placeholderData: previous => previous, isRunning: d => !!d.worker.busy || d.worker.queued > 0, every: 3000 })
  const { data, error, loading } = readOf(job), reload = job.poll
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null), [policy, setPolicy] = useState(''), [message, setMessage] = useState('')
  const [details, setDetails] = useState<object | null>(null)
  useEffect(() => { if (data) setPolicy(JSON.stringify(data.policy, null, 2)) }, [data?.policy.revision])
  useEffect(() => {
    const total = data?.total
    if (!loading && total != null) setPage(p => Math.min(p, Math.max(0, Math.ceil(total / 50) - 1)))
  }, [data?.total, loading])
  async function act(fn: () => Promise<unknown>, success: string) {
    setBusy(true); setErr(null); setMessage('')
    try { await fn(); setMessage(success); reload() } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }
  function save() {
    try { const value = JSON.parse(policy) as IngestPolicy; return act(() => ingestApi.savePolicy(value), 'Thresholds saved as a new revision') }
    catch { setErr('Thresholds must be valid JSON.') }
  }
  const label = phase === 'metadata' ? 'Book identification' : phase === 'audiobook' ? 'Audiobook identification' : 'Quality review'
  const runs = data?.runs ?? []
  const visible = runs.filter(run => !run.superseded && run.review_phase === phase)
  return <div className="space-y-4">
    <h2 className="type-title font-semibold">{label}</h2>
    <p className="type-body-sm text-muted-foreground">{phase === 'metadata' ? 'Identify the written book: title, authors, original publication year and publisher. Once approved, this identity guides the recording search.' : phase === 'audiobook' ? 'Refine the approved book identity to the specific recording: narrator, edition, language, runtime, release and abridgement.' : 'Review the selected audio, quality measurements and listening assessment before proposing final processing.'} Queued, running and failed work is in <a className="underline" href={href.process()}>Processing</a>.</p>
    <Err msg={err || error} />
    {job.status === 'ready' && job.stale && <StaleNotice stale={job.stale} />}
    <Card><div className="flex flex-wrap gap-3 items-center">
      <h2 className="type-body font-semibold">{data?.total ?? runs.length} awaiting {label.toLowerCase()}</h2>
      <Button variant="outline" size="sm" onClick={reload}>Refresh</Button>
      {phase === 'metadata' && <label className="flex items-center gap-2 type-body-sm">Sort books<NativeSelect value={sort} onChange={e => { setSort(e.target.value as ReviewSort); setPage(0) }}>
        <option value="strongest">Highest identification first</option>
        <option value="metadata">Most metadata first</option>
        <option value="newest">Newest reviews first</option>
        <option value="oldest">Oldest reviews first</option>
        <option value="title">Title A–Z</option>
      </NativeSelect></label>}
    </div>{data && <>
      <p className="type-body-sm mt-2">Worker: {data.worker.busy || 'idle'} · {data.worker.queued} queued · Book approval {data.policy.auto_approve_metadata ? 'automatic' : 'manual'} · Audiobook approval {data.policy.auto_approve_audiobook ? 'automatic' : 'manual'} · Quality approval {data.policy.auto_approve_quality ? 'automatic' : 'manual'} · Processing approval {data.policy.auto_approve ? 'automatic' : 'manual'} · Automatic processing {data.policy.auto_process ? 'enabled' : 'disabled'}</p>
      {phase !== 'quality' && <Checkbox className="mt-2" checked={phase === 'metadata' ? data.policy.auto_approve_metadata : data.policy.auto_approve_audiobook} disabled={busy} onCheckedChange={on => act(() => ingestApi.savePolicy({ ...data.policy, [phase === 'metadata' ? 'auto_approve_metadata' : 'auto_approve_audiobook']: on }), on ? `${label} automation enabled. Passing reviews queued for approval.` : `${label} automation disabled.`)} label={`Automatically approve ${phase === 'metadata' ? 'books' : 'audiobooks'} that pass every identification check`} />}
    </>}</Card>
    {message && <p role="status">{message}</p>}
    {!data && !error && <p>Loading reviews…</p>}
    {data?.runs.length === 0 && <p>No books awaiting review.</p>}
    {phase === 'metadata' && <p className="type-caption text-muted-foreground">{sort === 'strongest' ? 'More passing book checks first, then title/author similarity, source agreement and metadata completeness.' : sort === 'metadata' ? 'More book metadata fields first, then source agreement and identification checks.' : ''}</p>}
    {data && ((data.total ?? runs.length) > 50 || page > 0) && <nav aria-label="Review pages" className="flex items-center gap-3 type-body-sm">
      <Button variant="outline" size="sm" disabled={page === 0} onClick={() => setPage(p => p - 1)}>Previous page</Button>
      <span>{page * 50 + 1}–{page * 50 + runs.length} of {data.total} reviews</span>
      <Button variant="outline" size="sm" disabled={(page + 1) * 50 >= (data.total ?? 0)} onClick={() => setPage(p => p + 1)}>Next page</Button>
    </nav>}
    {data && runs.length > 0 && visible.length === 0 && <p className="type-body-sm text-muted-foreground">No books awaiting review.</p>}
    {visible.map(run => <Card key={run.id}>
      <div className="flex flex-wrap justify-between gap-2"><h3 className="font-medium">{run.title || `Book ${run.book_id}`} · {({ metadata_review: 'Book identification review', audiobook_review: 'Audiobook identification review', quality_review: 'Quality review' } as Record<string,string>)[run.state] || run.state.replaceAll('_', ' ')}</h3><span className="type-caption text-muted-foreground">{run.id.slice(0, 8)} · {run.members.length} catalogue entries</span></div>
      <SourceFiles bookId={run.book_id} />
      <p className="type-body-sm">Current thresholds: {run.reviews[phase].eligible ? 'pass' : 'review required'}{!run.reviews[phase].kind_allowed && ' · content kind requires review'}</p>
      {phase === 'metadata' && <section aria-label="Book identification" className="border-t border-border pt-3 mt-3">
        <h4 className="font-semibold">Book identification · {run.reviews.metadata.eligible ? 'checks passed' : 'review required'}</h4>
        <ReviewChecks review={run.reviews.metadata} work={run.metadata?.work} />
        {run.metadata?.work ? <><MetadataRows rows={run.metadata.work.rows} /><p className="type-body-sm">Book title/author similarity: {run.metadata.work.score == null ? 'unknown' : `${Math.round(run.metadata.work.score * 100)}%`} · {run.metadata.work.families} book source families</p><EvidenceSources evidence={run.metadata.work} scope="book" /></> : run.metadata && <MetadataRows rows={run.metadata.rows.filter(row => ['Title','Author','Year'].includes(row.field))} />}
        <Button className="mt-2" disabled={busy || !run.reviews.metadata.complete || !run.reviews.metadata.kind_allowed} onClick={() => act(() => ingestApi.approveStage(run.id, 'metadata'), 'Book approved. Audiobook identification queued.')}>Approve book and identify audiobook</Button>
      </section>}
      {phase === 'audiobook' && <section aria-label="Audiobook identification" className="border-t border-border pt-3 mt-3">
        <h4 className="font-semibold">Audiobook identification · {run.reviews.audiobook.eligible ? 'checks passed' : 'review required'}</h4>
        {run.metadata?.work && <Disclosure className="my-2 type-body-sm" summary="Approved book identity"><MetadataRows rows={run.metadata.work.rows} /></Disclosure>}
        <ReviewChecks review={run.reviews.audiobook} />
        {run.metadata?.performance && <>
          <MetadataRows rows={run.metadata.performance.rows} />
          <p className="type-body-sm">Local narrator/cast: {run.metadata.performance.local.narrators.join(', ') || 'unknown'} · Source runtime: {(run.metadata.performance.local.duration_seconds / 60).toFixed(1)} min</p>
          <p className="type-caption text-muted-foreground">Local date tags: {run.metadata.performance.local.dates.join(', ') || 'unknown'}</p>
          <EvidenceSources evidence={run.metadata.performance} scope="audiobook" />
          {(run.metadata.performance.choices?.length ?? 0) > 1 && <div className="my-3 type-body-sm"><h5 className="font-semibold">Choose the recording</h5><p>Multiple recordings match the approved book and source runtime. Select the performance to apply before approving it.</p><ul className="space-y-2 mt-2">{run.metadata.performance.choices!.map(choice => <li key={choice.key} className="rounded-control border p-2"><p>{choice.candidate.title} · {choice.candidate.narrators.join(', ') || 'narrator unknown'}</p><p>{choice.candidate.runtime_minutes} min · {(choice.runtime_error * 100).toFixed(2)}% runtime difference · {choice.candidate.language || 'language unknown'} · {choice.candidate.abridged == null ? 'abridgement unknown' : choice.candidate.abridged ? 'abridged' : 'unabridged'}</p><p>Sources: {choice.sources.map(source => sourceLabels[source] || source).join(' + ')}</p><Button variant="outline" size="sm" disabled={busy || run.metadata!.performance!.selected_choice === choice.key} onClick={() => act(() => ingestApi.chooseRecording(run.id, choice.key), 'Recording selected. Review the applied evidence, then approve audiobook identification.')}>{run.metadata!.performance!.selected_choice === choice.key ? 'Selected recording' : `Use recording by ${choice.candidate.narrators.join(', ') || choice.candidate.source}`}</Button></li>)}</ul></div>}
          {run.metadata.performance.candidate && !run.metadata.performance.matched && <div className="rounded-control border border-warning p-3 type-body-sm my-2"><h5 className="font-semibold">Catalogue candidate · not applied to this performance</h5><p>{run.metadata.performance.candidate.title} · {run.metadata.performance.candidate.narrators.join(', ') || 'narrator unknown'}</p><p>{run.metadata.performance.candidate.runtime_minutes ?? 'unknown'} min · {run.metadata.performance.candidate.publisher || 'publisher unknown'} · {run.metadata.performance.candidate.release_date || 'release date unknown'} · {run.metadata.performance.candidate.source}</p></div>}
        </>}
        <Button className="mt-2" disabled={busy || ((run.metadata?.performance?.choices?.length ?? 0) > 1 && !run.metadata?.performance?.selected_choice) || !run.reviews.metadata.approved || !run.reviews.audiobook.complete || !run.reviews.audiobook.kind_allowed} onClick={() => act(() => ingestApi.approveStage(run.id, 'audiobook'), 'Audiobook approved. Quality analysis queued.')}>Approve audiobook and analyse quality</Button>
      </section>}
      {phase === 'quality' && <Disclosure defaultOpen summary="Quality review · Awaiting approval"><section aria-label="Quality review" className="border-t border-border pt-3 mt-3">
        <ReviewChecks review={run.reviews.quality} />
        {run.selection && <QualitySamples runId={run.id} />}
      {run.selection && <Disclosure className="type-body-sm my-2" summary={<>Selected source: {run.selection.files.length} files</>}><p>{run.selection.reason}</p><ul>{run.selection.files.map(f => <li key={f}>{f}</li>)}</ul><ul>{run.selection.ranking.map(r => <li key={r.id}>{r.id}: coverage {r.reasons.coverage}, true peak {r.reasons.true_peak ?? 'unknown'} dBTP, high-band energy {r.reasons.band4k_relative_db ?? 'unknown'} dB</li>)}</ul></Disclosure>}
      {run.selection && <div className="type-body-sm my-2"><strong>Listening quality:</strong> {run.quality_rating || 'unrated'}{(run.quality_suggestions?.length ?? 0) > 0 && <p>Medium candidates to review: {run.quality_suggestions?.join(' · ')}. These measurements do not establish listening quality.</p>}<p className="type-caption text-muted-foreground">Confirm medium only for narration you want enhanced. Diamond may change words or remove music and effects; listen to the result.</p>{(!run.superseded && run.reviews.metadata.approved && !run.reviews.quality.approved && run.review_phase === 'quality') && <div className="flex gap-2 flex-wrap mt-1">{(['good', 'medium', 'poor', 'unrated'] as const).map(rating => <Button key={rating} variant="outline" size="sm" disabled={busy} onClick={() => act(() => ingestApi.rateQuality(run.id, rating), `Quality set to ${rating}`)}>{rating === 'unrated' ? 'Clear rating' : `Mark ${rating}`}</Button>)}</div>}</div>}
      {run.selection?.quiet_passages && <div className="type-body-sm my-2"><strong>Quiet passages:</strong> {run.selection.quiet_passages.median_dbfs == null ? 'not measured' : `median ${run.selection.quiet_passages.median_dbfs} dBFS`} · {run.selection.quiet_passages.samples.length} sampled<p className="type-caption text-muted-foreground">Quiet passages can include speech or music. This is not a calibrated noise floor or a recommendation to denoise.</p><Disclosure summary="Sample positions"><ul>{run.selection.quiet_passages.samples.map((s, i) => <li key={i}>{s.start_seconds}s: {s.rms_dbfs == null ? s.status : `${s.rms_dbfs} dBFS`} ({s.duration_seconds}s)</li>)}</ul></Disclosure></div>}
      {run.selection?.continuous_sound?.flagged && <p className="type-body-sm my-2"><strong>Review continuous sound:</strong> {run.selection.continuous_sound.sustained_samples} of {run.selection.continuous_sound.valid_samples} sampled passages have little level variation. This may include music or effects, as in a radio play, but continuous speech or compression can also cause it. Listen before treating it as noise.</p>}
      {run.selection?.acoustics && <div className="type-body-sm my-2"><strong>Listening review indicators</strong><p>Quiet high-band level: {run.selection.acoustics.medians.quiet_high_dbfs ?? 'unknown'} dBFS · Quiet vs active: {run.selection.acoustics.medians.quiet_to_active_db ?? 'unknown'} dB · Low vs presence: {run.selection.acoustics.medians.active_low_to_presence_db ?? 'unknown'} dB · Loudness range: {run.selection.loudness_range ?? 'unknown'} LU</p><p className="type-caption text-muted-foreground">Approximate sampled indicators, not a quality rating or speech/noise separation. Music, quiet speech and recording bandwidth affect them.</p></div>}
      {run.selection?.provenance && <Disclosure className="type-body-sm my-2" summary="Source format and provenance clues"><p>{run.selection.provenance.file_count} files · {run.selection.provenance.codecs.join(', ') || 'codec unknown'} · {run.selection.provenance.extensions.join(', ') || 'format unknown'} · {run.selection.provenance.bitrates_kbps.length ? `${run.selection.provenance.bitrates_kbps.length > 2 ? `${run.selection.provenance.bitrates_kbps[0]}–${run.selection.provenance.bitrates_kbps[run.selection.provenance.bitrates_kbps.length - 1]}` : run.selection.provenance.bitrates_kbps.join(', ')} kbps` : 'bitrate unknown'} · {run.selection.provenance.sample_rates_hz.length ? `${run.selection.provenance.sample_rates_hz.join(', ')} Hz` : 'sample rate unknown'}</p><p>Recording release: {run.selection.provenance.recording_release_dates.map(d => `${d.release_date} (${d.source})`).join(' · ') || 'unknown'}</p><p>Embedded date tags: {run.selection.provenance.embedded_dates.join(', ') || 'unknown'}</p><ul>{run.selection.provenance.medium_clues.map((c, i) => <li key={i}>{c.source}: {c.value}</li>)}</ul>{run.selection.provenance.medium_clues.length === 0 && <p>Original medium: unknown</p>}<p className="type-caption text-muted-foreground">Names, tags and release dates are clues, not proof of the original medium or recording date. Codec and bitrate alone do not establish quality.</p></Disclosure>}
        {<Button className="mt-2" disabled={busy || !run.reviews.metadata.approved || !run.reviews.audiobook?.approved || !run.reviews.quality.complete} onClick={() => act(() => ingestApi.approveStage(run.id, 'quality'), 'Quality approved. Processing proposal queued for review.')}>Approve quality and propose processing</Button>}
      </section></Disclosure>}
      <Disclosure className="mt-3" summary="Analysis history and timings"><ol className="type-body-sm my-2 grid gap-1">{run.stages.map(s => <li key={s.stage}><strong>{s.stage}</strong>: {s.status} · {s.elapsed.toFixed(1)}s {s.error && <span className="text-danger">{s.error}</span>}<div className="type-caption text-muted-foreground flex flex-wrap gap-x-3">{Object.entries(s.metrics).map(([name, value]) => <span key={name}>{name}: {value ?? 'unknown'}</span>)}</div></li>)}</ol></Disclosure>
      <Disclosure summary="Metrics and thresholds"><table className="type-caption w-full"><thead><tr><th className="text-left">Metric</th><th>Value</th><th>Threshold</th><th>Result</th></tr></thead><tbody>{(run.reviews[phase].checks ?? []).map(c => <tr key={c.metric}><td>{c.metric}</td><td className="text-center">{c.value ?? 'unknown'}</td><td className="text-center">{JSON.stringify(c.threshold)}</td><td className="text-center">{c.passed ? 'pass' : c.reason}</td></tr>)}</tbody></table></Disclosure>
      <div className="flex flex-wrap gap-2 mt-3">
        <Button variant="outline" size="sm" disabled={busy} onClick={() => act(() => ingestApi.evaluate(run.id), 'Saved metrics evaluated against current thresholds')}>Evaluate thresholds</Button>
        <Button variant="outline" size="sm" disabled={busy} onClick={() => act(async () => setDetails(await ingestApi.details(run.id)), 'Evidence loaded')}>View evidence</Button>
      </div>
    </Card>)}
    {details && <Card><h3>Run evidence</h3><pre className="type-caption whitespace-pre-wrap max-h-96 overflow-auto">{JSON.stringify(details, null, 2)}</pre><Button variant="outline" size="sm" onClick={() => setDetails(null)}>Close evidence</Button></Card>}
    <Card><Disclosure summary="Threshold policy"><p className="type-body-sm my-2">Edit thresholds, then evaluate existing runs without decoding audio again. Saving creates a revision; earlier decisions retain their policy. Metadata automation also checks existing pending reviews. Proposed plans remain subject to input checks.</p>
      <Textarea aria-label="Threshold policy" className="w-full font-mono mt-2" rows={12} value={policy} onChange={e => setPolicy(e.target.value)} />
      <Button disabled={busy || !data} onClick={save}>Save thresholds</Button>
    </Disclosure></Card>
  </div>
}
