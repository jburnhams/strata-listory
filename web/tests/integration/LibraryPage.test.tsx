import { fireEvent } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'
import LibraryPage from '../../src/components/LibraryPage'
import { setup, screen, waitFor, act } from '../utils/render'
import { server, mock, initialBooks } from '../utils/server'

describe('LibraryPage', () => {
  it('lists books, adds one, changes its status, filters, and removes it', async () => {
    const { user } = setup(<LibraryPage />)
    expect(await screen.findByText('1 book')).toBeInTheDocument()
    expect(screen.getByText('Dune')).toBeInTheDocument()
    expect(screen.getByText(/Dune #1 · 21h 00m/)).toBeInTheDocument()

    await user.type(screen.getByLabelText('Title'), 'Emma')
    await user.click(screen.getByRole('button', { name: 'Add book' }))
    expect(await screen.findByText('2 books')).toBeInTheDocument()
    expect(screen.getByText(/unknown author/)).toBeInTheDocument()

    await user.selectOptions(screen.getByLabelText('Status of Emma'), 'finished')
    await user.selectOptions(screen.getByLabelText('Filter by status'), 'finished')
    expect(await screen.findByText('1 book')).toBeInTheDocument()
    expect(screen.queryByText('Dune')).toBeNull()
    await user.selectOptions(screen.getByLabelText('Filter by status'), '')

    await user.type(screen.getByLabelText('Search'), 'herbert')
    expect(await screen.findByText('1 book')).toBeInTheDocument()
    expect(screen.queryByText('Emma')).toBeNull()

    await user.click(screen.getByRole('button', { name: 'Remove Dune' }))
    expect(await screen.findByText('No books match the current filters.')).toBeInTheDocument()
  })

  it('shows a server error from adding', async () => {
    const { user } = setup(<LibraryPage />)
    await screen.findByText('1 book')
    await user.click(screen.getByRole('button', { name: 'Add book' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('a title is required')
  })

  it('shows an error alert when loading books fails', async () => {
    server.use(http.get('/api/books', () => HttpResponse.json({ detail: 'load failed' }, { status: 500 })))
    setup(<LibraryPage />)
    expect(await screen.findByRole('alert')).toHaveTextContent('load failed')
  })
})

describe('Library ingest submission', () => {
  it('queues every selected candidate, including more than five, with offline collection', async () => {
    mock.books = Array.from({ length: 7 }, (_, i) => ({ ...initialBooks[0], id: i + 10, title: `Source ${i + 1}`, path: `/inbox/source-${i + 1}` }))
    let body: unknown
    server.use(http.post('/api/ingest/run', async ({ request }) => { body = await request.json(); return HttpResponse.json({ queued: true }) }))
    const { user } = setup(<LibraryPage />)
    await user.click(await screen.findByRole('button', { name: 'Select all matches (7)' }))
    await user.click(screen.getByRole('checkbox', { name: 'Query metadata providers' }))
    await user.click(screen.getByRole('button', { name: 'Start ingest (7)' }))
    expect(await screen.findByRole('status')).toHaveTextContent('Ingest queued')
    expect(body).toEqual({ ids: [10, 11, 12, 13, 14, 15, 16], online: false, limit: 7 })
    expect(screen.getByRole('button', { name: 'Start ingest (0)' })).toBeDisabled()
    expect(screen.getByRole('checkbox', { name: 'Select Source 1 (ID 10)' })).not.toBeChecked()
  })
})

const candidate = (id: number, title: string, over: Partial<typeof initialBooks[0]> = {}) => ({ ...initialBooks[0], id, title, path: `/inbox/${title}`, ...over })

describe('ingest candidate selection', () => {
  it('searches source details and keeps a batch selected across searches', async () => {
    mock.books = [candidate(10, 'Hitch-22', { author: 'Christopher Hitchens', narrator: 'Christopher Hitchens', series: '', file_count: 15 }), candidate(11, 'Dune Messiah', { series: 'Dune Chronicles' }), candidate(12, 'No author', { author: '', duration_seconds: null, series: '' }), candidate(13, 'Ignored', { review: 'ignored' }), candidate(14, 'No files', { path: '' }), candidate(15, 'Radio', { kind: 'radio' })]
    const { user } = setup(<LibraryPage />)
    expect(screen.getByText('Loading…')).toBeInTheDocument()
    expect(await screen.findByText('5 books')).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: 'Select Ignored (ID 13)' })).toBeDisabled(); expect(screen.getByRole('checkbox', { name: 'Select No files (ID 14)' })).toBeDisabled(); expect(screen.queryByRole('checkbox', { name: /Select Radio/ })).toBeNull()
    expect(screen.getByText(/Narrated by Christopher Hitchens/)).toBeInTheDocument()
    expect(screen.getByText(/15 files/)).toBeInTheDocument()
    expect(screen.getByText(/unknown author/)).toBeInTheDocument()
    const search = screen.getByRole('searchbox', { name: 'Search' })
    await user.type(search, 'hitchens /INBOX')
    await user.click(screen.getByRole('checkbox', { name: 'Select Hitch-22 (ID 10)' }))
    await user.clear(search); await user.type(search, 'Chronicles')
    expect(screen.queryByRole('checkbox', { name: 'Select Hitch-22 (ID 10)' })).toBeNull()
    await user.click(screen.getByRole('button', { name: 'Select all matches (1)' }))
    expect(screen.getByRole('button', { name: 'Start ingest (2)' })).toBeEnabled()
    await user.clear(search)
    await user.click(screen.getByRole('checkbox', { name: 'Show selected only' }))
    expect(screen.getByText('2 books')).toBeInTheDocument()
    expect(screen.queryByRole('checkbox', { name: 'Select No author (ID 12)' })).toBeNull()
    await user.click(screen.getByRole('checkbox', { name: 'Select Hitch-22 (ID 10)' }))
    expect(screen.getByRole('button', { name: 'Start ingest (1)' })).toBeEnabled()
    await user.click(screen.getByRole('button', { name: 'Clear selection' }))
    expect(screen.getByRole('button', { name: 'Start ingest (0)' })).toBeDisabled()
    expect(screen.getByText('No books match the current filters.')).toBeInTheDocument()
    await user.click(screen.getByRole('checkbox', { name: 'Show selected only' }))
    await user.type(search, 'not a book')
    expect(screen.getByRole('button', { name: 'Select all matches (0)' })).toBeDisabled()
    await user.clear(search); await user.type(search, '12')
    expect(screen.getByRole('checkbox', { name: 'Select No author (ID 12)' })).toBeInTheDocument()
  })

  it('selects pages independently and preserves selections while paging back', async () => {
    mock.books = Array.from({ length: 28 }, (_, i) => candidate(i + 10, `Source ${i + 1}`))
    const { user } = setup(<LibraryPage />)
    await screen.findByText('28 books')
    expect(screen.getByRole('button', { name: 'Previous page' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Select this page' }))
    expect(screen.getByRole('button', { name: 'Start ingest (25)' })).toBeEnabled()
    await user.click(screen.getByRole('button', { name: 'Next page' }))
    expect(screen.getByText('26–28 of 28')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Next page' })).toBeDisabled()
    await user.click(screen.getByRole('checkbox', { name: 'Select Source 28 (ID 37)' }))
    await user.click(screen.getByRole('button', { name: 'Previous page' }))
    expect(screen.getByRole('checkbox', { name: 'Select Source 1 (ID 10)' })).toBeChecked()
    expect(screen.getByRole('button', { name: 'Start ingest (26)' })).toBeEnabled()
    await user.type(screen.getByRole('searchbox'), 'Source 28')
    expect(screen.getByRole('checkbox', { name: 'Select Source 28 (ID 37)' })).toBeChecked()
    expect(screen.queryByRole('navigation', { name: 'Library pages' })).toBeNull()
  })

  it('selects and queues all 1681 matches without truncation', async () => {
    mock.books = Array.from({ length: 1681 }, (_, i) => candidate(i + 10, `Batch book ${i + 1}`))
    let body: unknown
    server.use(http.post('/api/ingest/run', async ({ request }) => { body = await request.json(); return HttpResponse.json({ queued: true }) }))
    const { user } = setup(<LibraryPage />)
    await user.click(await screen.findByRole('button', { name: 'Select all matches (1681)' }))
    expect(screen.getByText('1681 selected')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Start ingest (1681)' })).toBeEnabled()
    const first = screen.getByRole('checkbox', { name: 'Select Batch book 1 (ID 10)' })
    expect(first).toBeChecked()
    await user.click(first)
    expect(screen.getByText('1680 selected')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Select this page' }))
    expect(screen.getByText('1681 selected')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Start ingest (1681)' }))
    expect(await screen.findByRole('status')).toHaveTextContent('1681 selected entries')
    expect(body).toEqual({ ids: expect.arrayContaining(mock.books.map(b => b.id)), online: true, limit: 1681 })
    expect((body as { ids: number[] }).ids).toHaveLength(1681)
  })

  it('retains the chosen batch after a failed submission and prevents duplicate clicks while submitting', async () => {
    mock.books = [candidate(10, 'Hitch-22')]
    server.use(http.post('/api/ingest/run', () => HttpResponse.json({ detail: 'worker unavailable' }, { status: 503 })))
    const { user } = setup(<LibraryPage />)
    await user.click(await screen.findByRole('checkbox', { name: 'Select Hitch-22 (ID 10)' }))
    await user.click(screen.getByRole('button', { name: 'Start ingest (1)' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('worker unavailable')
    expect(screen.getByRole('checkbox', { name: 'Select Hitch-22 (ID 10)' })).toBeChecked()
    let finish: (() => void) | undefined
    let body: unknown, calls = 0
    server.use(http.post('/api/ingest/run', async ({ request }) => {
      calls++; body = await request.json()
      await new Promise<void>(resolve => { finish = resolve })
      return HttpResponse.json({ queued: true })
    }))
    await user.click(screen.getByRole('button', { name: 'Start ingest (1)' }))
    expect(screen.getByRole('button', { name: 'Queuing ingest…' })).toBeDisabled()
    expect(screen.getByRole('checkbox', { name: 'Select Hitch-22 (ID 10)' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Queuing ingest…' }))
    await waitFor(() => expect(finish).toBeDefined())
    act(() => finish!())
    expect(await screen.findByRole('status')).toHaveTextContent('1 selected entry')
    expect(calls).toBe(1)
    expect(body).toEqual({ ids: [10], online: true, limit: 1 })
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('shows catalogue errors and lets the user reload candidates', async () => {
    server.use(http.get('/api/books', () => HttpResponse.json({ detail: 'catalogue unavailable' }, { status: 503 })))
    const { user } = setup(<LibraryPage />)
    expect(await screen.findByRole('alert')).toHaveTextContent('catalogue unavailable')
    expect(screen.getByRole('button', { name: 'Start ingest (0)' })).toBeDisabled()
    mock.books = [candidate(10, 'Recovered book')]
    server.use(http.get('/api/books', () => HttpResponse.json(mock.books)))
    await user.click(screen.getByRole('button', { name: 'Reload library' }))
    expect(await screen.findByRole('checkbox', { name: 'Select Recovered book (ID 10)' })).toBeInTheDocument()
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('shows books without source files but prevents ingest selection', async () => {
    setup(<LibraryPage />)
    expect(await screen.findByText('No source files')).toBeInTheDocument()
    expect(screen.getByRole('checkbox', { name: 'Select Dune (ID 1)' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Start ingest (0)' })).toBeDisabled()
  })
})

it('hides every workflow stage by default and includes each through its checkbox', async () => {
  const labels = ['Processing', 'Book identification', 'Audiobook identification', 'Quality', 'Proposed', 'Finished']
  const stages = ['processing', 'metadata', 'audiobook', 'quality', 'proposed', 'finished']
  mock.books = [candidate(10, 'Ready'), ...stages.map((_, i) => candidate(i + 11, `Already ${i}`))]
  server.use(http.get('/api/library/workflow', () => HttpResponse.json({ books: Object.fromEntries(stages.map((stage, i) => [i + 11, [stage]])) })))
  const { user } = setup(<LibraryPage />)
  expect(await screen.findByText('1 book')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Ready' })).toBeInTheDocument()
  expect(screen.queryByRole('link', { name: 'Already 0' })).toBeNull()
  expect(screen.getByRole('button', { name: 'Select all matches (1)' })).toBeEnabled()
  for (let i = 0; i < stages.length; i++) {
    const checkbox = screen.getByRole('checkbox', { name: `${labels[i]} (1)` })
    expect(checkbox).not.toBeChecked()
    await user.click(checkbox)
    expect(await screen.findByRole('link', { name: `Already ${i}` })).toBeInTheDocument()
    await user.click(checkbox)
    expect(screen.queryByRole('link', { name: `Already ${i}` })).toBeNull()
  }
})

it('removes hidden workflow books from selection and refreshes membership after queuing ingest', async () => {
  mock.books = [candidate(10, 'Ready'), candidate(11, 'Reviewing')]
  let queued = false, request: unknown
  server.use(http.get('/api/library/workflow', () => HttpResponse.json({ books: { 11: ['metadata'], ...(queued ? { 10: ['processing'] } : {}) } })), http.post('/api/ingest/run', async ({ request: r }) => {
    request = await r.json(); queued = true; return HttpResponse.json({ queued: true })
  }))
  const { user } = setup(<LibraryPage />)
  await user.click(await screen.findByRole('checkbox', { name: 'Book identification (1)' }))
  await user.click(screen.getByRole('checkbox', { name: 'Select Reviewing (ID 11)' }))
  expect(screen.getByText('1 selected')).toBeInTheDocument()
  await user.click(screen.getByRole('checkbox', { name: 'Book identification (1)' }))
  await waitFor(() => expect(screen.getByText('0 selected')).toBeInTheDocument())
  await user.click(screen.getByRole('button', { name: 'Select all matches (1)' }))
  await user.click(screen.getByRole('button', { name: 'Start ingest (1)' }))
  await waitFor(() => expect(screen.queryByRole('link', { name: 'Ready' })).toBeNull())
  expect(request).toEqual({ ids: [10], online: true, limit: 1 })
  expect(screen.getByText('No books match the current filters.')).toBeInTheDocument()
})

it('keeps ingestion unavailable when workflow membership cannot be loaded', async () => {
  server.use(http.get('/api/library/workflow', () => HttpResponse.json({ detail: 'workflow unavailable' }, { status: 503 })))
  setup(<LibraryPage />)
  expect(await screen.findByRole('alert')).toHaveTextContent('workflow unavailable')
  expect(screen.getByRole('button', { name: 'Select all matches (0)' })).toBeDisabled()
  expect(screen.getByRole('button', { name: 'Start ingest (0)' })).toBeDisabled()
})


describe('Library source folders', () => {
  it('filters a folder and its descendants alongside search and bulk selection', async () => {
    mock.books = [
      { ...initialBooks[0], id: 1, title: 'Root book', path: '/audio/Books' },
      { ...initialBooks[0], id: 2, title: 'Nested book', path: '/audio/Books/Writer/Novel.mp3' },
      { ...initialBooks[0], id: 3, title: 'Different folder', path: '/audio/Bookshelf/Other' },
      { ...initialBooks[0], id: 4, title: 'No folder', path: '' },
    ]
    const { user } = setup(<LibraryPage />)
    await user.click(await screen.findByRole('button', { name: 'Books' }))
    expect(screen.getByRole('link', { name: 'Root book' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Nested book' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Different folder' })).toBeNull()
    expect(screen.queryByRole('link', { name: 'No folder' })).toBeNull()
    await user.click(screen.getByRole('button', { name: 'Select all matches (2)' }))
    expect(screen.getByRole('button', { name: 'Start ingest (2)' })).toBeEnabled()
    await user.type(screen.getByRole('searchbox'), 'Nested')
    expect(screen.queryByRole('link', { name: 'Root book' })).toBeNull()
    expect(screen.getByRole('link', { name: 'Nested book' })).toBeInTheDocument()
    await user.clear(screen.getByRole('searchbox'))
    await user.click(screen.getByRole('button', { name: 'Writer' }))
    expect(screen.queryByRole('link', { name: 'Root book' })).toBeNull()
    await user.click(screen.getByRole('button', { name: 'All folders' }))
    expect(screen.getByRole('link', { name: 'Different folder' })).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'No source folder' }))
    expect(screen.getByRole('link', { name: 'No folder' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Root book' })).toBeNull()
  })
})


it('filters to known covers and recognises images loaded during the session', async () => {
  mock.books = [
    { ...initialBooks[0], id: 1, title: 'Cached cover', path: '/books/A' },
    { ...initialBooks[0], id: 2, title: 'Newly loaded cover', path: '/books/B' },
    { ...initialBooks[0], id: 3, title: 'Missing cover', path: '/books/C' },
  ]
  server.use(http.get('/api/library/covers', () => HttpResponse.json({ ids: [1] })))
  const { user } = setup(<LibraryPage />)
  await screen.findByRole('link', { name: 'Cached cover' })
  fireEvent.load(screen.getAllByAltText('')[1])
  await user.click(screen.getByRole('checkbox', { name: 'Has cover image' }))
  expect(screen.getByRole('link', { name: 'Cached cover' })).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Newly loaded cover' })).toBeInTheDocument()
  expect(screen.queryByRole('link', { name: 'Missing cover' })).toBeNull()
  await user.type(screen.getByRole('searchbox'), 'Cached')
  expect(screen.queryByRole('link', { name: 'Newly loaded cover' })).toBeNull()
  await user.clear(screen.getByRole('searchbox'))
  await user.click(screen.getByRole('checkbox', { name: 'Has cover image' }))
  expect(screen.getByRole('link', { name: 'Missing cover' })).toBeInTheDocument()
})
