import { afterEach, describe, expect, it, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import IngestPage from '../../src/components/IngestPage'
import App from '../../src/App'
import { setup, screen, waitFor, act, within } from '../utils/render'
import { ingestFixture, server } from '../utils/server'
import { href, parse } from '../../src/route'

afterEach(() => vi.restoreAllMocks())
const qualityFixture = () => { const f = ingestFixture(); f.runs[0].review_phase = 'quality'; f.runs[0].reviews.metadata.approved = true; f.runs[0].reviews.audiobook.approved = true; return f }

describe('staged ingest', () => {
  it('requires a choice between different recording performances', async () => {
    const f = ingestFixture(); f.runs[0].review_phase = 'audiobook'; f.runs[0].reviews.metadata.approved = true
    const choices = ['Reader One', 'Reader Two'].map((name, i) => ({ key: `audible:${i}`, candidate: { source: 'audible', title: 'Dune', narrators: [name], runtime_minutes: 60, language: 'english', abridged: false }, sources: ['audible', 'audnexus'], runtime_error: .01 }))
    const performance = { rows: [], records: {}, matched: false, local: { title: 'Dune', narrators: [], language: '', abridged: null, duration_seconds: 3600, dates: [] }, candidate: null, choices, selected_choice: null as string | null }
    Object.assign(f.runs[0].metadata!, { performance }); let chosen = ''
    server.use(http.get('/api/ingest', () => HttpResponse.json(f)), http.put('/api/ingest/runs/:id/recording', async ({ request }) => {
      chosen = (await request.json() as { key: string }).key; performance.selected_choice = chosen; performance.matched = true
      return HttpResponse.json({ key: chosen, changed: true })
    }))
    const { user } = setup(<IngestPage phase="audiobook" />)
    expect(await screen.findByRole('button', { name: 'Approve audiobook and analyse quality' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Use recording by Reader Two' }))
    expect(await screen.findByRole('button', { name: 'Selected recording' })).toBeDisabled()
    expect(chosen).toBe('audible:1')
    expect(screen.getByRole('button', { name: 'Approve audiobook and analyse quality' })).toBeEnabled()
  })

  it('explains missing independent evidence and distinguishes missing book fields', async () => {
    const f = ingestFixture()
    f.runs[0].reviews.metadata.checks = [{ metric: 'metadata.work_families', value: 1, threshold: { min: 2 }, passed: false, reason: 'outside threshold' }]
    Object.assign(f.runs[0].metadata!, { work: { score: 1, families: 1, records: { catalogue: { title: 'Outer Dark' }, hardcover: { title: 'Outer Dark' } }, rows: [{ field: 'Publisher', value: 'unknown', source: 'No supporting source', note: '' }], lookups: [{ source: 'hardcover', status: 'confirmed', reason: '' }, { source: 'openlibrary', status: 'error', reason: 'Connection reset by peer' }] } })
    server.use(http.get('/api/ingest', () => HttpResponse.json(f)))
    setup(<IngestPage />)
    expect(await screen.findByText('1 of 2 required independent sources confirm this book’s title and author.')).toBeInTheDocument()
    expect(screen.getByText(/Confirming sources: Hardcover/)).toBeInTheDocument()
    expect(screen.getByText(/Open Library: Lookup failed. Connection reset by peer/)).toBeInTheDocument()
    expect(screen.getByText('Missing book metadata (informational): Publisher.')).toBeInTheDocument()
  })

  it('waits for a slow initial page before polling again', async () => {
    let poll = () => {}; let finish!: () => void; let requests = 0
    const ready = new Promise<void>(resolve => { finish = resolve })
    const realInterval = window.setInterval.bind(window)
    // Testing Library also uses intervals: only capture the app's polling timer.
    vi.spyOn(window, 'setInterval').mockImplementation((cb, delay, ...args) => {
      if (delay !== 3000) return realInterval(cb, delay, ...args) as unknown as ReturnType<typeof window.setInterval>
      poll = cb as () => void; return {} as ReturnType<typeof window.setInterval>
    })
    server.use(http.get('/api/ingest', async () => { requests++; await ready; return HttpResponse.json(ingestFixture()) }))
    try {
      setup(<IngestPage />)
      await waitFor(() => expect(requests).toBe(1))
      act(() => { poll(); poll(); poll() })
      expect(requests).toBe(1)
      finish()
      expect(await screen.findByRole('button', { name: 'Approve book and identify audiobook' })).toBeEnabled()
      act(() => poll())
      await waitFor(() => expect(requests).toBe(2))
    } finally { finish() }
  })

  it('toggles metadata automation without changing quality or processing approval', async () => {
    const f = ingestFixture(); let body: Record<string, unknown> = {}
    server.use(http.get('/api/ingest', () => HttpResponse.json(f)), http.put('/api/ingest/policy', async ({ request }) => {
      body = await request.json() as Record<string, unknown>
      f.policy = { ...f.policy, auto_approve_metadata: body.auto_approve_metadata as boolean, revision: f.policy.revision + 1 }
      return HttpResponse.json(f.policy)
    }))
    const { user } = setup(<IngestPage />)
    const toggle = await screen.findByRole('checkbox', { name: 'Automatically approve books that pass every identification check' })
    await user.click(toggle)
    await waitFor(() => expect(toggle).toBeChecked())
    expect(body).toMatchObject({ auto_approve_metadata: true, auto_approve_quality: false, auto_approve: false, auto_process: false })
    expect(screen.getByText(/Book approval automatic · Audiobook approval manual · Quality approval manual/)).toBeInTheDocument()
    await user.click(toggle)
    await waitFor(() => expect(toggle).not.toBeChecked())
    expect(await screen.findByRole('status')).toHaveTextContent('Book identification automation disabled')
  })
  it('shows an approval error while keeping the run in metadata review', async () => {
    server.use(http.post('/api/ingest/runs/:id/approve/:phase', () => HttpResponse.json({ detail: 'source files changed' }, { status: 400 })))
    const { user } = setup(<IngestPage />)
    await user.click(await screen.findByRole('button', { name: 'Approve book and identify audiobook' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('source files changed')
    expect(screen.getByRole('button', { name: 'Approve book and identify audiobook' })).toBeEnabled()
    expect(screen.queryByRole('link', { name: 'Review processing proposal' })).toBeNull()
  })

  it('pages through older runs without losing access to their review queues', async () => {
    const f = ingestFixture()
    let offset = 0; let sort = ''
    server.use(http.get('/api/ingest', ({ request }) => {
      offset = Number(new URL(request.url).searchParams.get('offset') || 0)
      sort = new URL(request.url).searchParams.get('sort') || ''
      return HttpResponse.json({ ...f, total: 51, offset, runs: [{ ...f.runs[0], title: offset ? 'Older book' : 'Dune' }] })
    }))
    const { user } = setup(<IngestPage />)
    expect(await screen.findByRole('button', { name: 'Previous page' })).toBeDisabled()
    expect(screen.getByRole('combobox', { name: 'Sort books' })).toHaveValue('strongest')
    await user.click(screen.getByRole('button', { name: 'Next page' }))
    expect(await screen.findByText('Older book · review')).toBeInTheDocument()
    expect(offset).toBe(50)
    expect(sort).toBe('strongest')
    await user.selectOptions(screen.getByRole('combobox', { name: 'Sort books' }), 'metadata')
    await waitFor(() => expect(sort).toBe('metadata'))
    expect(offset).toBe(0)
    expect(screen.getByRole('button', { name: 'Previous page' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Next page' }))
    await screen.findByText('Older book · review')
    expect(screen.getByRole('button', { name: 'Next page' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Previous page' }))
    expect(await screen.findByText('Dune · review')).toBeInTheDocument()
  })
  it('flags continuous sound for listening without identifying it as noise', async () => {
    const f = qualityFixture()
    Object.assign(f.runs[0], { selection: { files: ['one.mp3'], reason: 'complete', ranking: [], continuous_sound: { flagged: true, sustained_samples: 2, valid_samples: 3, threshold_db: -8, method: 'relative-window-review-v1' } } })
    server.use(http.get('/api/ingest', () => HttpResponse.json(f)))
    setup(<IngestPage phase="quality" />)
    expect(await screen.findByText(/2 of 3 sampled passages/)).toHaveTextContent('radio play')
    expect(screen.getByText(/Listen before treating it as noise/)).toBeInTheDocument()
  })
  it('lets the user confirm a suggested medium recording', async () => {
    const f = qualityFixture()
    f.runs[0].review_phase = 'quality'; f.runs[0].reviews.metadata.approved = true
    Object.assign(f.runs[0], { quality_rating: 'unrated', quality_suggestions: ['Low encoded bitrate (48 kbps or less)'], selection: { files: ['one.mp3'], reason: 'complete', ranking: [] } })
    server.use(http.get('/api/ingest', () => HttpResponse.json(f)))
    const { user } = setup(<IngestPage phase="quality" />)
    expect(await screen.findByText(/Medium candidates to review/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Mark medium' }))
    expect(await screen.findByRole('status')).toHaveTextContent('Quality set to medium')
  })
  it('shows source format and attributed provenance clues', async () => {
    const f = qualityFixture()
    Object.assign(f.runs[0], { selection: { files: ['one.mp3'], reason: 'complete', ranking: [], provenance: { file_count: 1, codecs: ['mp3'], extensions: ['.mp3'], bitrates_kbps: [64], sample_rates_hz: [22050], recording_release_dates: [{ source: 'audible', release_date: '2010' }], embedded_dates: ['1990'], medium_clues: [{ source: 'file name', value: 'CD 01.mp3' }], medium_clues_truncated: false } } })
    server.use(http.get('/api/ingest', () => HttpResponse.json(f)))
    const { user } = setup(<IngestPage phase="quality" />)
    await user.click(await screen.findByText('Source format and provenance clues'))
    expect(screen.getByText(/64 kbps/)).toBeInTheDocument()
    expect(screen.getByText(/2010 \(audible\)/)).toBeInTheDocument()
    expect(screen.getByText('file name: CD 01.mp3')).toBeInTheDocument()
    expect(screen.getByText(/not proof of the original medium/)).toBeInTheDocument()
  })
  it('shows unknown provenance and a bitrate range', async () => {
    const f = qualityFixture()
    Object.assign(f.runs[0], { selection: { files: ['one.mp3'], reason: 'complete', ranking: [], provenance: { file_count: 1, codecs: [], extensions: [], bitrates_kbps: [32, 64, 96], sample_rates_hz: [], recording_release_dates: [], embedded_dates: [], medium_clues: [], medium_clues_truncated: false } } })
    server.use(http.get('/api/ingest', () => HttpResponse.json(f)))
    const { user } = setup(<IngestPage phase="quality" />)
    await user.click(await screen.findByText('Source format and provenance clues'))
    expect(screen.getByText(/32–96 kbps/)).toHaveTextContent('sample rate unknown')
    expect(screen.getByText('Original medium: unknown')).toBeInTheDocument()
    expect(screen.getByText('Recording release: unknown')).toBeInTheDocument()
  })
  it('shows quiet passage measurements without calling them a noise floor', async () => {
    const f = qualityFixture()
    Object.assign(f.runs[0], { selection: { files: ['one.mp3'], reason: 'complete', ranking: [], quiet_passages: { median_dbfs: -52.3, samples: [{ start_seconds: 10, duration_seconds: .5, rms_dbfs: -52.3, status: 'measured' }] } } })
    server.use(http.get('/api/ingest', () => HttpResponse.json(f)))
    const { user } = setup(<IngestPage phase="quality" />)
    expect(await screen.findByText(/median -52.3 dBFS/)).toBeInTheDocument()
    expect(screen.getByText(/not a calibrated noise floor/)).toBeInTheDocument()
    await user.click(screen.getByText('Sample positions'))
    expect(screen.getByText('10s: -52.3 dBFS (0.5s)')).toBeInTheDocument()
  })
  it('shows review indicators as estimates', async () => {
    const f = qualityFixture()
    Object.assign(f.runs[0], { selection: { files: ['one.mp3'], reason: 'complete', ranking: [], loudness_range: 5.3, acoustics: { medians: { quiet_rms_dbfs: -50, quiet_high_dbfs: -60, quiet_to_active_db: -30, active_low_to_presence_db: 8 }, samples: [] } } })
    server.use(http.get('/api/ingest', () => HttpResponse.json(f)))
    setup(<IngestPage phase="quality" />)
    expect(await screen.findByText(/Quiet high-band level: -60 dBFS/)).toHaveTextContent('Loudness range: 5.3 LU')
    expect(screen.getByText(/not a quality rating/)).toBeInTheDocument()
  })
  it('shows stage timings, unknown metrics and review thresholds', async () => {
    setup(<IngestPage />)
    expect(screen.getByText('Loading reviews…')).toBeInTheDocument()
    expect(await screen.findByText('Dune · review')).toBeInTheDocument()
    expect(screen.getByText(/Book approval manual/)).toBeInTheDocument()
    expect(screen.getByText('unknown')).toBeInTheDocument()
    expect(screen.getByText('missing measurement')).toBeInTheDocument()
    expect(screen.getByText(/1.3s/)).toBeInTheDocument()
  })
  it('navigates to ingest from the app', async () => {
    expect(parse('#/ingest')).toEqual({ page: 'metadata' }); expect(href.ingest()).toBe('#/metadata')
    window.location.hash = '#/ingest'; setup(<App />)
    expect(await screen.findByText('Dune · review')).toBeInTheDocument()
  })
  it('shows empty and server-error states', async () => {
    server.use(http.get('/api/ingest', () => HttpResponse.json({ ...ingestFixture(), runs: [] })))
    const { user } = setup(<IngestPage />)
    expect(await screen.findByText('No books awaiting review.')).toBeInTheDocument()
    server.use(http.get('/api/ingest', () => HttpResponse.json({ detail: 'offline' }, { status: 503 })))
    await user.click(screen.getByRole('button', { name: 'Refresh' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('offline')
  })
  it('evaluates saved metrics and displays preserved evidence', async () => {
    const { user } = setup(<IngestPage />); await screen.findByText('Dune · review')
    await user.click(screen.getByRole('button', { name: 'Evaluate thresholds' }))
    expect(await screen.findByRole('status')).toHaveTextContent('Saved metrics evaluated')
    await user.click(screen.getByRole('button', { name: 'View evidence' }))
    expect(await screen.findByText(/"preserved"/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Close evidence' }))
    expect(screen.queryByText('Run evidence')).toBeNull()
  })
  it('saves policy revisions and handles invalid JSON and rejected policies', async () => {
    const { user } = setup(<IngestPage />); await screen.findByText('Dune · review')
    await user.click(screen.getByText('Threshold policy'))
    await user.click(screen.getByRole('button', { name: 'Save thresholds' }))
    expect(await screen.findByRole('status')).toHaveTextContent('new revision')
    await user.clear(screen.getByLabelText('Threshold policy')); await user.type(screen.getByLabelText('Threshold policy'), 'invalid')
    await user.click(screen.getByRole('button', { name: 'Save thresholds' }))
    expect(screen.getByRole('alert')).toHaveTextContent('valid JSON')
    await user.clear(screen.getByLabelText('Threshold policy')); await user.paste(JSON.stringify(ingestFixture().policy))
    server.use(http.put('/api/ingest/policy', () => HttpResponse.json({ detail: 'invalid gate' }, { status: 400 })))
    await user.click(screen.getByRole('button', { name: 'Save thresholds' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('invalid gate')
  })
  it('polls active work and displays passing thresholds', async () => {
    const f = ingestFixture(); f.worker.busy = 'run'; f.policy.auto_approve = true; f.policy.auto_process = true
    f.runs[0].state = 'eligible'; f.runs[0].current_evaluation.eligible = true; f.runs[0].current_evaluation.checks[0].value = 1; f.runs[0].current_evaluation.checks[0].passed = true
    f.runs[0].reviews.metadata.eligible = true; f.runs[0].reviews.metadata.checks[0].value = 1; f.runs[0].reviews.metadata.checks[0].passed = true
    let poll: (() => void) | undefined
    vi.spyOn(window, 'setInterval').mockImplementation(cb => { poll = cb as () => void; return {} as ReturnType<typeof window.setInterval> })
    vi.spyOn(window, 'clearInterval').mockImplementation(() => {})
    server.use(http.get('/api/ingest', () => HttpResponse.json(f)))
    setup(<IngestPage />); await screen.findByText('Dune · eligible')
    await waitFor(() => expect(poll).toBeDefined()); act(() => poll?.())
    expect(screen.getByText(/Current thresholds: pass/)).toBeInTheDocument()
    expect(screen.getByText('pass', { selector: 'td' })).toBeInTheDocument()
  })
})

it('shows selected files and measured source alternatives', async () => {
  const f = { ...qualityFixture(), runs: [{ ...qualityFixture().runs[0], selection: { files: ['Part01.mp3', 'Part02.mp3'], reason: 'Complete recording with better measured quality', ranking: [{ id: 'source1', reasons: { coverage: 1, true_peak: -1, band4k_relative_db: -20 } }, { id: 'source2', reasons: { coverage: .5, true_peak: null, band4k_relative_db: null } }] } }] }
  server.use(http.get('/api/ingest', () => HttpResponse.json(f)))
  setup(<IngestPage phase="quality" />)
  expect(await screen.findByText('Selected source: 2 files')).toBeInTheDocument()
  expect(screen.getByText('Part01.mp3')).toBeInTheDocument()
  expect(screen.getByText(/source2: coverage 0.5/)).toHaveTextContent('unknown')
})

it('shows numeric and unknown measurements for each stage', async () => {
  const f = ingestFixture(); f.runs[0].stages[0].metrics = { 'inventory.files': 16, 'quality.noise_estimate': null }
  server.use(http.get('/api/ingest', () => HttpResponse.json(f)))
  setup(<IngestPage />)
  expect(await screen.findByText('inventory.files: 16')).toBeInTheDocument()
  expect(screen.getByText('quality.noise_estimate: unknown')).toBeInTheDocument()
})


it('moves through book, audiobook and quality pages with distinct approvals', async () => {
  const f = ingestFixture()
  server.use(http.get('/api/ingest', ({ request }) => HttpResponse.json({ ...f, total: 1, runs: f.runs.filter(r => r.review_phase === new URL(request.url).searchParams.get('phase')) })), http.post('/api/ingest/runs/:id/approve/:phase', ({ params }) => {
    if (params.phase === 'metadata') { f.runs[0].reviews.metadata.approved = true; f.runs[0].review_phase = 'audiobook' }
    else if (params.phase === 'audiobook') { f.runs[0].reviews.audiobook.approved = true; f.runs[0].review_phase = 'quality' }
    else { f.runs[0].reviews.quality.approved = true; f.runs[0].review_phase = 'processing' }
    return HttpResponse.json({ queued: true })
  }))
  window.location.hash = '#/metadata'
  const { user } = setup(<App />)
  await user.click(await screen.findByRole('button', { name: 'Approve book and identify audiobook' }))
  expect(await screen.findByText('No books awaiting review.')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Approve quality and propose processing' })).toBeNull()
  await user.click(screen.getByRole('link', { name: 'Audiobook identification' }))
  await user.click(await screen.findByRole('button', { name: 'Approve audiobook and analyse quality' }))
  expect(await screen.findByText('No books awaiting review.')).toBeInTheDocument()
  await user.click(screen.getByRole('link', { name: 'Quality' }))
  expect(await screen.findByRole('heading', { name: 'Quality review' })).toBeInTheDocument()
  await user.click(await screen.findByRole('button', { name: 'Approve quality and propose processing' }))
  expect(await screen.findByText('No books awaiting review.')).toBeInTheDocument()
  expect(screen.queryByRole('searchbox')).toBeNull()
  await user.click(screen.getByRole('link', { name: 'Library' }))
  expect(await screen.findByRole('heading', { name: 'Library · choose books to ingest' })).toBeInTheDocument()
})

it('keeps incomplete or unsupported metadata unapprovable and shows unknown recording fields', async () => {
  const f = ingestFixture(); const r = f.runs[0]
  r.title = ''; r.reviews.metadata.complete = false; r.reviews.metadata.kind_allowed = false; r.current_evaluation.kind_allowed = false
  Object.assign(r.metadata, { match_score: null, source_families: null, recording: { language: '', runtime_minutes: null, abridged: null } })
  r.metadata.rows[0].note = 'Sources disagree about the title'
  r.reviews.metadata.checks[0] = { metric: 'recording.score', value: 1, threshold: { min: .9 }, passed: true, reason: '' }
  server.use(http.get('/api/ingest', () => HttpResponse.json(f)))
  setup(<IngestPage />)
  expect(await screen.findByText('Book 1 · review')).toBeInTheDocument()
  expect(screen.getByText('Analysis is not complete yet.')).toBeInTheDocument()
  expect(screen.queryByText(/language unknown · runtime unknown · abridgement unknown/)).toBeNull()
  expect(screen.getByText('Sources disagree about the title')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Approve book and identify audiobook' })).toBeDisabled()
})

const scopedFixture = (matched = false, candidate = true) => {
  const f = ingestFixture(), check = (metric: string, passed: boolean) => ({ metric, passed, value: passed ? 1 : 0, threshold: { min: 1 }, reason: passed ? '' : 'outside threshold' })
  Object.assign(f.runs[0].reviews.metadata, { groups: {
    work: { eligible: true, complete: true, kind_allowed: true, checks: [check('work.score',true)] },
    performance: { eligible: matched, complete: true, kind_allowed: true, checks: [check('recording.matched',matched)] },
    source: { eligible: true, complete: true, kind_allowed: true, checks: [check('inventory.files',true)] },
  } })
  Object.assign(f.runs[0].metadata, {
    work: { score: 1, families: 3, rows: [{ field: 'Title', value: 'Capitalist Realism', source: 'Open Library', note: '' }, { field: 'Publisher', value: 'Book Press', source: 'Hardcover', note: '' }, { field: 'Year', value: '2009', source: 'Open Library', note: '' }], records: { openlibrary: { title: 'Capitalist Realism', authors: ['Mark Fisher'], original_year: 2009, publisher: '', year_kind: null } } },
    performance: { matched, rows: [{ field: 'Narrator', value: 'Russell Brand', source: 'Local source', note: '' }, { field: 'Publisher', value: matched ? 'Audio Press' : 'unknown', source: matched ? 'Audible' : 'No supporting source', note: '' }], records: { catalogue: { narrators: ['Russell Brand'], publisher: '', language: null, abridged: false, asin: '', release_date: null, runtime_minutes: 169 } }, local: { title: 'Capitalist Realism', narrators: ['Russell Brand'], duration_seconds: 10138, dates: ['2021'], language: '', abridged: null }, candidate: candidate ? { source: 'audible', title: 'Capitalist Realism', narrators: ['Tom Lawrence'], runtime_minutes: 250, publisher: 'Audio Press', release_date: '2024-01-04', language: 'english', abridged: false } : null },
  })
  return f
}

it('shows book evidence separately from the recording refinement', async () => {
  const f = scopedFixture()
  server.use(http.get('/api/ingest', () => HttpResponse.json(f)))
  const { rerender } = setup(<IngestPage />)
  const book = await screen.findByRole('region', { name: 'Book identification' })
  expect(within(book).getByText('Book Press')).toBeInTheDocument()
  expect(within(book).getAllByText('2009').length).toBeGreaterThan(0)
  expect(within(book).queryByText(/Tom Lawrence|Audio Press|Russell Brand/)).toBeNull()
  expect(screen.queryByRole('region', { name: 'Audiobook identification' })).toBeNull()
  f.runs[0].review_phase = 'audiobook'; f.runs[0].reviews.metadata.approved = true
  rerender(<IngestPage phase="audiobook" />)
  const performance = await screen.findByRole('region', { name: 'Audiobook identification' })
  expect(within(performance).getByText('Catalogue candidate · not applied to this performance')).toBeInTheDocument()
  expect(within(performance).getByText(/250 min · Audio Press · 2024-01-04/)).toBeInTheDocument()
  expect(within(performance).getAllByText(/Russell Brand/).length).toBeGreaterThan(0)
  expect(within(performance).getByText('Approved book identity')).toBeInTheDocument()
})

it.each([true,false])('shows independently verified or incomplete performance evidence: %s', async matched => {
  const f = scopedFixture(matched, false)
  if (!matched) Object.assign(f.runs[0].metadata, { work: { ...scopedFixture().runs[0].metadata, score: null, families: 0, rows: [], records: {} }, performance: { matched: true, rows: [], records: {}, local: { narrators: [], duration_seconds: 10, dates: [] }, candidate: null } })
  f.runs[0].review_phase = 'audiobook'; f.runs[0].reviews.metadata.approved = true; f.runs[0].reviews.audiobook.eligible = matched
  server.use(http.get('/api/ingest', () => HttpResponse.json(f)))
  setup(<IngestPage phase="audiobook" />)
  expect(await screen.findByRole('heading', { name: matched ? 'Audiobook identification · checks passed' : 'Audiobook identification · review required' })).toBeInTheDocument()
  expect(screen.queryByText('Catalogue candidate · not applied to this performance')).toBeNull()
})

it.each([.4795757617429185,null])('shows runtime comparison as a readable percentage or unknown: %s', async value => {
  const f = scopedFixture() as unknown as import('../../src/api').IngestDashboard
  f.runs[0].review_phase = 'audiobook'; f.runs[0].reviews.metadata.approved = true
  f.runs[0].reviews.audiobook.checks = [{ metric: 'recording.runtime_error', value, threshold: { max: .02 }, passed: false, reason: value == null ? 'missing measurement' : 'outside threshold' }]
  f.runs[0].metadata!.performance!.rows.push({ field: 'Runtime', value: 'unknown', source: 'No supporting source', note: '' })
  server.use(http.get('/api/ingest', () => HttpResponse.json(f)))
  setup(<IngestPage phase="audiobook" />)
  expect(await screen.findByText(value == null ? /Recording runtime comparison: unknown/ : /Recording runtime comparison: 48.0% difference/)).toBeInTheDocument()
  expect(screen.getByText('Catalogue runtime')).toBeInTheDocument()
})

it('starts background previews on opening quality and enables each ready sample', async () => {
  const f = qualityFixture()
  Object.assign(f.runs[0], { selection: { files: ['one.mp3'], reason: 'complete', ranking: [] } })
  let prepares = 0, polls = 0
  const samples = (status: string) => ({ samples: Array.from({ length: 5 }, (_, index) => ({ index, start_seconds: 60 + index * 600, duration_seconds: 30, status, url: `/api/ingest/runs/test/samples/${index}` })) })
  server.use(http.get('/api/ingest', () => HttpResponse.json(f)), http.post('/api/ingest/runs/:id/samples', () => { prepares++; return HttpResponse.json(samples('generating')) }), http.get('/api/ingest/runs/:id/samples', () => { polls++; return HttpResponse.json(samples('ready')) }))
  const { user } = setup(<IngestPage phase="quality" />)
  expect(await screen.findByRole('button', { name: 'Sample 3 · 0:21:00 · preparing…' })).toBeDisabled()
  await waitFor(() => expect(screen.getByRole('button', { name: 'Sample 3 · 0:21:00' })).toBeEnabled(), { timeout: 3000 })
  expect(prepares).toBe(1); expect(polls).toBe(1)
  expect(screen.getAllByRole('button', { name: /^Sample/ })).toHaveLength(5)
  await user.click(screen.getByRole('button', { name: 'Sample 3 · 0:21:00' }))
  const player = screen.getByLabelText('Listening sample 3')
  expect(player).toHaveAttribute('src', '/api/ingest/runs/test/samples/2')
  act(() => player.dispatchEvent(new Event('error')))
  expect(await screen.findByRole('alert')).toHaveTextContent('Select it again to retry')
  await user.click(screen.getByRole('button', { name: 'Sample 3 · 0:21:00' }))
  expect(screen.queryByRole('alert')).toBeNull()
})

it('can retry failed background previews and request errors', async () => {
  const f = qualityFixture()
  Object.assign(f.runs[0], { selection: { files: ['one.mp3'], reason: 'complete', ranking: [] } })
  server.use(http.get('/api/ingest', () => HttpResponse.json(f)), http.post('/api/ingest/runs/:id/samples', () => HttpResponse.json({ detail: 'Source changed' }, { status: 400 })))
  const { user } = setup(<IngestPage phase="quality" />)
  expect(await screen.findByRole('alert')).toHaveTextContent('Source changed')
  server.use(http.post('/api/ingest/runs/:id/samples', () => HttpResponse.json({ samples: [{ index: 0, start_seconds: 1, duration_seconds: 30, status: 'failed', url: '/sample' }] })))
  await user.click(screen.getByRole('button', { name: 'Retry listening samples' }))
  expect(await screen.findByRole('button', { name: 'Sample 1 · 0:00:01 · failed' })).toBeDisabled()
  expect(screen.getByRole('button', { name: 'Retry listening samples' })).toBeEnabled()
})

it('controls audiobook automation independently of book and quality approvals', async () => {
  const f = ingestFixture(); f.runs[0].review_phase = 'audiobook'; f.runs[0].reviews.metadata.approved = true
  let body: Record<string, unknown> = {}
  server.use(http.get('/api/ingest', () => HttpResponse.json(f)), http.put('/api/ingest/policy', async ({ request }) => {
    body = await request.json() as Record<string, unknown>
    f.policy.auto_approve_audiobook = Boolean(body.auto_approve_audiobook)
    return HttpResponse.json(f.policy)
  }))
  const { user } = setup(<IngestPage phase="audiobook" />)
  const toggle = await screen.findByRole('checkbox', { name: 'Automatically approve audiobooks that pass every identification check' })
  await user.click(toggle)
  await waitFor(() => expect(toggle).toBeChecked())
  expect(body).toMatchObject({ auto_approve_metadata: false, auto_approve_audiobook: true, auto_approve_quality: false, auto_approve: false, auto_process: false })
  expect(screen.queryByRole('button', { name: 'Approve book and identify audiobook' })).toBeNull()
})

it.each([50,0])('returns to an accessible review page when approval shrinks the queue to %s', async remaining => {
  const f = ingestFixture(); let approved = false; let lastOffset = -1
  server.use(http.get('/api/ingest', ({ request }) => {
    lastOffset = Number(new URL(request.url).searchParams.get('offset') || 0)
    const total = approved ? remaining : 51
    const runs = lastOffset >= total ? [] : [{ ...f.runs[0], title: lastOffset ? 'Last book' : 'Still awaiting' }]
    return HttpResponse.json({ ...f, total, offset: lastOffset, runs })
  }), http.post('/api/ingest/runs/:id/approve/metadata', () => { approved = true; return HttpResponse.json({ changed: true }) }))
  const { user } = setup(<IngestPage />)
  await user.click(await screen.findByRole('button', { name: 'Next page' }))
  expect(await screen.findByText('Last book · review')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Approve book and identify audiobook' }))
  await waitFor(() => expect(lastOffset).toBe(0))
  if (remaining) expect(await screen.findByText('Still awaiting · review')).toBeInTheDocument()
  else expect(await screen.findByText('No books awaiting review.')).toBeInTheDocument()
})
