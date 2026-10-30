import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { STATUSES, type Book, type Settings } from '../../src/api'

export const defaultSettings: Settings = { data_dir: '/tmp/appdata', library_dir: '', backup_dir: '/tmp/appdata/backups' }
export const initialBooks: Book[] = [
  { id: 1, title: 'Dune', author: 'Frank Herbert', narrator: '', series: 'Dune', series_position: 1, duration_seconds: 75600, path: '', status: 'unlistened', added: '2026-10-29T12:00:00Z', asin: '', kind: 'book', review: 'confirmed', file_count: null, suggestion: null, match_score: null },
]

export const hardcover = {
  id: '375699', url: 'https://hardcover.app/books/dune', description: 'A desert planet saga.', pages: 612, rating: 4.31, ratings_count: 12345, reviews_count: 99, readers: 5000, finished: 4000,
  genres: ['Science Fiction'], moods: ['adventurous'], tags: ['classic'], content_warnings: ['Violence'], series: [{ name: 'Dune Chronicles', position: 1 }], audiobook_editions: [{ asin: 'B0A', hours: 21, publisher: 'Macmillan Audio', released: '2007-01-01', format: 'Audible' }],
  reviews: [{ user: 'reader1', rating: 5, text: 'A masterpiece.', spoilers: false, likes: 12, date: '2020-01-02' }, { user: 'reader2', rating: 3, text: 'x'.repeat(800), spoilers: false, likes: 3, date: '' }, { user: 'reader3', rating: null, text: 'The twist is big.', spoilers: true, likes: 1, date: '2021-05-06' }],
}

export const mock: { settings: Settings; books: Book[]; extras: Record<string, { fetched: string; data: object }>; processed: object | null } = {
  settings: { ...defaultSettings },
  books: [...initialBooks],
  extras: {},
  processed: null,
}

export const resetMock = () => {
  mock.settings = { ...defaultSettings }
  mock.books = [...initialBooks]
  mock.extras = {}
  mock.processed = null
}

