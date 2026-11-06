import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import MatchReviewPage from '../../src/components/MatchReviewPage'
import App from '../../src/App'
import { setup, screen, within, waitFor } from '../utils/render'
import { server, matchReviewFixture } from '../utils/server'

beforeEach(() => { vi.spyOn(window, 'scrollTo').mockImplementation(() => {}) })
afterEach(() => vi.restoreAllMocks())

const listing = (rows = [matchReviewFixture()]) => ({ rows, total: rows.length, offset: 0, limit: 10 })

describe('quick match review', () => {
  it('compares local and proposed identity and loads all evidence only when expanded', async () => {
    let detailCalls = 0
    server.use(http.get('/api/match-reviews/:id', () => { detailCalls++; return HttpResponse.json(matchReviewFixture()) }))
    const { user } = setup(<MatchReviewPage mode="shortlist" />)
    expect(await screen.findByRole('heading', { name: 'Dune' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Local identity' })).toHaveTextContent('Reader')
    expect(screen.getByRole('region', { name: 'Proposed match' })).toHaveTextContent('1965')
    expect(screen.getByText(/model score 0.970/)).toBeInTheDocument()
    expect(detailCalls).toBe(0)
    await user.click(screen.getByText('All evidence and alternatives'))
    expect(await screen.findByText('Desert planet.')).toBeInTheDocument()
    expect(screen.getByText('0 · 0 ratings')).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Recording evidence' })).toHaveTextContent('Recording not approved')
    expect(screen.getByRole('region', { name: 'Source metadata' })).toHaveTextContent('album')
    await user.click(screen.getByText('All 4 source files'))
    expect(screen.getByRole('region', { name: 'Source metadata' })).toHaveTextContent('03.mp3')
    await user.click(screen.getByText(/Open Library · 1 results/))
    await user.click(screen.getByText('All catalogue fields'))
    expect(within(screen.getByRole('region', { name: 'Catalogue alternatives' })).getAllByRole('link')[0]).toHaveAttribute('href', 'https://openlibrary.org/works/OL1W')
  })

  it('accepts the exact displayed snapshot, retains it and reports separate recording work', async () => {
    let rows = [matchReviewFixture()], received: unknown
    server.use(http.get('/api/match-reviews', () => HttpResponse.json(listing(rows))), http.post('/api/match-reviews/:id/accept', async ({ request, params }) => {
      received = { id: params.id, ...await request.json() as object }; rows = []; return HttpResponse.json({ changed: true })
    }))
    const { user } = setup(<MatchReviewPage mode="shortlist" />)
    await user.click(await screen.findByRole('button', { name: 'Approve' }))
    await screen.findByText(/book match accepted. Recording identification starts when you press Next/)
    expect(received).toEqual({ id: 'review1', basis: 'a'.repeat(64), defer_continuation: true })
    expect(screen.getByRole('button', { name: 'Approve' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Reject' })).toBeEnabled()
    await user.click(screen.getByRole('button', { name: 'Next' }))
    expect(await screen.findByText(/No current model suggestions/)).toBeInTheDocument()
  })

  it('withdraws automatic consent and explains the manual hold', async () => {
    let rows = [matchReviewFixture('automatic')]
    server.use(http.get('/api/match-reviews', () => HttpResponse.json(listing(rows))), http.post('/api/match-reviews/:id/reject', () => { rows = []; return HttpResponse.json({ changed: true }) }))
    const { user } = setup(<MatchReviewPage mode="automatic" />)
    expect(await screen.findByRole('region', { name: 'Approved match' })).toHaveTextContent('Dune')
    await user.click(screen.getByRole('button', { name: 'Reject' }))
    expect(await screen.findByRole('status')).toHaveTextContent('Automatic reapproval is held for manual review')
    expect(screen.getByRole('button', { name: 'Reject' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Approve' })).toBeEnabled()
    await user.click(screen.getByRole('button', { name: 'Next' }))
    expect(await screen.findByText('No automatic approvals to review.')).toBeInTheDocument()
  })

  it('shows failed checks and preserves the card after stale-decision errors', async () => {
    const row = matchReviewFixture(); row.checks = [{ metric: 'work.conflicts', value: 1, threshold: { max: 0 }, passed: false, reason: 'Original years conflict' }]
    server.use(http.get('/api/match-reviews', () => HttpResponse.json(listing([row]))), http.post('/api/match-reviews/:id/accept', () => HttpResponse.json({ detail: 'This review changed. Refresh before deciding.' }, { status: 400 })))
    const { user } = setup(<MatchReviewPage mode="shortlist" />)
    expect(await screen.findByLabelText('Review warnings')).toHaveTextContent('Original years conflict')
    await user.click(screen.getByRole('button', { name: 'Approve' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('This review changed')
    expect(screen.getByRole('button', { name: 'Approve' })).toBeEnabled()
    expect(screen.getByRole('heading', { name: 'Dune' })).toBeInTheDocument()
  })

  it('supports search, pagination, refresh and empty pages', async () => {
    const requests: string[] = []
    server.use(http.get('/api/match-reviews', ({ request }) => {
      const url = new URL(request.url); requests.push(url.search)
      return HttpResponse.json({ rows: url.searchParams.get('q') ? [] : Array.from({ length: Number(url.searchParams.get('offset')) ? 1 : 10 }, (_, i) => ({ ...matchReviewFixture(), id: `review${i}`, book_id: i + 1 })), total: url.searchParams.get('q') ? 0 : 11, offset: Number(url.searchParams.get('offset')), limit: 10 })
    }))
    const { user } = setup(<MatchReviewPage mode="shortlist" />)
    await screen.findByText('1–10 of 11 reviews')
    await user.click(screen.getByRole('button', { name: 'Next' }))
    expect(await screen.findByText('11–11 of 11 reviews')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Previous' }))
    await screen.findByText('1–10 of 11 reviews')
    await user.type(screen.getByRole('textbox', { name: 'Search reviews' }), 'missing')
    expect(await screen.findByText('0 reviews')).toBeInTheDocument()
    expect(requests.some(q => q.includes('q=missing'))).toBe(true)
    await user.click(screen.getByRole('button', { name: 'Refresh' }))
  })

  it('reports list/detail failures, disables active work and renders missing values safely', async () => {
    const row = matchReviewFixture(); row.blocked = 'Wait for the active job to finish.'; row.complete = false; row.cover = false; row.local.author = ''; row.local.locked = ['author']; row.source.hours = 0; row.suggestion!.candidate.original_year = null; row.suggestion!.candidate.publisher = ''; row.suggestion!.candidate.url = 'javascript:alert(1)'
    server.use(http.get('/api/match-reviews', () => HttpResponse.json(listing([row]))), http.get('/api/match-reviews/:id', () => HttpResponse.json({ detail: 'Evidence unavailable' }, { status: 400 })))
    const { user, unmount } = setup(<MatchReviewPage mode="shortlist" />)
    expect(await screen.findByRole('button', { name: 'Approve' })).toBeDisabled()
    expect(screen.getAllByText('Unknown').length).toBeGreaterThan(0)
    expect(screen.getByText('Manually locked: author')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Open Library' })).toBeNull()
    await user.click(screen.getByText('All evidence and alternatives'))
    expect(await screen.findByRole('alert')).toHaveTextContent('Evidence unavailable')
    unmount()
    server.use(http.get('/api/match-reviews', () => HttpResponse.json({ detail: 'Review index unavailable' }, { status: 500 })))
    setup(<MatchReviewPage mode="automatic" />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Review index unavailable')
  })

  it('routes both review pages from the main navigation', async () => {
    window.location.hash = '#/automatic-approvals'
    const { user } = setup(<App />)
    expect(await screen.findByRole('heading', { name: 'Automatic approvals' })).toBeInTheDocument()
    await user.click(screen.getByRole('link', { name: 'Match shortlist' }))
    expect(await screen.findByRole('heading', { name: 'Match shortlist' })).toBeInTheDocument()
  })
})


it('expands the original approval record and saved source assertions', async () => {
  const row = matchReviewFixture('automatic')
  row.approval!.checks = [{ metric: 'identity.title_known', value: 1, passed: true, reason: '', threshold: { min: 1 } }]
  row.evidence!.records.hardcover.genres = ['Fiction']
  row.evidence!.records.hardcover.contributors = [{ name: 'Reader', role: 'narrator' }]
  row.match.rows[0].note = 'Known original work'
  server.use(http.get('/api/match-reviews', () => HttpResponse.json(listing([row]))), http.get('/api/match-reviews/:id', () => HttpResponse.json(row)))
  const { user } = setup(<MatchReviewPage mode="automatic" />)
  expect(await screen.findByText('Supporting sources: Hardcover + Open Library')).toBeInTheDocument()
  await user.click(screen.getByText('All evidence and alternatives'))
  await user.click(await screen.findByText('Approval record'))
  expect(screen.getByText(/Title identified: passed/)).toBeInTheDocument()
  await user.click(screen.getByText('Hardcover assertions'))
  expect(screen.getByText('Fiction')).toBeInTheDocument()
  expect(screen.getByText('name: Reader · role: narrator')).toBeInTheDocument()
  expect(within(screen.getByRole('region', { name: 'Book evidence' })).getByText(/Known original work/)).toBeInTheDocument()
})

it('firmly approves an automatic match and keeps it out of the review queue on refresh', async () => {
  let rows = [matchReviewFixture('automatic')], confirmed = false
  server.use(http.get('/api/match-reviews', () => HttpResponse.json(listing(rows))), http.post('/api/match-reviews/:id/confirm', async ({ request }) => {
    expect(await request.json()).toEqual({ basis: 'a'.repeat(64) }); confirmed = true; rows = []; return HttpResponse.json({ changed: true, confirmed: true })
  }))
  const { user } = setup(<MatchReviewPage mode="automatic" />)
  await screen.findByRole('button', { name: 'Approve' })
  expect(screen.getByRole('button', { name: 'Reject' })).toBeEnabled()
  await user.click(screen.getByRole('button', { name: 'Approve' }))
  expect(await screen.findByRole('status')).toHaveTextContent('approval confirmed.')
  expect(confirmed).toBe(true)
  expect(screen.getByRole('button', { name: 'Approve' })).toBeDisabled()
  expect(screen.getByRole('button', { name: 'Reject' })).toBeEnabled()
  await user.click(screen.getByRole('button', { name: 'Refresh' }))
  expect(screen.queryByRole('button', { name: 'Approve' })).toBeNull()
})

it('rejects a proposed model match without accepting it', async () => {
  let rows = [matchReviewFixture()], accepted = false, rejected = false
  server.use(http.get('/api/match-reviews', () => HttpResponse.json(listing(rows))), http.post('/api/match-reviews/:id/reject', () => {
    rejected = true; rows = []; return HttpResponse.json({ changed: true, rejected: true })
  }), http.post('/api/match-reviews/:id/accept', () => { accepted = true; return HttpResponse.json({ changed: true }) }))
  const { user } = setup(<MatchReviewPage mode="shortlist" />)
  await user.click(await screen.findByRole('button', { name: 'Reject' }))
  expect(await screen.findByRole('status')).toHaveTextContent('match rejected. Automatic reapproval is held for manual review')
  expect(rejected).toBe(true); expect(accepted).toBe(false)
  expect(screen.getByRole('button', { name: 'Reject' })).toBeDisabled()
  expect(screen.getByRole('button', { name: 'Approve' })).toBeEnabled()
})

function reviewRows(count: number, mode: 'automatic' | 'shortlist' = 'automatic') {
  return Array.from({ length: count }, (_, i) => ({ ...matchReviewFixture(mode), id: `batch-${i + 1}`, book_id: i + 1, local: { ...matchReviewFixture(mode).local, title: `Book ${i + 1}` } }))
}


it('ignores obsolete search preloads and retries a failed preload when advancing', async () => {
  const original = reviewRows(11)
  let speculative = true, oldRelease: () => void = () => {}
  const oldSearch = new Promise<void>(resolve => { oldRelease = resolve })
  server.use(http.get('/api/match-reviews', async ({ request }) => {
    const url = new URL(request.url), q = url.searchParams.get('q'), after = url.searchParams.get('after')
    if (q === 'old') { await oldSearch; return HttpResponse.json({ rows: [original[0]], total: 1, offset: 0, limit: 10 }) }
    if (q === 'new') return HttpResponse.json({ rows: [original[10]], total: 1, offset: 0, limit: 10 })
    if (after && speculative) { speculative = false; return HttpResponse.json({ detail: 'Preload failed' }, { status: 500 }) }
    return HttpResponse.json({ rows: after ? original.slice(10) : original.slice(0, 10), total: 11, offset: Number(url.searchParams.get('offset')), limit: 10 })
  }))
  const { user } = setup(<MatchReviewPage mode="automatic" />)
  await screen.findByRole('article', { name: 'Review Book 1' })
  await waitFor(() => expect(speculative).toBe(false))
  expect(screen.queryByRole('alert')).toBeNull()
  await user.click(screen.getByRole('button', { name: 'Next' }))
  expect(await screen.findByRole('article', { name: 'Review Book 11' })).toBeInTheDocument()
  await user.type(screen.getByRole('textbox', { name: 'Search reviews' }), 'old')
  await user.clear(screen.getByRole('textbox', { name: 'Search reviews' }))
  await user.type(screen.getByRole('textbox', { name: 'Search reviews' }), 'new')
  expect(await screen.findByRole('article', { name: 'Review Book 11' })).toBeInTheDocument()
  oldRelease()
  await waitFor(() => expect(screen.queryByRole('article', { name: 'Review Book 1' })).toBeNull())
})

it('recovers when concurrent reviews empty a prefetched page or make its offset obsolete', async () => {
  let reads = 0, empty = false
  const original = reviewRows(20)
  server.use(http.get('/api/match-reviews', ({ request }) => {
    const url = new URL(request.url), after = url.searchParams.get('after'); reads++
    if (after) return HttpResponse.json({ rows: [], total: empty ? 20 : 5, offset: 10, limit: 10 })
    return HttpResponse.json({ rows: original.slice(0, reads === 1 ? 10 : 5), total: reads === 1 ? 20 : 5, offset: 0, limit: 10 })
  }))
  const { user } = setup(<MatchReviewPage mode="automatic" />)
  await screen.findByRole('article', { name: 'Review Book 1' })
  await waitFor(() => expect(reads).toBe(2))
  await user.click(screen.getByRole('button', { name: 'Next' }))
  expect(await screen.findByText('1–5 of 5 reviews')).toBeInTheDocument()
  empty = true; reads = 0
  await user.click(screen.getByRole('button', { name: 'Refresh' }))
  await screen.findByText('1–10 of 20 reviews')
  await waitFor(() => expect(reads).toBe(2))
  await user.click(screen.getByRole('button', { name: 'Next' }))
  expect(await screen.findByText('1–5 of 5 reviews')).toBeInTheDocument()
})


it.each(['automatic', 'shortlist'] as const)('keeps saved %s cards in place and fills only their slots on Next', async mode => {
  const original = reviewRows(23, mode)
  let rows = [...original]
  const cursors: (string | null)[] = []
  server.use(http.get('/api/match-reviews', ({ request }) => {
    const url = new URL(request.url), after = url.searchParams.get('after'); cursors.push(after)
    const eligible = after ? original.slice(original.findIndex(row => row.id === after) + 1).filter(row => rows.includes(row)) : rows
    return HttpResponse.json({ rows: eligible.slice(0, 10), total: rows.length, offset: 0, limit: 10 })
  }), http.post('/api/match-reviews/:id/:action', ({ params }) => {
    rows = rows.filter(row => row.id !== params.id)
    return HttpResponse.json({ changed: true, basis: 'b'.repeat(64) })
  }))
  const { user } = setup(<MatchReviewPage mode={mode} />)
  await screen.findByRole('article', { name: 'Review Book 1' })
  await waitFor(() => expect(cursors).toContain('batch-10'))
  const originalCards = screen.getAllByRole('article')
  for (const i of [1, 4, 7]) {
    const card = screen.getByRole('article', { name: `Review Book ${i}` })
    await user.click(within(card).getByRole('button', { name: 'Approve' }))
    await waitFor(() => expect(within(card).getByRole('button', { name: 'Approve' })).toBeDisabled())
    expect(within(card).getByRole('button', { name: 'Reject' })).toBeEnabled()
  }
  expect(screen.getAllByRole('article')).toEqual(originalCards)
  expect(window.scrollTo).not.toHaveBeenCalled()
  await user.click(screen.getByRole('button', { name: 'Next' }))
  await screen.findByRole('article', { name: 'Review Book 13' })
  expect(screen.getAllByRole('article').map(card => card.getAttribute('aria-label'))).toEqual([2, 3, 5, 6, 8, 9, 10, 11, 12, 13].map(i => `Review Book ${i}`))
  expect(screen.queryByRole('article', { name: 'Review Book 14' })).toBeNull()
  await waitFor(() => expect(cursors).toContain('batch-13'))
  for (const i of [2, 3, 5, 6, 8, 9, 10, 11, 12, 13]) {
    const card = screen.getByRole('article', { name: `Review Book ${i}` })
    await user.click(within(card).getByRole('button', { name: 'Reject' }))
    await waitFor(() => expect(within(card).getByRole('button', { name: 'Reject' })).toBeDisabled())
  }
  expect(screen.getAllByRole('article')).toHaveLength(10)
  await user.click(screen.getByRole('button', { name: 'Next' }))
  await screen.findByRole('article', { name: 'Review Book 14' })
  expect(screen.getAllByRole('article')).toHaveLength(10)
  expect(screen.getByRole('article', { name: 'Review Book 23' })).toBeInTheDocument()
})

it.each(['automatic', 'shortlist'] as const)('uses current tokens to change saved %s decisions in either direction', async mode => {
  const row = matchReviewFixture(mode), calls: { action: string; basis: string }[] = []
  let count = 0
  server.use(http.get('/api/match-reviews', () => HttpResponse.json(listing([row]))), http.post('/api/match-reviews/:id/:action', async ({ request, params }) => {
    calls.push({ action: String(params.action), ...await request.json() as { basis: string } })
    return HttpResponse.json({ changed: true, basis: ['b', 'c', 'd'][count++].repeat(64) })
  }))
  const { user } = setup(<MatchReviewPage mode={mode} />)
  const card = await screen.findByRole('article', { name: `Review ${row.local.title}` })
  for (const action of ['Approve', 'Reject', 'Approve']) {
    await user.click(within(card).getByRole('button', { name: action }))
    await waitFor(() => expect(within(card).getByRole('button', { name: action })).toBeDisabled())
    expect(within(card).getByRole('button', { name: action === 'Approve' ? 'Reject' : 'Approve' })).toBeEnabled()
    expect(screen.getByRole('article', { name: `Review ${row.local.title}` })).toBe(card)
  }
  expect(calls).toEqual([{ action: mode === 'automatic' ? 'confirm' : 'accept', basis: 'a'.repeat(64), ...(mode === 'shortlist' ? { defer_continuation: true } : {}) }, { action: 'reject', basis: 'b'.repeat(64), ...(mode === 'shortlist' ? { defer_continuation: true } : {}) }, { action: 'restore', basis: 'c'.repeat(64), ...(mode === 'shortlist' ? { defer_continuation: true } : {}) }])
})

it('keeps saving cards and failed decisions while Next replaces only successfully saved cards', async () => {
  const original = reviewRows(12)
  const releases = new Map<string, () => void>()
  server.use(http.get('/api/match-reviews', ({ request }) => HttpResponse.json({ rows: new URL(request.url).searchParams.has('after') ? original.slice(10) : original.slice(0, 10), total: 12, offset: 0, limit: 10 })), http.post('/api/match-reviews/:id/:action', async ({ params }) => {
    await new Promise<void>(resolve => { releases.set(String(params.id), resolve) })
    return params.id === 'batch-2' ? HttpResponse.json({ detail: 'Review changed' }, { status: 400 }) : HttpResponse.json({ changed: true })
  }))
  const { user } = setup(<MatchReviewPage mode="automatic" />)
  await screen.findByRole('article', { name: 'Review Book 1' })
  for (const i of [1, 2, 3]) await user.click(within(screen.getByRole('article', { name: `Review Book ${i}` })).getByRole('button', { name: 'Approve' }))
  await waitFor(() => expect(releases.size).toBe(3))
  releases.get('batch-1')!(); releases.get('batch-2')!()
  expect(await screen.findByRole('alert')).toHaveTextContent('Book 2: Review changed')
  await user.click(screen.getByRole('button', { name: 'Next' }))
  await screen.findByRole('article', { name: 'Review Book 11' })
  expect(screen.queryByRole('article', { name: 'Review Book 1' })).toBeNull()
  expect(screen.getAllByRole('article')).toHaveLength(10)
  expect(within(screen.getByRole('article', { name: 'Review Book 2' })).getByRole('button', { name: 'Approve' })).toBeEnabled()
  const pending = screen.getByRole('article', { name: 'Review Book 3' })
  expect(within(pending).getByRole('button', { name: 'Saving…' })).toBeDisabled()
  releases.get('batch-3')!()
  await waitFor(() => expect(within(pending).getByRole('button', { name: 'Approve' })).toBeDisabled())
  expect(screen.getByRole('article', { name: 'Review Book 3' })).toBe(pending)
})

it('shows a shorter final batch and clears it only when Next is pressed', async () => {
  const rows = reviewRows(2)
  server.use(http.get('/api/match-reviews', () => HttpResponse.json(listing(rows))), http.post('/api/match-reviews/:id/:action', () => HttpResponse.json({ changed: true })))
  const { user } = setup(<MatchReviewPage mode="automatic" />)
  for (const i of [1, 2]) {
    const card = await screen.findByRole('article', { name: `Review Book ${i}` })
    await user.click(within(card).getByRole('button', { name: 'Approve' }))
    await waitFor(() => expect(within(card).getByRole('button', { name: 'Approve' })).toBeDisabled())
  }
  expect(screen.getAllByRole('article')).toHaveLength(2)
  expect(screen.getByRole('button', { name: 'Next' })).toBeEnabled()
  await user.click(screen.getByRole('button', { name: 'Next' }))
  expect(await screen.findByText('No automatic approvals to review.')).toBeInTheDocument()
})

it('starts shortlist recording work only on Next and retains the card if queueing fails', async () => {
  const row = matchReviewFixture(), resumes: string[] = []
  let fail = true, jobPolls = 0
  server.use(http.get('/api/match-reviews', () => HttpResponse.json(listing([row]))), http.post('/api/match-reviews/:id/accept', async ({ request }) => {
    expect(await request.json()).toEqual({ basis: 'a'.repeat(64), defer_continuation: true })
    return HttpResponse.json({ changed: true, basis: 'b'.repeat(64) })
  }), http.post('/api/ingest/runs/:id/resume', ({ params }) => {
    resumes.push(String(params.id))
    return fail ? HttpResponse.json({ detail: 'Queue unavailable' }, { status: 503 }) : HttpResponse.json({ job_id: 'recording' }, { status: 202 })
  }), http.get('/api/jobs/recording', () => { jobPolls++; return HttpResponse.json({ state: 'running' }) }))
  const { user } = setup(<MatchReviewPage mode="shortlist" />)
  await user.click(await screen.findByRole('button', { name: 'Approve' }))
  await waitFor(() => expect(screen.getByRole('button', { name: 'Approve' })).toBeDisabled())
  expect(resumes).toEqual([])
  await user.click(screen.getByRole('button', { name: 'Next' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Queue unavailable')
  expect(screen.getByRole('button', { name: 'Reject' })).toBeEnabled()
  fail = false
  await user.click(screen.getByRole('button', { name: 'Next' }))
  expect(await screen.findByText(/No current model suggestions/)).toBeInTheDocument()
  expect(resumes).toEqual(['review1', 'review1'])
  expect(jobPolls).toBe(0)
})

it.each(['automatic', 'shortlist'] as const)('retains a full %s batch while its first decision is still saving', async mode => {
  const original = reviewRows(12, mode)
  let release: (() => void) | undefined
  const continued: string[] = []
  server.use(http.get('/api/match-reviews', ({ request }) => HttpResponse.json({ rows: new URL(request.url).searchParams.has('after') ? original.slice(10) : original.slice(0, 10), total: 12, offset: 0, limit: 10 })), http.post('/api/match-reviews/:id/:action', async () => {
    await new Promise<void>(resolve => { release = resolve })
    return HttpResponse.json({ changed: true, basis: 'b'.repeat(64) })
  }), http.post('/api/ingest/runs/:id/resume', ({ params }) => { continued.push(String(params.id)); return HttpResponse.json({ queued: true }) }))
  const { user } = setup(<MatchReviewPage mode={mode} />)
  const first = await screen.findByRole('article', { name: 'Review Book 1' })
  const batch = screen.getAllByRole('article')
  await user.click(within(first).getByRole('button', { name: 'Approve' }))
  await waitFor(() => expect(release).toBeDefined())
  await user.click(screen.getByRole('button', { name: 'Next' }))
  expect(screen.getAllByRole('article')).toEqual(batch)
  expect(within(first).getByRole('button', { name: 'Saving…' })).toBeDisabled()
  expect(within(screen.getByRole('article', { name: 'Review Book 2' })).getByRole('button', { name: 'Approve' })).toBeEnabled()
  release!()
  await waitFor(() => expect(within(first).getByRole('button', { name: 'Approve' })).toBeDisabled())
  await user.click(screen.getByRole('button', { name: 'Next' }))
  await screen.findByRole('article', { name: 'Review Book 11' })
  expect(screen.queryByRole('article', { name: 'Review Book 1' })).toBeNull()
  expect(continued).toEqual(mode === 'shortlist' ? ['batch-1'] : [])
})
