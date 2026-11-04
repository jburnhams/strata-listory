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

  it('accepts the exact displayed snapshot, removes it and reports separate recording work', async () => {
    let rows = [matchReviewFixture()], received: unknown
    server.use(http.get('/api/match-reviews', () => HttpResponse.json(listing(rows))), http.post('/api/match-reviews/:id/accept', async ({ request, params }) => {
      received = { id: params.id, ...await request.json() as object }; rows = []; return HttpResponse.json({ changed: true })
    }))
    const { user } = setup(<MatchReviewPage mode="shortlist" />)
    await user.click(await screen.findByRole('button', { name: 'Approve' }))
    await screen.findByText(/book match accepted. Recording identification is queued/)
    expect(received).toEqual({ id: 'review1', basis: 'a'.repeat(64) })
    expect(await screen.findByText(/No current model suggestions/)).toBeInTheDocument()
  })

  it('withdraws automatic consent and explains the manual hold', async () => {
    let rows = [matchReviewFixture('automatic')]
    server.use(http.get('/api/match-reviews', () => HttpResponse.json(listing(rows))), http.post('/api/match-reviews/:id/reject', () => { rows = []; return HttpResponse.json({ changed: true }) }))
    const { user } = setup(<MatchReviewPage mode="automatic" />)
    expect(await screen.findByRole('region', { name: 'Approved match' })).toHaveTextContent('Dune')
    await user.click(screen.getByRole('button', { name: 'Reject' }))
    expect(await screen.findByRole('status')).toHaveTextContent('Automatic reapproval is held for manual review')
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
  expect(await screen.findByRole('status')).toHaveTextContent('approval confirmed. Removed from this review queue')
  expect(confirmed).toBe(true)
  expect(await screen.findByText('No automatic approvals to review.')).toBeInTheDocument()
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
  expect(await screen.findByText(/No current model suggestions/)).toBeInTheDocument()
})

function reviewRows(count: number, mode: 'automatic' | 'shortlist' = 'automatic') {
  return Array.from({ length: count }, (_, i) => ({ ...matchReviewFixture(mode), id: `batch-${i + 1}`, book_id: i + 1, local: { ...matchReviewFixture(mode).local, title: `Book ${i + 1}` } }))
}

it.each(['automatic', 'shortlist'] as const)('preloads ten at a time and automatically advances %s batches without skips', async mode => {
  const original = reviewRows(21, mode)
  let rows = [...original]
  const requests: { offset: number; after: string | null }[] = [], decisions: string[] = []
  server.use(http.get('/api/match-reviews', ({ request }) => {
    const url = new URL(request.url), offset = Number(url.searchParams.get('offset')), after = url.searchParams.get('after')
    requests.push({ offset, after })
    const eligible = after ? original.slice(original.findIndex(r => r.id === after) + 1).filter(r => rows.some(remaining => remaining.id === r.id)) : rows.slice(offset)
    return HttpResponse.json({ rows: eligible.slice(0, 10), total: rows.length, offset, limit: 10 })
  }), http.post('/api/match-reviews/:id/:action', ({ params }) => {
    decisions.push(String(params.id)); rows = rows.filter(r => r.id !== params.id); return HttpResponse.json({ changed: true })
  }))
  const { user } = setup(<MatchReviewPage mode={mode} />)
  await screen.findByRole('article', { name: 'Review Book 1' })
  expect(screen.getAllByRole('article')).toHaveLength(10)
  await waitFor(() => expect(requests).toContainEqual({ offset: 10, after: 'batch-10' }))
  expect(screen.queryByRole('article', { name: 'Review Book 11' })).toBeNull()
  for (let i = 1; i <= 10; i++) {
    const card = screen.getByRole('article', { name: `Review Book ${i}` })
    await user.click(within(card).getByRole('button', { name: i % 2 ? 'Approve' : 'Reject' }))
    await waitFor(() => expect(screen.queryByRole('article', { name: `Review Book ${i}` })).toBeNull())
    if (i < 10) expect(screen.getAllByRole('article')).toHaveLength(10 - i)
  }
  expect(await screen.findByRole('article', { name: 'Review Book 11' })).toBeInTheDocument()
  expect(screen.getAllByRole('article')).toHaveLength(10)
  expect(requests.filter(r => !r.after)).toHaveLength(1)
  expect(requests.filter(r => r.after === 'batch-10')).toHaveLength(1)
  await waitFor(() => expect(requests).toContainEqual({ offset: 10, after: 'batch-20' }))
  for (let i = 11; i <= 21; i++) {
    await user.click(within(await screen.findByRole('article', { name: `Review Book ${i}` })).getByRole('button', { name: 'Approve' }))
    await waitFor(() => expect(screen.queryByRole('article', { name: `Review Book ${i}` })).toBeNull())
  }
  expect(new Set(decisions).size).toBe(21)
  expect(decisions).toEqual(original.map(r => r.id))
  expect(rows).toHaveLength(0)
  expect(window.scrollTo).toHaveBeenCalledWith({ top: 0, behavior: 'auto' })
})

