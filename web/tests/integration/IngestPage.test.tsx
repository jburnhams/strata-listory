import { afterEach, describe, expect, it, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import IngestPage from '../../src/components/IngestPage'
import App from '../../src/App'
import { setup, screen, waitFor, act } from '../utils/render'
import { ingestFixture, server } from '../utils/server'
import { href, parse } from '../../src/route'

afterEach(() => vi.restoreAllMocks())

describe('staged ingest', () => {
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