// Every endpoint the app calls has a handler here; one without a handler fails the test (setup.ts: onUnhandledFrame 'error').
export const handlers = [
  http.get('/api/health', () => HttpResponse.json({ ok: true, version: '0.1.0' })),
  http.get('/api/settings', () => HttpResponse.json(mock.settings)),
  http.put('/api/settings', async ({ request }) => {
    const body = (await request.json()) as Partial<Settings>
    if (body.data_dir && !body.data_dir.startsWith('/')) return HttpResponse.json({ detail: 'the data folder must be an absolute path' }, { status: 400 })
    mock.settings = { ...mock.settings, ...body }
    return HttpResponse.json(mock.settings)
  }),
  http.get('/api/books', ({ request }) => {
    const u = new URL(request.url), q = (u.searchParams.get('q') || '').toLowerCase(), status = u.searchParams.get('status'), review = u.searchParams.get('review'), kind = u.searchParams.get('kind')
    return HttpResponse.json(mock.books.filter(b => (!status || b.status === status) && (!review || b.review === review) && (!kind || b.kind === kind) && (!q || `${b.title} ${b.author}`.toLowerCase().includes(q))))
  }),
  http.post('/api/books', async ({ request }) => {
    const b = (await request.json()) as Partial<Book>
    if (!(b.title || '').trim()) return HttpResponse.json({ detail: 'a title is required' }, { status: 400 })
    const entry: Book = { id: mock.books.length + 1, title: b.title!.trim(), author: b.author || '', narrator: '', series: '', series_position: null, duration_seconds: null, path: '', status: 'unlistened', added: '2026-10-29T12:01:00Z', asin: '', kind: 'book', review: 'confirmed', file_count: null, suggestion: null, match_score: null }
    mock.books = [...mock.books, entry]
    return HttpResponse.json(entry)
  }),
  http.patch('/api/books/:id', async ({ request, params }) => {
    const b = (await request.json()) as { status?: Book['status'] }
    if (b.status && !STATUSES.includes(b.status)) return HttpResponse.json({ detail: 'bad status' }, { status: 400 })
    const cur = mock.books.find(x => x.id === Number(params.id))
    if (!cur) return HttpResponse.json({ detail: 'no such book' }, { status: 404 })
    Object.assign(cur, b)
    return HttpResponse.json(cur)
  }),
  http.get('/api/books/:id/details', ({ params }) => {
    const book = mock.books.find(x => x.id === Number(params.id))
    if (!book) return HttpResponse.json({ detail: 'no such book' }, { status: 404 })
    if (book.id === 1) return HttpResponse.json({
      book, extras: mock.extras, processed: mock.processed, path_exists: true,
      files: { count: 2, total_bytes: 5_000_000, truncated: false, list: [{ name: '01.mp3', bytes: 2_000_000 }, { name: '02.mp3', bytes: 3_000_000 }] },
      probe: { available: true, files_probed: 2, complete: true, duration_seconds: 3700, tags: { album: 'Dune', artist: 'Frank Herbert' }, codec: 'mp3', bitrate_kbps: 64, chapters: 0, has_cover_art: true },
      audible: { asin: 'B000TEST01', title: 'Dune', subtitle: 'Book One', authors: ['Frank Herbert'], narrators: ['Scott Brick'], series: [{ title: 'Dune Chronicles', sequence: '1' }], publisher: 'Macmillan', release_date: '2007-01-01', language: 'english', format: 'unabridged',
        runtime_minutes: 1260, summary: 'A desert planet.', rating: '4.6', rating_count: 12000, categories: ['Science Fiction'], sample_url: '', chapters: [{ title: 'Chapter 1', start_sec: 0, length_sec: 600 }], cover_url: '' },
    })
    return HttpResponse.json({ book, extras: {}, processed: null, path_exists: false, files: null, probe: null, audible: null })
  }),
  http.post('/api/books/:id/enrich', ({ params }) => {
    if (!mock.books.find(x => x.id === Number(params.id))) return HttpResponse.json({ detail: 'no such book' }, { status: 404 })
    mock.extras = { hardcover: { fetched: '2026-10-30T12:00:00+00:00', data: hardcover } }
    return HttpResponse.json({ found: true, extras: mock.extras })
  }),
  http.post('/api/enrich', () => HttpResponse.json({ tried: 1, enriched: 1, not_found: 0, remaining: 0 })),
  http.post('/api/scan', () => {
    mock.books = [...mock.books,
      { ...initialBooks[0], id: 10, title: 'dune messiah', author: '', review: 'new', file_count: 12 },
      { ...initialBooks[0], id: 11, title: 'Show S01E01', author: '', review: 'new', kind: 'radio', file_count: 1 }]
    return HttpResponse.json({ found: 2, added: 2, existing: 0 })
  }),
  http.post('/api/match', () => {
    const b = mock.books.find(x => x.id === 10)!
    Object.assign(b, { review: 'suggested', match_score: 0.7, suggestion: { title: 'Dune Messiah', author: 'Frank Herbert', series: 'Dune Chronicles', sources: ['audible', 'audiobookdb'] } })
    return HttpResponse.json({ tried: 1, suggested: 1, none: 0, remaining: 0 })
  }),
  http.post('/api/books/:id/confirm', ({ params }) => {
    const b = mock.books.find(x => x.id === Number(params.id))
    if (!b) return HttpResponse.json({ detail: 'no such book' }, { status: 404 })
    Object.assign(b, { review: 'confirmed', title: b.suggestion?.title || b.title, author: b.suggestion?.author || b.author })
    return HttpResponse.json(b)
  }),
  http.post('/api/books/:id/ignore', ({ params }) => {
    const b = mock.books.find(x => x.id === Number(params.id))
    if (!b) return HttpResponse.json({ detail: 'no such book' }, { status: 404 })
    b.review = 'ignored'
    return HttpResponse.json(b)
  }),
  http.delete('/api/books/:id', ({ params }) => {
    const cur = mock.books.find(x => x.id === Number(params.id))
    if (!cur) return HttpResponse.json({ detail: 'no such book' }, { status: 404 })
    mock.books = mock.books.filter(x => x !== cur)
    return HttpResponse.json(cur)
  }),
]

export const server = setupServer(...handlers)