it('uses the stable cursor when a preload completes after decisions changed the queue', async () => {
  const original = reviewRows(11), requests: (string | null)[] = []
  let rows = [...original], release: () => void = () => {}
  const held = new Promise<void>(resolve => { release = resolve })
  server.use(http.get('/api/match-reviews', async ({ request }) => {
    const url = new URL(request.url), after = url.searchParams.get('after'); requests.push(after)
    if (after) await held
    const eligible = after ? original.slice(original.findIndex(r => r.id === after) + 1).filter(r => rows.includes(r)) : rows
    return HttpResponse.json({ rows: eligible.slice(0, 10), total: rows.length, offset: Number(url.searchParams.get('offset')), limit: 10 })
  }), http.post('/api/match-reviews/:id/:action', ({ params }) => { rows = rows.filter(r => r.id !== params.id); return HttpResponse.json({ changed: true }) }))
  const { user } = setup(<MatchReviewPage mode="automatic" />)
  await screen.findByRole('article', { name: 'Review Book 1' })
  await waitFor(() => expect(requests).toContain('batch-10'))
  for (let i = 1; i <= 9; i++) {
    await user.click(within(screen.getByRole('article', { name: `Review Book ${i}` })).getByRole('button', { name: 'Approve' }))
    await waitFor(() => expect(screen.queryByRole('article', { name: `Review Book ${i}` })).toBeNull())
  }
  release()
  await user.click(within(screen.getByRole('article', { name: 'Review Book 10' })).getByRole('button', { name: 'Approve' }))
  expect(await screen.findByRole('article', { name: 'Review Book 11' })).toBeInTheDocument()
  expect(requests.filter(after => after === 'batch-10')).toHaveLength(1)
})

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

it('returns to earlier unreviewed books after finishing a manually selected last page', async () => {
  const original = reviewRows(11)
  let rows = [...original]
  server.use(http.get('/api/match-reviews', ({ request }) => {
    const url = new URL(request.url), after = url.searchParams.get('after'), offset = Number(url.searchParams.get('offset'))
    const eligible = after ? original.slice(original.findIndex(r => r.id === after) + 1).filter(r => rows.includes(r)) : rows.slice(offset)
    return HttpResponse.json({ rows: eligible.slice(0, 10), total: rows.length, offset, limit: 10 })
  }), http.post('/api/match-reviews/:id/:action', ({ params }) => { rows = rows.filter(r => r.id !== params.id); return HttpResponse.json({ changed: true }) }))
  const { user } = setup(<MatchReviewPage mode="automatic" />)
  await screen.findByRole('article', { name: 'Review Book 1' })
  await user.click(within(screen.getByRole('article', { name: 'Review Book 1' })).getByRole('button', { name: 'Approve' }))
  await waitFor(() => expect(screen.getAllByRole('article')).toHaveLength(9))
  await user.click(screen.getByRole('button', { name: 'Next' }))
  expect(await screen.findByText('10–10 of 10 reviews')).toBeInTheDocument()
  await user.click(within(screen.getByRole('article', { name: 'Review Book 11' })).getByRole('button', { name: 'Reject' }))
  expect(await screen.findByRole('article', { name: 'Review Book 2' })).toBeInTheDocument()
  expect(screen.getAllByRole('article')).toHaveLength(9)
  expect(screen.queryByRole('article', { name: 'Review Book 1' })).toBeNull()
  expect(screen.queryByRole('article', { name: 'Review Book 11' })).toBeNull()
})

