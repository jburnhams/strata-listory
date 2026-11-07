import { describe, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'
import LibraryPage from '../../src/components/LibraryPage'
import { setup, screen, within } from '../utils/render'
import { server, mock, initialBooks } from '../utils/server'

const table = () => screen.findByRole('table', { name: 'Library' })

describe('LibraryPage', () => {
  it('lists books with their series, length and link, and says how many are shown', async () => {
    setup(<LibraryPage />)
    expect(await screen.findByRole('link', { name: 'Dune' })).toHaveAttribute('href', '/book/1')
    expect(screen.getByText(/Dune #1/)).toBeInTheDocument()
    expect(screen.getByText('21h 00m')).toBeInTheDocument()
    expect(screen.getByText('1 book shown.', { exact: false })).toBeInTheDocument()
  })

  it('adds a book, changes its listening status, filters in the URL and removes it with an undo window', async () => {
    const { user } = setup(<LibraryPage />)
    await user.type(await screen.findByLabelText('Title'), 'Emma')
    await user.click(screen.getByRole('button', { name: 'Add book' }))
    expect(await screen.findByRole('link', { name: 'Emma' })).toBeInTheDocument()
    expect(screen.getByText(/unknown author/)).toBeInTheDocument()

    await user.selectOptions(screen.getByLabelText('Status of Emma'), 'finished')
    await user.selectOptions(screen.getByLabelText('Listening'), 'finished')
    expect(await screen.findByLabelText('Status of Emma')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Dune' })).toBeNull()
    await user.selectOptions(screen.getByLabelText('Listening'), '')

    await user.type(screen.getByLabelText('Search'), 'herbert')
    expect(await screen.findByRole('link', { name: 'Dune' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Emma' })).toBeNull()

    await user.click(screen.getByRole('button', { name: 'Remove Dune' }))
    expect(await screen.findByText('No books match these filters')).toBeInTheDocument()
    expect(screen.getByText('Removed Dune from the catalogue.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Undo' }))
    expect(await screen.findByRole('link', { name: 'Dune' })).toBeInTheDocument()
  })

  it('shows radio programmes only when asked and forgets the choice when unticked', async () => {
    mock.books = [{ ...initialBooks[0], id: 60, title: 'Panel show', kind: 'radio' }]
    const { user } = setup(<LibraryPage />)
    expect(await screen.findByText('No books match these filters')).toBeInTheDocument()
    await user.click(screen.getByLabelText('Show radio'))
    expect(await screen.findByRole('link', { name: 'Panel show' })).toBeInTheDocument()
    await user.click(screen.getByLabelText('Show radio'))
    expect(await screen.findByText('No books match these filters')).toBeInTheDocument()
  })

  it('starts from a search kept in the URL', async () => {
    setup(<LibraryPage />, { route: '/library?q=zzz' })
    expect(await screen.findByText('No books match these filters')).toBeInTheDocument()
    expect(screen.getByLabelText('Search')).toHaveValue('zzz')
  })

  it('teaches the next step when the catalogue is empty', async () => {
    mock.books = []
    setup(<LibraryPage />)
    expect(await screen.findByText('No books yet')).toBeInTheDocument()
  })

  it('shows an error alert when loading books fails and offers to try again', async () => {
    server.use(http.get('/api/books', () => HttpResponse.json({ detail: 'load failed' }, { status: 500 })))
    setup(<LibraryPage />)
    expect(await screen.findByRole('alert')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Try again' })).toBeInTheDocument()
  })

  it('reports a failed add from the server', async () => {
    const { user } = setup(<LibraryPage />)
    await user.type(await screen.findByLabelText('Title'), '  ')
    await user.click(screen.getByRole('button', { name: 'Add book' }))
    await table()
    expect(screen.queryByRole('alert')).toBeNull()               // a blank title is never sent
    mock.books = []
    await user.type(screen.getByLabelText('Title'), 'x')
    server.use(http.post('/api/books', () => HttpResponse.json({ detail: 'a title is required' }, { status: 400 })))
    await user.click(screen.getByRole('button', { name: 'Add book' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('a title is required')
  })

  it('reports a failed update when the book vanished', async () => {
    const { user } = setup(<LibraryPage />)
    await screen.findByRole('link', { name: 'Dune' })
    mock.books = []
    await user.selectOptions(screen.getByLabelText('Status of Dune'), 'listening')
    expect(await screen.findByRole('alert')).toHaveTextContent('no such book')
  })
})

describe('LibraryPage table', () => {
  it('sorts by title and by length from the column headers and adds a book with an author', async () => {
    mock.books = [{ ...initialBooks[0], id: 40, title: 'Zebra', duration_seconds: 3600 }, { ...initialBooks[0], id: 41, title: 'Apple', duration_seconds: 7200 }]
    const { user } = setup(<LibraryPage />)
    const titles = () => within(screen.getByRole('table', { name: 'Library' })).getAllByRole('link').map(a => a.textContent)
    await screen.findByRole('link', { name: 'Zebra' })
    await user.click(screen.getByRole('button', { name: 'Book' }))
    expect(titles()).toEqual(['Apple', 'Zebra'])
    await user.click(screen.getByRole('button', { name: 'Length' }))
    expect(titles()).toEqual(['Zebra', 'Apple'])
    await user.type(screen.getByLabelText('Title'), 'Emma'); await user.type(screen.getByLabelText('Author'), 'Jane Austen')
    await user.click(screen.getByRole('button', { name: 'Add book' }))
    expect(await screen.findByText(/Jane Austen/)).toBeInTheDocument()
  })

  it('shows a suggestion with its series, sources, narrator and file count', async () => {
    mock.books = [{ ...initialBooks[0], id: 50, title: 'Odd one', review: 'suggested', narrator: 'Reader', series: '', series_position: null, file_count: 1, duration_seconds: null, match_score: .9, suggestion: { title: 'Odd One Out', author: '', series: 'Saga', sources: [] } }]
    setup(<LibraryPage />)
    expect(await screen.findByText(/Suggested:/)).toHaveTextContent('Odd One Out by unknown (Saga)')
    expect(screen.getByText(/1 file · Narrated by Reader/)).toBeInTheDocument()
    expect(screen.getByText('90% match')).toBeInTheDocument()
  })

  it('offers a scan from the empty state', async () => {
    mock.books = []
    const { user } = setup(<LibraryPage />)
    await screen.findByText('No books yet')
    await user.click(screen.getAllByRole('button', { name: 'Scan audiobook folder' }).at(-1)!)           // the empty state's own action
    expect(await screen.findByText(/Scanned: 2 found/)).toBeInTheDocument()
  })
})

describe('Library ingest submission', () => {
  const seven = () => { mock.books = Array.from({ length: 7 }, (_, i) => ({ ...initialBooks[0], id: i + 10, title: `Source ${i + 1}`, path: `/inbox/source-${i + 1}` })) }

  it('queues every selected book, including more than five, with offline collection', async () => {
    seven()
    let body: unknown
    server.use(http.post('/api/ingest/run', async ({ request }) => { body = await request.json(); return HttpResponse.json({ queued: true }) }))
    const { user } = setup(<LibraryPage />)
    await user.click(await screen.findByRole('checkbox', { name: 'Select all shown' }))
    await user.click(screen.getByRole('checkbox', { name: 'Query metadata providers' }))
    await user.click(screen.getByRole('button', { name: 'Start ingest (7)' }))
    expect(await screen.findByText(/Ingest queued for 7 books/)).toBeInTheDocument()
    expect(body).toEqual({ ids: [10, 11, 12, 13, 14, 15, 16], online: false, limit: 7 })
    expect(screen.getByText('No books selected')).toBeInTheDocument()
  })

  it('keeps the selection when the server refuses, and clears it on request', async () => {
    seven()
    server.use(http.post('/api/ingest/run', () => HttpResponse.json({ detail: 'worker unavailable' }, { status: 503 })))
    const { user } = setup(<LibraryPage />)
    await user.click(await screen.findByRole('checkbox', { name: 'Select Source 1 (ID 10)' }))
    await user.click(screen.getByRole('button', { name: 'Start ingest (1)' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('worker unavailable')
    expect(screen.getByRole('checkbox', { name: 'Select Source 1 (ID 10)' })).toBeChecked()
    await user.click(screen.getByRole('button', { name: 'Clear selection' }))
    expect(screen.getByRole('checkbox', { name: 'Select Source 1 (ID 10)' })).not.toBeChecked()
  })

  it('does not let books without source files, radio shows or ignored books be selected', async () => {
    mock.books = [{ ...initialBooks[0], id: 20, title: 'Handwritten', path: '' }, { ...initialBooks[0], id: 21, title: 'Ignored one', path: '/a/b', review: 'ignored' }]
    setup(<LibraryPage />)
    expect(await screen.findByRole('checkbox', { name: 'Select Handwritten (ID 20)' })).toBeDisabled()
    expect(screen.getByRole('checkbox', { name: 'Select Ignored one (ID 21)' })).toBeDisabled()
    expect(screen.getByRole('checkbox', { name: 'Select all shown' })).toBeDisabled()
  })
})

describe('Library workflow filter', () => {
  const inWorkflow = () => { mock.books = [{ ...initialBooks[0], id: 30, title: 'Fresh', path: '/a/fresh' }, { ...initialBooks[0], id: 31, title: 'Underway', path: '/a/underway' }]; server.use(http.get('/api/library/workflow', () => HttpResponse.json({ books: { 31: ['quality'] } }))) }

  it('hides books already in the workflow until their stage is chosen', async () => {
    inWorkflow()
    const { user } = setup(<LibraryPage />)
    expect(await screen.findByRole('link', { name: 'Fresh' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Underway' })).toBeNull()
    expect(screen.getByText(/1 entry already in the workflow are hidden/)).toBeInTheDocument()
    await user.selectOptions(screen.getByLabelText('Workflow'), 'quality')
    expect(await screen.findByRole('link', { name: 'Underway' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Fresh' })).toBeNull()
    expect(within(await table()).getByText(/Quality/)).toBeInTheDocument()
  })
})

describe('scan, match and review', () => {
  it('scans, matches, then confirms a suggestion and ignores another item', async () => {
    const { user } = setup(<LibraryPage />)
    await screen.findByRole('link', { name: 'Dune' })
    await user.click(screen.getByRole('button', { name: 'Scan audiobook folder' }))
    expect(await screen.findByText('Scanned: 2 found, 2 new, 0 already known.')).toBeInTheDocument()
    expect(await screen.findByRole('link', { name: 'dune messiah' })).toBeInTheDocument()   // the radio item is hidden until "Show radio"
    expect(screen.queryByRole('link', { name: 'Show S01E01' })).toBeNull()

    await user.click(screen.getByRole('button', { name: 'Match new books' }))
    expect(await screen.findByText('Matched 1: 1 suggestions, 0 without a match, 0 still to try.')).toBeInTheDocument()
    expect(await screen.findByText('70% match (check) · via audible + audiobookdb')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'audiobookdb.org' })).toHaveAttribute('href', 'https://audiobookdb.org')

    await user.click(screen.getByRole('button', { name: 'Confirm dune messiah' }))
    await screen.findByText('Dune Messiah', { exact: false })
    await user.selectOptions(screen.getByLabelText('Match state'), 'new')
    expect(await screen.findByText('No books match these filters')).toBeInTheDocument()
    await user.click(screen.getByLabelText('Show radio'))
    await user.click(await screen.findByRole('button', { name: 'Ignore Show S01E01' }))
    await user.selectOptions(screen.getByLabelText('Match state'), 'ignored')
    expect(await screen.findByRole('link', { name: 'Show S01E01' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Ignore Show S01E01' })).toBeNull()
  })

  it('shows an error when matching fails', async () => {
    server.use(http.post('/api/match', () => HttpResponse.json({ detail: 'no AudiobookDB API key' }, { status: 502 })))
    const { user } = setup(<LibraryPage />)
    await screen.findByRole('link', { name: 'Dune' })
    await user.click(screen.getByRole('button', { name: 'Match new books' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('no AudiobookDB API key')
  })
})
