import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { STATUSES, type Book, type Settings } from '../../src/api'

export const defaultSettings: Settings = { data_dir: '/tmp/appdata', library_dir: '' }
export const initialBooks: Book[] = [
  { id: 1, title: 'Dune', author: 'Frank Herbert', narrator: '', series: 'Dune', series_position: 1, duration_seconds: 75600, path: '', status: 'unlistened', added: '2026-10-29T12:00:00Z' },
]

export const mock = {
  settings: { ...defaultSettings },
  books: [...initialBooks],
}

export const resetMock = () => {
  mock.settings = { ...defaultSettings }
  mock.books = [...initialBooks]
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
    const u = new URL(request.url), q = (u.searchParams.get('q') || '').toLowerCase(), status = u.searchParams.get('status')
    return HttpResponse.json(mock.books.filter(b => (!status || b.status === status) && (!q || `${b.title} ${b.author}`.toLowerCase().includes(q))))
  }),
  http.post('/api/books', async ({ request }) => {
    const b = (await request.json()) as Partial<Book>
    if (!(b.title || '').trim()) return HttpResponse.json({ detail: 'a title is required' }, { status: 400 })
    const entry: Book = { id: mock.books.length + 1, title: b.title!.trim(), author: b.author || '', narrator: '', series: '', series_position: null, duration_seconds: null, path: '', status: 'unlistened', added: '2026-10-29T12:01:00Z' }
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
  http.delete('/api/books/:id', ({ params }) => {
    const cur = mock.books.find(x => x.id === Number(params.id))
    if (!cur) return HttpResponse.json({ detail: 'no such book' }, { status: 404 })
    mock.books = mock.books.filter(x => x !== cur)
    return HttpResponse.json(cur)
  }),
]

export const server = setupServer(...handlers)