it('keeps completed decisions hidden if loading the next batch fails, then recovers on refresh', async () => {
  const original = reviewRows(11)
  let rows = [...original], recover = false
  server.use(http.get('/api/match-reviews', ({ request }) => {
    const url = new URL(request.url)
    if (url.searchParams.has('after') && !recover) return HttpResponse.json({ detail: 'Next reviews unavailable' }, { status: 500 })
    return HttpResponse.json({ rows: rows.slice(0, 10), total: rows.length, offset: 0, limit: 10 })
  }), http.post('/api/match-reviews/:id/:action', ({ params }) => { rows = rows.filter(r => r.id !== params.id); return HttpResponse.json({ changed: true }) }))
  const { user } = setup(<MatchReviewPage mode="automatic" />)
  await screen.findByRole('article', { name: 'Review Book 1' })
  for (let i = 1; i <= 10; i++) {
    await user.click(within(screen.getByRole('article', { name: `Review Book ${i}` })).getByRole('button', { name: 'Approve' }))
    await waitFor(() => expect(screen.queryByRole('article', { name: `Review Book ${i}` })).toBeNull())
  }
  expect(await screen.findByRole('alert')).toHaveTextContent('Next reviews unavailable')
  expect(screen.queryAllByRole('article')).toHaveLength(0)
  expect(screen.queryByText('No automatic approvals to review.')).toBeNull()
  recover = true
  await user.click(screen.getByRole('button', { name: 'Refresh' }))
  expect(await screen.findByRole('article', { name: 'Review Book 11' })).toBeInTheDocument()
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

it.each(['automatic', 'shortlist'] as const)('saves %s cards independently while a decision is still queued', async mode => {
  const original = reviewRows(11, mode)
  let rows = [...original]
  const calls: string[] = []
  const releases = new Map<string, (failed?: boolean) => void>()
  server.use(http.get('/api/match-reviews', ({ request }) => {
    const after = new URL(request.url).searchParams.get('after')
    return HttpResponse.json({ rows: after ? original.slice(10) : rows.slice(0, 10), total: rows.length, offset: after ? 10 : 0, limit: 10 })
  }), http.post('/api/match-reviews/:id/:action', ({ params }) => {
    const id = String(params.id); calls.push(id)
    return HttpResponse.json({ job_id: id }, { status: 202 })
  }), http.get('/api/jobs/:id', async ({ params }) => {
    const id = String(params.id)
    const failed = await new Promise<boolean>(resolve => { releases.set(id, (fail = false) => resolve(fail)) })
    if (!failed) rows = rows.filter(row => row.id !== id)
    return HttpResponse.json({ state: failed ? 'failed' : 'done', result: { changed: true }, error: failed ? 'Evidence changed; refresh and retry.' : '' })
  }))
  const { user } = setup(<MatchReviewPage mode={mode} />)
  const first = await screen.findByRole('article', { name: 'Review Book 1' })
  const second = screen.getByRole('article', { name: 'Review Book 2' })
  await user.click(within(first).getByRole('button', { name: 'Approve' }))
  await waitFor(() => expect(releases.has('batch-1')).toBe(true))
  expect(within(first).getByRole('button', { name: 'Saving…' })).toBeDisabled()
  expect(within(first).getByRole('button', { name: 'Reject' })).toBeDisabled()
  expect(within(second).getByRole('button', { name: 'Approve' })).toBeEnabled()
  expect(screen.getByRole('textbox', { name: 'Search reviews' })).toBeEnabled()
  expect(screen.getByRole('button', { name: 'Refresh' })).toBeEnabled()
  expect(screen.getByRole('button', { name: 'Next' })).toBeEnabled()
  await user.click(within(second).getByRole('button', { name: 'Approve' }))
  await waitFor(() => expect(releases.has('batch-2')).toBe(true))
  releases.get('batch-2')!()
  await waitFor(() => expect(screen.queryByRole('article', { name: 'Review Book 2' })).toBeNull())
  expect(within(first).getByRole('button', { name: 'Saving…' })).toBeDisabled()
  releases.get('batch-1')!(true)
  expect(await screen.findByRole('alert')).toHaveTextContent('Book 1: Evidence changed')
  expect(within(first).getByRole('button', { name: 'Approve' })).toBeEnabled()
  expect(calls).toEqual(['batch-1', 'batch-2'])
})

it('does not restore a saved card when an overlapping refresh returns stale rows', async () => {
  const rows = reviewRows(2)
  let releaseDecision: () => void = () => {}, releaseRefresh: () => void = () => {}, reads = 0, deciding = false
  const decision = new Promise<void>(resolve => { releaseDecision = resolve })
  const refresh = new Promise<void>(resolve => { releaseRefresh = resolve })
  server.use(http.get('/api/match-reviews', async () => {
    if (++reads > 1) await refresh
    return HttpResponse.json(listing(rows))
  }), http.post('/api/match-reviews/:id/confirm', async () => {
    deciding = true; await decision; return HttpResponse.json({ changed: true })
  }))
  const { user } = setup(<MatchReviewPage mode="automatic" />)
  await user.click(within(await screen.findByRole('article', { name: 'Review Book 1' })).getByRole('button', { name: 'Approve' }))
  await waitFor(() => expect(deciding).toBe(true))
  await user.click(screen.getByRole('button', { name: 'Refresh' }))
  await waitFor(() => expect(reads).toBe(2))
  releaseDecision()
  await waitFor(() => expect(screen.queryByRole('article', { name: 'Review Book 1' })).toBeNull())
  releaseRefresh()
  await waitFor(() => expect(screen.getByRole('button', { name: 'Refresh' })).toBeEnabled())
  expect(screen.queryByRole('article', { name: 'Review Book 1' })).toBeNull()
  expect(screen.getByRole('article', { name: 'Review Book 2' })).toBeInTheDocument()
})
