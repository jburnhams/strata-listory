import { describe, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'
import ProcessPage from '../../src/components/ProcessPage'
import { setup, screen, within } from '../utils/render'
import { mock, server } from '../utils/server'

describe('ProcessPage', () => {
  it('shows retained date notes alongside the plan', async () => {
    Object.assign(mock.queue[0].summary!, { advisories: ['Hardcover lists a different work release year; original publication is 2017.'] })
    setup(<ProcessPage mode="proposed" />)
    expect(await screen.findByText('Note: Hardcover lists a different work release year; original publication is 2017.')).toBeInTheDocument()
  })
  it('shows proposals with their plans, lets you approve some, reject one and process the approved', async () => {
    const { user, rerender } = setup(<ProcessPage mode="proposed" />)
    expect(await screen.findByRole('link', { name: 'Dune' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'To review (2)' })).toBeInTheDocument()
    expect(screen.getAllByText(/8.2 h · 18 chapters \(from Audible chapter list\) · 17× V5 · 1× V3/)).toHaveLength(2)
    expect(screen.getAllByText(/ends 2.9 dB below the target/)).toHaveLength(1)         // only Dune has a warning
    const meta = within(screen.getByLabelText('Metadata for Dune'))
    expect(meta.getByText('Dune')).toBeInTheDocument()
    expect(meta.getByText(/audible \+ audiobookdb match \(confirmed\)/)).toBeInTheDocument()
    expect(meta.getByText('· Audible metadata file')).toBeInTheDocument()          // the year's source
    expect(meta.getByText(/the Audible metadata file says: Frank Herbert/)).toBeInTheDocument()          // a disagreement between the match and the file is pointed out
    expect(screen.queryByRole('button', { name: /Process approved/ })).toBeNull()
    await user.click(screen.getAllByText('Chapters and output')[0])
    expect(screen.getAllByText('/home/u/audiobooks/A/Dune/')).toHaveLength(2)
    expect(screen.getAllByText('29.4 min · V5')[0]).toBeInTheDocument()
    expect(screen.getAllByText('21.4 min · kept as is')[0]).toBeInTheDocument()

    await user.click(screen.getByLabelText('Select Dune'))
    await user.click(screen.getByRole('button', { name: 'Approve selected (1)' }))
    expect(await screen.findByRole('tab', { name: 'To review (1)' })).toBeInTheDocument()

    await user.click(screen.getByLabelText('Select Emma'))
    await user.click(screen.getByRole('button', { name: 'Reject selected' }))
    expect(await screen.findByRole('tab', { name: 'Rejected (1)' })).toBeInTheDocument()

    await user.click(screen.getByRole('tab', { name: 'Rejected (1)' }))
    await user.click(screen.getByLabelText('Select Emma'))
    await user.click(screen.getByRole('button', { name: 'Plan again' }))
    expect(await screen.findByText('No operations in this view.')).toBeInTheDocument()

    rerender(<ProcessPage key="operations" />)
    await user.click(await screen.findByRole('button', { name: 'Process approved (1)' }))
    expect(await screen.findByRole('tab', { name: 'Done (1)' })).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: 'Done (1)' }))
    expect(screen.getByRole('link', { name: 'Dune' })).toBeInTheDocument()
  })

  it('finds more candidates, selects all, and reports errors', async () => {
    const { user } = setup(<ProcessPage mode="proposed" />)
    await screen.findByRole('link', { name: 'Dune' })
    await user.click(screen.getByRole('button', { name: 'Select all' }))
    expect(screen.getByRole('button', { name: 'Approve selected (2)' })).toBeInTheDocument()
    server.use(http.post('/api/process/approve', () => HttpResponse.json({ detail: 'cannot approve' }, { status: 500 })))
    await user.click(screen.getByRole('button', { name: 'Approve selected (2)' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('cannot approve')
  })

  it('shows results, failures and an empty queue', async () => {
    mock.queue = [
      { book_id: 3, title: 'Finished', author: 'A', state: 'done', error: '', updated: '2026-10-30T12:00:00+00:00', summary: null },
      { book_id: 4, title: 'Broken', author: 'B', state: 'failed', error: 'its files are not available', updated: '2026-10-30T12:00:00+00:00', summary: null },
    ]
    const { user } = setup(<ProcessPage />)
    expect(await screen.findByText('its files are not available')).toBeInTheDocument(); expect(screen.queryByRole('link', { name: 'Finished' })).toBeNull()
    await user.click(screen.getByRole('tab', { name: 'Done (1)' }))
    expect(screen.getByText(/Finished: 286 MB → 134 MB · -18.1 LUFS · peak -1.6 dBTP/)).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: 'Failed (1)' }))
    expect(screen.getByText('its files are not available')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Plan these again' }))
    expect(await within(screen.getByRole('tablist')).findByRole('tab', { name: 'Failed (0)' })).toBeInTheDocument()
  })
})


