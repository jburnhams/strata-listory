import { useRef, useState } from 'react'
import { matchReviewApi, withBase, type MatchReview, type MatchCandidate } from '../api'
import { href, useLoad } from '../route'
import { useMatchReviewQueue } from '../useMatchReviewQueue'
import { Card, Err, btn, btnQuiet, input } from './ui'

const show = (value: unknown): string => value == null || value === '' || (Array.isArray(value) && value.length === 0) ? 'Unknown' : Array.isArray(value) ? value.map(show).join(', ') : typeof value === 'boolean' ? value ? 'Yes' : 'No' : typeof value === 'object' ? Object.entries(value).map(([k, v]) => `${k.replaceAll('_', ' ')}: ${show(v)}`).join(' · ') : String(value)
const sourceName = (name: string) => ({ hardcover: 'Hardcover', openlibrary: 'Open Library', audible: 'Audible', audnexus: 'Audnexus', catalogue: 'Source tags / catalogue', audiobookdb: 'AudiobookDB', applebooks: 'Apple Books' }[name] || name)
const labels: Record<string, string> = { 'metadata.work_families': 'Independent source agreement', 'metadata.original_work_link': 'Original work link', 'work.score': 'Title and author match', 'work.conflicts': 'Conflicting publication details', 'identity.conflicts': 'Conflicting source identity', 'identity.author_known': 'Author identified', 'identity.title_known': 'Title identified', 'inventory.probe_errors': 'Source readability', 'inventory.files': 'Source files', 'review.manual_hold': 'Manual review requested' }
function Fields({ values }: { values: [string, unknown][] }) {
  return <dl className="grid grid-cols-[6.5rem_1fr] gap-x-3 gap-y-1 text-sm">{values.map(([name, value]) => <div key={name} className="contents"><dt className="text-stone-500">{name}</dt><dd className="min-w-0 break-words">{show(value)}</dd></div>)}</dl>
}
function SourceLink({ candidate }: { candidate: MatchCandidate }) {
  return candidate.url && /^https?:\/\//.test(candidate.url) ? <a className="underline text-blue-600 dark:text-blue-400" href={withBase(candidate.url)} target="_blank" rel="noreferrer">{sourceName(candidate.source)}</a> : <span>{sourceName(candidate.source)}</span>
}
function MoreEvidence({ id }: { id: string }) {
  const { data, error } = useLoad(() => matchReviewApi.details(id), [id])
  if (error) return <Err msg={error} />
  if (!data) return <p className="text-sm" role="status">Loading evidence…</p>
  const proposed = data.suggestion?.candidate
  return <div className="space-y-4 mt-3">
    {proposed && <Fields values={[
      ['ISBN', proposed.isbns], ['ASIN', proposed.asin], ['Series', [proposed.series, proposed.series_position].filter(v => v != null && v !== '')], ['Genres', proposed.genres], ['Pages', proposed.pages],
      ['Rating', proposed.rating == null ? null : `${proposed.rating} · ${proposed.ratings_count ?? 'unknown'} ratings`], ['Edition release', proposed.release_date], ['Language', proposed.language],
      ['Narrators', proposed.narrators], ['Runtime', proposed.runtime_minutes == null ? null : `${proposed.runtime_minutes} minutes`], ['Abridged', proposed.abridged],
    ]} />}
    {proposed?.description && <p className="text-sm whitespace-pre-line">{proposed.description}</p>}
    <section aria-label="Book evidence"><h4 className="font-medium mb-2">Book evidence</h4>{data.match.rows.length > 0 && <Fields values={data.match.rows.map(row => [row.field, `${row.value} · ${row.source}${row.note ? ` · ${row.note}` : ''}`])} />}
      {Object.entries(data.evidence?.records ?? {}).map(([name, record]) => <details key={name} className="text-sm mt-2"><summary className="cursor-pointer">{sourceName(name)} assertions</summary><Fields values={Object.entries(record).map(([key, value]) => [key.replaceAll('_', ' '), value])} /></details>)}
    </section>
    {data.recording.rows && <section aria-label="Recording evidence"><h4 className="font-medium mb-2">Recording evidence · separate approval</h4><Fields values={data.recording.rows.map(row => [row.field, `${row.value} · ${row.source}${row.note ? ` · ${row.note}` : ''}`])} /></section>}
    <section aria-label="Source metadata"><h4 className="font-medium mb-2">Source tags</h4><Fields values={Object.entries(data.evidence?.tags ?? {}).map(([key, value]) => [key.replaceAll('_', ' '), value])} />
      <details className="text-sm mt-2"><summary className="cursor-pointer">All {data.source.count} source files</summary><ul className="list-disc pl-5 break-all">{data.source.files.map((file, i) => <li key={i}>{file}</li>)}</ul></details>
    </section>
    <section aria-label="Catalogue alternatives"><h4 className="font-medium mb-2">Catalogue results</h4>{Object.entries(data.evidence?.providers ?? {}).map(([name, result]) => <details key={name} className="text-sm mt-2"><summary className="cursor-pointer">{sourceName(name)} · {result.candidates.length} results · {result.status}</summary>
      {result.reason && <p>{result.reason}</p>}{result.candidates.map((candidate, i) => <div key={i} className="border-l-2 border-stone-200 dark:border-stone-700 pl-3 my-3"><p className="font-medium">{candidate.title}{candidate.subtitle && !candidate.title.includes(candidate.subtitle) ? `: ${candidate.subtitle}` : ''}</p><p>{show(candidate.authors)} · {show(candidate.original_year)} · <SourceLink candidate={candidate} /></p><details><summary className="cursor-pointer">All catalogue fields</summary><Fields values={Object.entries(candidate).map(([key, value]) => [key.replaceAll('_', ' '), value])} /></details></div>)}
    </details>)}</section>
    {data.suggestion && <details className="text-sm"><summary className="cursor-pointer">Match measurements</summary><Fields values={Object.entries(data.suggestion.features).map(([key, value]) => [key.replaceAll('_', ' '), Number(value.toFixed(3))])} /></details>}
    {data.approval && <details className="text-sm"><summary className="cursor-pointer">Approval record</summary><Fields values={Object.entries(data.approval).filter(([key]) => key !== 'checks').map(([key, value]) => [key.replaceAll('_', ' '), value])} /><ul>{data.approval.checks?.map(c => <li key={c.metric}>{labels[c.metric] || c.metric}: {c.passed ? 'passed' : 'overridden'} · {show(c.value)}</li>)}</ul></details>}
  </div>
}
function ReviewCard({ row, mode, busy, selected, act }: { row: MatchReview; mode: 'automatic' | 'shortlist'; busy: boolean; selected?: 'approve' | 'reject'; act: (row: MatchReview, decision: 'approve' | 'reject') => void }) {
  const [expanded, setExpanded] = useState(false)
  const c = row.suggestion?.candidate
  const failed = row.checks.filter(check => !check.passed)
  const title = c ? c.title + (c.subtitle && !c.title.includes(c.subtitle) ? `: ${c.subtitle}` : '') : row.match.title
  return <Card>
    <article aria-label={`Review ${row.local.title}`} className="space-y-3">
      <div className="flex items-start gap-3">
        {row.cover && <img src={withBase(`/api/books/${row.book_id}/cover`)} alt="" loading="lazy" className="w-12 h-16 rounded object-cover" />}
        <div className="flex-1 min-w-0"><h2 className="font-semibold"><a href={withBase(href.book(row.book_id))} className="hover:underline">{row.local.title}</a></h2><p className="text-xs text-stone-500">#{row.book_id} · {row.local.kind} · {row.state.replaceAll('_', ' ')}{row.approved_at && ` · approved ${new Date(row.approved_at).toLocaleDateString()}`}</p></div>
        <div className="flex flex-wrap gap-2">
          <button type="button" className={btn} aria-pressed={selected === 'approve'} disabled={!!busy || selected === 'approve' || !!row.blocked || (mode === 'shortlist' && !row.complete)} onClick={() => act(row, 'approve')}>{busy ? 'Saving…' : 'Approve'}</button>
          <button type="button" className={`${btnQuiet} text-red-700 dark:text-red-400`} aria-pressed={selected === 'reject'} disabled={!!busy || selected === 'reject' || !!row.blocked} onClick={() => act(row, 'reject')}>Reject</button>
        </div>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <section aria-label="Local identity" className="rounded bg-stone-50 dark:bg-stone-800/50 p-3"><h3 className="text-xs uppercase tracking-wide text-stone-500 mb-2">Source identity</h3><Fields values={[
          ['Title', row.local.title], ['Author', row.local.author], ['Narrator', row.local.narrator], ['Duration', row.source.hours ? `${row.source.hours.toFixed(2)} hours` : null], ['ASIN', row.local.asin], ['Language', row.recording.local?.language], ['Abridged', row.recording.local?.abridged],
        ]} />{row.local.locked.length > 0 && <p className="text-xs mt-2">Manually locked: {row.local.locked.join(', ')}</p>}</section>
        <section aria-label={mode === 'automatic' ? 'Approved match' : 'Proposed match'} className="rounded border border-blue-200 dark:border-blue-900 p-3"><h3 className="text-xs uppercase tracking-wide text-blue-600 dark:text-blue-400 mb-2">{mode === 'automatic' ? 'Automatically approved book' : 'Proposed book match'}</h3><Fields values={[
          ['Title', title], ['Authors', c?.authors ?? row.match.authors], ['Original year', c ? c.original_year : row.match.year], ['Publisher', c ? c.publisher : row.match.publisher],
        ]} />{!c && <p className="text-xs mt-2">Supporting sources: {row.match.sources.map(sourceName).join(' + ') || 'None saved'}</p>}{c && <p className="text-xs mt-2"><SourceLink candidate={c} /> · model score {row.suggestion!.score.toFixed(3)}</p>}</section>
      </div>
      <p className="text-xs text-stone-500 break-all">{row.source.folder} · {row.source.count} files{row.source.files.length > 0 && ` · ${row.source.files.join(' · ')}${row.source.count > row.source.files.length ? ' …' : ''}`}</p>
      {row.suggestion && <p className="text-xs text-stone-500">Title similarity {Math.round((row.suggestion.features.title_edit ?? 0) * 100)}% · author similarity {Math.round((row.suggestion.features.author_edit ?? 0) * 100)}% · {row.suggestion.features.supporting_families ?? 0} exact source agreements. Model score is not a certainty percentage.</p>}
      {!row.complete ? <p className="text-sm text-amber-700 dark:text-amber-400">Saved book analysis is incomplete.</p> : failed.length > 0 ? <ul aria-label="Review warnings" className="text-sm text-amber-700 dark:text-amber-400 list-disc pl-5">{failed.map(c => <li key={c.metric}>{labels[c.metric] || c.metric} · {c.reason}{c.value != null && ` (${c.value})`}</li>)}</ul> : <p className="text-xs text-emerald-700 dark:text-emerald-400">All saved book checks passed.</p>}
      {row.match.rows.some(field => field.note) && <ul className="text-sm text-amber-700 dark:text-amber-400 list-disc pl-5">{row.match.rows.filter(field => field.note).map(field => <li key={field.field}>{field.field}: {field.note}</li>)}</ul>}
      {row.blocked && <p className="text-sm text-amber-700 dark:text-amber-400">{row.blocked}</p>}
      <details onToggle={e => setExpanded(e.currentTarget.open)}><summary className="text-sm cursor-pointer underline">All evidence and alternatives</summary>{expanded && <MoreEvidence id={row.id} />}</details>
    </article>
  </Card>
}
export default function MatchReviewPage({ mode }: { mode: 'automatic' | 'shortlist' }) {
  const [query, setQuery] = useState(''), [advancing, setAdvancing] = useState(false)
  const { data, error, loading, reload, next, previous, saved } = useMatchReviewQueue(mode, query)
  const offset = data?.offset ?? 0
  const inFlight = useRef(new Set<string>())
  const [decisions, setDecisions] = useState<Record<string, { action: 'approve' | 'reject'; basis: string }>>({})
  const [pending, setPending] = useState<Set<string>>(new Set()), [actionErrors, setActionErrors] = useState<Record<string, string>>({}), [message, setMessage] = useState('')
  async function act(row: MatchReview, decision: 'approve' | 'reject') {
    if (inFlight.current.has(row.id)) return
    inFlight.current.add(row.id); setPending(new Set(inFlight.current))
    setActionErrors(errors => { const next = { ...errors }; delete next[row.id]; return next })
    try {
      const prior = decisions[row.id]
      const result = await matchReviewApi.decide(row.id, decision === 'reject' ? 'reject' : prior?.action === 'reject' && prior.basis === row.basis ? 'restore' : mode === 'automatic' ? 'confirm' : 'accept', row.basis, mode === 'shortlist')
      const basis = result.basis ?? row.basis
      saved(row.id, row.basis, basis)
      setDecisions(values => ({ ...values, [row.id]: { action: decision, basis } }))
      setMessage(decision === 'reject' ? `${row.local.title}: match rejected. Automatic reapproval is held for manual review.` : mode === 'automatic' ? `${row.local.title}: approval confirmed.` : `${row.local.title}: book match accepted. Recording identification starts when you press Next.`)
    }
    catch (e) { setActionErrors(errors => ({ ...errors, [row.id]: `${row.local.title}: ${(e as Error).message}` })) }
    finally { inFlight.current.delete(row.id); setPending(new Set(inFlight.current)) }
  }
  async function advance() {
    if (!data || advancing) return
    setAdvancing(true)
    try {
      const completed = data.rows.filter(row => decisions[row.id]?.basis === row.basis && !inFlight.current.has(row.id))
      const removable = new Set(completed.map(row => row.id))
      if (mode === 'shortlist') {
        await Promise.all(completed.filter(row => decisions[row.id].action === 'approve').map(async row => {
          try {
            setActionErrors(errors => { const next = { ...errors }; delete next[row.id]; return next })
            await matchReviewApi.advance(row.id)
          }
          catch (e) { removable.delete(row.id); setActionErrors(errors => ({ ...errors, [row.id]: `${row.local.title}: ${(e as Error).message}` })) }
        }))
      }
      if (completed.length && !removable.size) return
      await next(removable, inFlight.current)
    } finally { setAdvancing(false) }
  }
  return <div className="space-y-4">
    <h1 className="text-xl font-semibold">{mode === 'automatic' ? 'Automatic approvals' : 'Match shortlist'}</h1>
    <p className="text-sm text-stone-500">{mode === 'automatic' ? 'Approve to confirm an automatic book match. Reject to withdraw consent and hold it for manual review. Existing outputs are retained.' : 'Approve the displayed book match, including any shown exceptions, then press Next to start separate recording identification. Reject dismisses the suggestion and holds the book for manual review.'}</p>
    <p className="text-sm text-stone-500">Saved cards stay in place. Change a decision with the other button. Next clears saved cards and fills the batch with new reviews.</p>
    <div className="flex flex-wrap items-center gap-3"><label className="flex-1 text-sm">Search reviews<input className={`${input} block w-full mt-1`} value={query} onChange={e => setQuery(e.target.value)} placeholder="Title or author" /></label><button className={btnQuiet} disabled={loading} onClick={reload}>Refresh</button></div>
    <Err msg={error} />{Object.entries(actionErrors).map(([id, msg]) => <Err key={id} msg={msg} />)}{message && <p role="status" className="text-sm">{message}</p>}
    {!data && !error && <p role="status">Loading match reviews…</p>}
    {data && loading && data.rows.length === 0 && <p aria-live="polite">Loading next reviews…</p>}
    {data && <><nav aria-label="Match review pages" className="flex items-center gap-3 text-sm"><span>{data.total === 0 ? '0 reviews' : `${offset + 1}–${Math.min(offset + data.rows.length, data.total)} of ${data.total} reviews`}</span><button className={btnQuiet} disabled={offset === 0 || loading} onClick={previous}>Previous</button><button className={btnQuiet} disabled={advancing || loading || (!data.rows.some(row => decisions[row.id]?.basis === row.basis) && offset + data.rows.length >= data.total)} onClick={() => { void advance() }}>Next</button></nav>
      {data.rows.length === 0 && !loading && !error && <p>{mode === 'automatic' ? 'No automatic approvals to review.' : 'No current model suggestions. Imported suggestions appear here when their saved evidence is still current.'}</p>}
      {data.rows.map(row => <ReviewCard key={row.id} row={row} mode={mode} busy={pending.has(row.id)} selected={decisions[row.id]?.basis === row.basis ? decisions[row.id].action : undefined} act={act} />)}
    </>}
  </div>
}
