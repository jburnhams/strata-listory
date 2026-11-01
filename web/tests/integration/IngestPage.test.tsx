import { afterEach, describe, expect, it, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import IngestPage from '../../src/components/IngestPage'
import App from '../../src/App'
import { setup, screen, waitFor, act } from '../utils/render'
import { ingestFixture, server } from '../utils/server'
import { href, parse } from '../../src/route'

afterEach(() => vi.restoreAllMocks())

describe('staged ingest', () => {
  it('flags continuous sound for listening without identifying it as noise', async () => {
    const f = ingestFixture()
    Object.assign(f.runs[0], { selection: { files: ['one.mp3'], reason: 'complete', ranking: [], continuous_sound: { flagged: true, sustained_samples: 2, valid_samples: 3, threshold_db: -8, method: 'relative-window-review-v1' } } })
    server.use(http.get('/api/ingest', () => HttpResponse.json(f)))
    setup(<IngestPage />)
    expect(await screen.findByText(/2 of 3 sampled passages/)).toHaveTextContent('radio play')
    expect(screen.getByText(/Listen before treating it as noise/)).toBeInTheDocument()
  })
  it('shows source format and attributed provenance clues', async () => {
    const f = ingestFixture()
    Object.assign(f.runs[0], { selection: { files: ['one.mp3'], reason: 'complete', ranking: [], provenance: { file_count: 1, codecs: ['mp3'], extensions: ['.mp3'], bitrates_kbps: [64], sample_rates_hz: [22050], recording_release_dates: [{ source: 'audible', release_date: '2010' }], embedded_dates: ['1990'], medium_clues: [{ source: 'file name', value: 'CD 01.mp3' }], medium_clues_truncated: false } } })
    server.use(http.get('/api/ingest', () => HttpResponse.json(f)))
    const { user } = setup(<IngestPage />)
    await user.click(await screen.findByText('Source format and provenance clues'))
    expect(screen.getByText(/64 kbps/)).toBeInTheDocument()
    expect(screen.getByText(/2010 \(audible\)/)).toBeInTheDocument()
    expect(screen.getByText('file name: CD 01.mp3')).toBeInTheDocument()
    expect(screen.getByText(/not proof of the original medium/)).toBeInTheDocument()
  })
  it('shows unknown provenance and a bitrate range', async () => {
    const f = ingestFixture()
    Object.assign(f.runs[0], { selection: { files: ['one.mp3'], reason: 'complete', ranking: [], provenance: { file_count: 1, codecs: [], extensions: [], bitrates_kbps: [32, 64, 96], sample_rates_hz: [], recording_release_dates: [], embedded_dates: [], medium_clues: [], medium_clues_truncated: false } } })
    server.use(http.get('/api/ingest', () => HttpResponse.json(f)))
    const { user } = setup(<IngestPage />)
    await user.click(await screen.findByText('Source format and provenance clues'))
    expect(screen.getByText(/32–96 kbps/)).toHaveTextContent('sample rate unknown')
    expect(screen.getByText('Original medium: unknown')).toBeInTheDocument()
    expect(screen.getByText('Recording release: unknown')).toBeInTheDocument()
  })
  it('shows quiet passage measurements without calling them a noise floor', async () => {
    const f = ingestFixture()
    Object.assign(f.runs[0], { selection: { files: ['one.mp3'], reason: 'complete', ranking: [], quiet_passages: { median_dbfs: -52.3, samples: [{ start_seconds: 10, duration_seconds: .5, rms_dbfs: -52.3, status: 'measured' }] } } })
    server.use(http.get('/api/ingest', () => HttpResponse.json(f)))
    const { user } = setup(<IngestPage />)
    expect(await screen.findByText(/median -52.3 dBFS/)).toBeInTheDocument()
    expect(screen.getByText(/not a calibrated noise floor/)).toBeInTheDocument()
    await user.click(screen.getByText('Sample positions'))
    expect(screen.getByText('10s: -52.3 dBFS (0.5s)')).toBeInTheDocument()
  })
  it('shows review indicators as estimates', async () => {
    const f = ingestFixture()
    Object.assign(f.runs[0], { selection: { files: ['one.mp3'], reason: 'complete', ranking: [], loudness_range: 5.3, acoustics: { medians: { quiet_rms_dbfs: -50, quiet_high_dbfs: -60, quiet_to_active_db: -30, active_low_to_presence_db: 8 }, samples: [] } } })
    server.use(http.get('/api/ingest', () => HttpResponse.json(f)))
    setup(<IngestPage />)
    expect(await screen.findByText(/Quiet high-band level: -60 dBFS/)).toHaveTextContent('Loudness range: 5.3 LU')
    expect(screen.getByText(/not a quality rating/)).toBeInTheDocument()
  })
  it('shows stage timings, unknown metrics and review thresholds', async () => {
    setup(<IngestPage />)
    expect(screen.getByText('Loading ingest…')).toBeInTheDocument()
    expect(await screen.findByText('Dune · review')).toBeInTheDocument()
    expect(screen.getByText(/Automatic approval disabled/)).toBeInTheDocument()
    expect(screen.getByText('unknown')).toBeInTheDocument()
    expect(screen.getByText('missing measurement')).toBeInTheDocument()
    expect(screen.getByText(/1.3s/)).toBeInTheDocument()
  })
  it('navigates to ingest from the app', async () => {
    expect(parse('#/ingest')).toEqual({ page: 'ingest' }); expect(href.ingest()).toBe('#/ingest')
    window.location.hash = '#/ingest'; setup(<App />)
    expect(await screen.findByText('Dune · review')).toBeInTheDocument()
  })
  it('shows empty and server-error states', async () => {
    server.use(http.get('/api/ingest', () => HttpResponse.json({ ...ingestFixture(), runs: [] })))
    const { user } = setup(<IngestPage />)
    expect(await screen.findByText('No ingest runs yet.')).toBeInTheDocument()
    server.use(http.get('/api/ingest', () => HttpResponse.json({ detail: 'offline' }, { status: 503 })))
    await user.click(screen.getByRole('button', { name: 'Refresh' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('offline')
  })
  it('validates IDs and queues offline ingest for selected books', async () => {
    let body: unknown
    server.use(http.post('/api/ingest/run', async ({ request }) => { body = await request.json(); return HttpResponse.json({ queued: true }) }))
    const { user } = setup(<IngestPage />); await screen.findByText('Dune · review')
    await user.type(screen.getByLabelText('Book IDs'), 'bad')
    await user.click(screen.getByRole('button', { name: 'Analyse' }))
    expect(screen.getByRole('alert')).toHaveTextContent('positive book IDs')
    await user.clear(screen.getByLabelText('Book IDs')); await user.type(screen.getByLabelText('Book IDs'), '1, 2')
    await user.click(screen.getByRole('checkbox')); await user.click(screen.getByRole('button', { name: 'Analyse' }))
    expect(await screen.findByRole('status')).toHaveTextContent('Ingest queued')
    expect(body).toEqual({ ids: [1, 2], online: false, limit: 5 })
    await user.clear(screen.getByLabelText('Book IDs')); await user.click(screen.getByRole('button', { name: 'Analyse' }))
    await waitFor(() => expect(body).toEqual({ ids: null, online: false, limit: 5 }))
  })
  it('evaluates saved metrics, proposes a plan, and displays preserved evidence', async () => {
    const { user } = setup(<IngestPage />); await screen.findByText('Dune · review')
    await user.click(screen.getByRole('button', { name: 'Evaluate thresholds' }))
    expect(await screen.findByRole('status')).toHaveTextContent('Saved metrics evaluated')
    await user.click(screen.getByRole('button', { name: 'Propose processing' }))
    expect(await screen.findByRole('status')).toHaveTextContent('manual approval')
    await user.click(screen.getByRole('button', { name: 'View evidence' }))
    expect(await screen.findByText(/"preserved"/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Close evidence' }))
    expect(screen.queryByText('Run evidence')).toBeNull()
  })
  it('resumes a failed stage and shows action errors', async () => {
    const f = ingestFixture(); f.runs[0].state = 'failed'; f.runs[0].title = ''; f.runs[0].stages[0].error = 'probe failed'; f.runs[0].current_evaluation.kind_allowed = false
    server.use(http.get('/api/ingest', () => HttpResponse.json(f)))
    const { user } = setup(<IngestPage />); await screen.findByText('Book 1 · failed')
    expect(screen.getByText('probe failed')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Resume' }))
    expect(await screen.findByRole('status')).toHaveTextContent('Resume queued')
    server.use(http.post('/api/ingest/runs/:id/resume', () => HttpResponse.json({ detail: 'changed input' }, { status: 400 })))
    await user.click(screen.getByRole('button', { name: 'Resume' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('changed input')
  })
  it('saves policy revisions and handles invalid JSON and rejected policies', async () => {
    const { user } = setup(<IngestPage />); await screen.findByText('Dune · review')
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
  const f = { ...ingestFixture(), runs: [{ ...ingestFixture().runs[0], selection: { files: ['Part01.mp3', 'Part02.mp3'], reason: 'Complete recording with better measured quality', ranking: [{ id: 'source1', reasons: { coverage: 1, true_peak: -1, band4k_relative_db: -20 } }, { id: 'source2', reasons: { coverage: .5, true_peak: null, band4k_relative_db: null } }] } }] }
  server.use(http.get('/api/ingest', () => HttpResponse.json(f)))
  setup(<IngestPage />)
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