describe('ProcessPage progress', () => {
  it('keeps refreshing while something is running and shows the result when it finishes', async () => {
    mock.queue = [{ book_id: 5, title: 'Running book', author: 'R', state: 'running', error: '', updated: '2026-10-30T12:00:00+00:00', summary: null }]
    const { user } = setup(<ProcessPage pollMs={30} />)
    expect(await screen.findByText(/Working… run/)).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: 'Queued / running (1)' }))
    expect(screen.getByText('Running book')).toBeInTheDocument()
    mock.queue = [{ ...mock.queue[0], state: 'done' }]
    expect(await screen.findByRole('tab', { name: 'Done (1)' })).toBeInTheDocument()
    expect(screen.queryByText(/Working…/)).toBeNull()
  })

  it('refuses to run when nothing is approved', async () => {
    mock.queue = [{ book_id: 6, title: 'Pending', author: 'P', state: 'approved', error: '', updated: '2026-10-30T12:00:00+00:00', summary: null }]
    const { user } = setup(<ProcessPage />)
    await screen.findByRole('tab', { name: 'Queued / running (1)' })
    mock.queue = []
    await user.click(screen.getByRole('button', { name: 'Process approved (1)' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('nothing is approved yet')
  })
})

it('shows ingest queues and failures by default and completed operations on request', async () => {
  let retried = 0
  server.use(http.get('/api/operations', ({ request }) => HttpResponse.json({ busy: false, rows: [
    { id: 'pending', book_id: 20, run_id: null, title: 'Next book', stage: 'Ingest analysis', state: 'queued', phase: null, error: '' },
    { id: 'active', book_id: 21, run_id: 'r1', title: 'Audio analysis', stage: 'quality', state: 'running', phase: 'quality', error: '' },
    { id: 'failed', book_id: 22, run_id: 'r2', title: 'Failed metadata', stage: 'providers', state: 'failed', phase: 'metadata', error: 'Provider unavailable' },
    { id: 'waiting', book_id: 23, run_id: 'r3', title: 'Metadata to approve', stage: 'Metadata approval', state: 'waiting', phase: 'metadata', error: '' },
    ...(new URL(request.url).searchParams.get('completed') === 'true' ? [{ id: 'complete', book_id: 24, run_id: 'r4', title: 'Earlier operation', stage: 'Processing', state: 'completed', phase: null, error: '' }] : []),
  ] })), http.post('/api/ingest/runs/:id/resume', () => { retried++; return HttpResponse.json({ queued: true }) }))
  const { user } = setup(<ProcessPage />)
  expect(await screen.findByText('Next book')).toBeInTheDocument()
  expect(screen.getByText('Audio analysis')).toBeInTheDocument()
  expect(screen.getByText('Provider unavailable')).toBeInTheDocument()
  expect(screen.queryByText('Earlier operation')).toBeNull()
  expect(screen.getByRole('link', { name: 'Review quality' })).toHaveAttribute('href', '#/quality')
  await user.click(screen.getByRole('button', { name: 'Retry analysis' }))
  expect(retried).toBe(1)
  await user.click(screen.getByRole('checkbox', { name: 'Show completed' }))
  expect(await screen.findByText('Earlier operation')).toBeInTheDocument()
  await user.click(screen.getByRole('tab', { name: 'Failed (1)' }))
  expect(screen.getByText('Failed metadata')).toBeInTheDocument()
  expect(screen.queryByText('Next book')).toBeNull()
  await user.click(screen.getByRole('tab', { name: 'Queued / running (2)' }))
  expect(screen.getByText('Next book')).toBeInTheDocument()
  expect(screen.queryByText('Failed metadata')).toBeNull()
})

it('separates book and audiobook publishers and dates in a frozen proposal', async () => {
  Object.assign(mock.queue[0].summary!, {
    book_metadata: [{ field: 'Publisher', value: 'Book Press', source: 'Hardcover', note: '' }, { field: 'Year', value: '1965', source: 'Open Library', note: '' }],
    audiobook_metadata: [{ field: 'Publisher', value: 'Audio Press', source: 'Audible', note: 'Recording publisher' }, { field: 'Release date', value: '2024-01-01', source: 'Audible', note: '' }],
  })
  setup(<ProcessPage mode="proposed" />)
  const book = await screen.findByRole('region', { name: 'Book metadata for Dune' })
  const audio = screen.getByRole('region', { name: 'Audiobook / performance metadata for Dune' })
  expect(within(book).getByText('Book Press')).toBeInTheDocument()
  expect(within(book).queryByText('Audio Press')).toBeNull()
  expect(within(book).getByText('Original publication year')).toBeInTheDocument()
  expect(within(audio).getByText('Audio Press')).toBeInTheDocument()
  expect(within(audio).getByText('2024-01-01')).toBeInTheDocument()
  expect(within(audio).getByText('Recording publisher')).toBeInTheDocument()
})

it('shows the live step, elapsed time, and activity log for active ingest', async () => {
  server.use(http.get('/api/operations', () => HttpResponse.json({ busy: true, rows: [{ id: 'active', book_id: 1, run_id: 'active', title: 'Inspecting book', stage: 'inventory', state: 'running', phase: 'metadata', error: '', current_step: 'Inspecting chapter 2.mp3', step_elapsed: 61, logs: [{ created: '2026-11-03T05:00:00Z', status: 'running', message: 'Source inventory: running' }, { created: '2026-11-03T05:01:00Z', status: 'progress', message: 'Hashed chapter 1.mp3' }] }] })))
  const { user } = setup(<ProcessPage />)
  expect(await screen.findByText(/Current step: Inspecting chapter 2.mp3 · 1m 1s/)).toBeInTheDocument()
  await user.click(screen.getByText('Activity log'))
  expect(screen.getByText(/Hashed chapter 1.mp3/)).toBeInTheDocument()
})

it('keeps failed jobs stopped until Retry job is clicked', async () => {
  let retries = 0
  server.use(http.get('/api/operations', () => HttpResponse.json({ busy: false, rows: retries ? [] : [
    { id: 'job-failed', job_id: 'failed', book_id: null, run_id: null, title: 'Source inspection', stage: 'system', state: 'failed', phase: null, error: 'Job exceeded its time limit' },
  ] })), http.post('/api/jobs/failed/retry', () => { retries++; return HttpResponse.json({ retried: true }) }))
  const { user } = setup(<ProcessPage />)
  expect(await screen.findByText('Job exceeded its time limit')).toBeInTheDocument()
  expect(retries).toBe(0)
  await user.click(screen.getByRole('button', { name: 'Retry job' }))
  expect(retries).toBe(1)
  expect(await screen.findByText('No operations in this view.')).toBeInTheDocument()
})
