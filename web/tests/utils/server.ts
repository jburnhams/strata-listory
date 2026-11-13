import { http, HttpResponse } from 'msw'
import { setupServer } from 'msw/node'
import { STATUSES, type Book, type Settings } from '../../src/api'

export const defaultSettings: Settings = { data_dir: '/tmp/appdata', library_dir: '/audio', library_dirs: ['/audio'], backup_dir: '/tmp/appdata/backups' }
export const initialBooks: Book[] = [
  { id: 1, title: 'Dune', author: 'Frank Herbert', narrator: '', series: 'Dune', series_position: 1, duration_seconds: 75600, path: '', status: 'unlistened', added: '2026-10-29T12:00:00Z', asin: '', kind: 'book', review: 'confirmed', file_count: null, suggestion: null, match_score: null, year: null, year_source: '' },
]

export const hardcover = {
  id: '375699', url: 'https://hardcover.app/books/dune', description: 'A desert planet saga.', pages: 612, rating: 4.31, ratings_count: 12345, reviews_count: 99, readers: 5000, finished: 4000,
  genres: ['Science Fiction'], moods: ['adventurous'], tags: ['classic'], content_warnings: ['Violence'], series: [{ name: 'Dune Chronicles', position: 1 }], audiobook_editions: [{ asin: 'B0A', hours: 21, publisher: 'Macmillan Audio', released: '2007-01-01', format: 'Audible' }],
  reviews: [{ user: 'reader1', rating: 5, text: 'A masterpiece.', spoilers: false, likes: 12, date: '2020-01-02' }, { user: 'reader2', rating: 3, text: 'x'.repeat(800), spoilers: false, likes: 3, date: '' }, { user: 'reader3', rating: null, text: 'The twist is big.', spoilers: true, likes: 1, date: '2021-05-06' }],
}

export const ingestFixture = () => ({
  policy: { revision: 0, auto_approve_metadata: false, auto_approve_audiobook: false, auto_approve_quality: false, auto_approve: false, auto_process: false, providers: ['audible'], allowed_kinds: ['book'], gates: { 'recording.score': { min: .9 } } },
  stages: ['inventory', 'identity'], worker: { busy: null as string | null, queued: 0, last: {} },
  runs: [{ id: 'run1', book_id: 1, title: 'Dune', state: 'review', created: '2026-10-31', stage: 'eligibility', members: [1, 2], metrics: { 'recording.score': null }, stages: [{ stage: 'inventory', status: 'done', elapsed: 1.25, error: '', metrics: {} }], eligibility: null,
    review_phase: 'metadata', superseded: false, processing_proposed: false,
    reviews: { metadata: { approved: false, eligible: false, complete: true, kind_allowed: true, checks: [{ metric: 'recording.score', value: null as number | null, threshold: { min: .9 }, passed: false, reason: 'missing measurement' }] }, audiobook: { approved: false, eligible: false, complete: true, kind_allowed: true, checks: [] }, quality: { approved: false, eligible: true, complete: true, kind_allowed: true, checks: [] } },
    metadata: { rows: [{ field: 'Title', value: 'Dune', source: 'Audible', note: '' }, { field: 'Year', value: '1965', source: 'Open Library (first published)', note: '' }], recording: { asin: 'B000000001', language: 'english', runtime_minutes: 1260, abridged: false }, source_families: 3, match_score: 1 },
    current_evaluation: { eligible: false, kind_allowed: true, checks: [{ metric: 'recording.score', value: null as number | null, threshold: { min: .9 }, passed: false, reason: 'missing measurement' }], policy: {} } }],
})

const planSummary = (over: object = {}) => ({ year: 2007, metadata: [{ field: 'Title', value: 'Dune', source: 'audible + audiobookdb match (confirmed)', note: '' }, { field: 'Author', value: 'CBS Radio Workshop, Frank Herbert', source: 'audiobookdb match (confirmed)', note: 'the Audible metadata file says: Frank Herbert' }, { field: 'Year', value: '2007', source: 'Audible metadata file', note: '' }], hours: 8.2, files: 18, chapters: 18, chapter_source: 'Audible chapter list', tiers: { V5: 17, V3: 1 }, in_bytes: 382_000_000, est_bytes: 177_000_000, out_dir: '/home/u/audiobooks/A/Dune', loudness_before: -23.4, loudness_after_est: -20.9, decision: 'too quiet: lifted with a limiter', reencoded: false, warnings: ['ends 2.9 dB below the target because its peaks leave no more room'], downmix: 'average',
  chapter_list: [{ file: '01 - One.mp3', title: 'One', minutes: 29.4, mode: 'encode', tier: 'V5' }, { file: '02 - Two.mp3', title: 'Two', minutes: 21.4, mode: 'copy', tier: 'copy' }], ...over })

export const mock: { settings: Settings; books: Book[]; extras: Record<string, { fetched: string; data: object }>; processed: object | null; queue: { book_id: number; title: string; author: string; state: string; error: string; updated: string; summary: object | null }[] } = {
  settings: { ...defaultSettings },
  books: [...initialBooks],
  extras: {},
  processed: null,
  queue: [
    { book_id: 1, title: 'Dune', author: 'Frank Herbert', state: 'proposed', error: '', updated: '2026-10-30T12:00:00+00:00', summary: planSummary() },
    { book_id: 2, title: 'Emma', author: 'Jane Austen', state: 'proposed', error: '', updated: '2026-10-30T12:01:00+00:00', summary: planSummary({ warnings: [] }) },
  ],
}

// How the classifier (src/app/store/kinds.py) judged invented listens: a clear broadcast, and a close call that is still a book.
export const kindInfos: Record<string, import('../../src/api').KindInfoData> = {
  series: { kind: 'radio_series', guess: 'radio_series', band: 'broadcast', confidence: 0.97, p_broadcast: 0.97, decided_by: 'rule', signals: [{ name: 'channel', weight: 3.9, prior: 3, learned: true, detail: 'title: "BBC Radio 4"' }, { name: 'series number', weight: 1.8, prior: 1.8, learned: false, detail: 'title: "series 11"' }] },
  close: { kind: 'book', guess: 'play', band: 'review', confidence: 0.5, p_broadcast: 0.5, decided_by: 'rule', signals: [{ name: 'play length', weight: 0.6, detail: '90 minutes in 1 file' }, { name: 'programme genre', weight: 0.8, detail: 'genre "Drama"' }] },
}
const kindState: { chosen: { id: number; kind: string }[] } = { chosen: [] }
export const kindChoices = kindState.chosen
// The match queue (/api/matches): invented books, most certain first.
export function matchQueue(): import('../../src/api').QueueItem[] {
  const cand = (part_id: number, p: number, title: string, extra: Partial<import('../../src/api').QueueCandidate> = {}): import('../../src/api').QueueCandidate => ({ part_id, ordinal: 1, state: p >= 0.95 ? 'accepted' : 'candidate', auto: p >= 0.95, p, work_id: part_id, key: `/works/OL${part_id}W`, title, authors: ['Mara Quill'], year: 2001, editions: 4, ol: [`/works/OL${part_id}W`], isbns: ['9780306406157'],
    recordings: [{ asin: 'B0AAAAAAA1', title, narrators: ['Ada Penn'], minutes: 606, fmt: 'unabridged', year: 2015 }, { asin: 'B0AAAAAAA2', title, narrators: ['Tom Vale'], minutes: 300, fmt: 'abridged', year: 2009 }], recording: 'B0AAAAAAA1', alias: title.toLowerCase(), reading: 'tags',
    evidence: { title: 'exact_rare', author: 'all', narrator: 'match', runtime: '1pct', sources: 'both', popularity: 'medium' }, p_none: 0, second: 0, ...extra })
  const files = (title: string, narrator?: string) => ({ kind: 'book', status: 'review', added: 1762000000, chosen: { title, author: 'Mara Quill', ...(narrator ? { narrator } : {}), year: 2015 }, tags: { album: `${title} (Unabridged)`, artist: 'Mara Quill', files: 12 }, folder: { title, folder: `Mara Quill - ${title}` }, copies: [{ minutes: 600, files: 12, bytes: 1, folder: `Mara Quill - ${title}`, path: `/x/${title}`, group_id: 7 }] })
  return [
    { listen_id: 1, p: 0.99, label: 'Glass Orchard', cursor: '0.99:1', candidates: [cand(11, 0.99, 'Glass Orchard')], ...files('Glass Orchard', 'Ada Penn') },
    { listen_id: 2, p: 0.6, label: 'Salt Roads', cursor: '0.6:2', candidates: [cand(21, 0.6, 'Salt Roads', { evidence: { title: 'partial', author: 'conflict', runtime: 'far' }, second: 0.55 }), cand(22, 0.3, 'Salt Roads Again', { ordinal: 2, authors: [], year: null })], ...files('Salt Roads'), added: 1762500000 },
    { listen_id: 3, p: 0.4, label: 'Lune', cursor: '0.4:3', candidates: [cand(31, 0.4, 'Lune', { recordings: [], isbns: [], ol: [], evidence: {} })], ...files('Lune'), added: 1761000000 },
    { listen_id: 4, p: 0.2, label: 'Untagged folder', cursor: '0.2:4', candidates: [cand(41, 0.2, 'x', { title: null, authors: [], year: null, editions: null, isbns: [], recording: null, second: null, recordings: [{ asin: 'B0AAAAAAA9', title: 'x', narrators: [], minutes: null, fmt: 'original_recording', year: null }] })],
      kind: 'radio_series', kind_info: kindInfos.series, status: 'review', added: null, chosen: {}, tags: { composer: 'Someone Else', publisher: 'A Press' }, folder: {}, copies: [{ minutes: 60, files: null, bytes: null, folder: 'Untagged folder', path: null, group_id: null }, { minutes: 61, files: 3, bytes: 1, folder: 'Copy', path: null, group_id: null }] },
  ]
}
export const matchState: { queue: import('../../src/api').QueueItem[]; decided: { listen_id: number; action: string; part_id?: number }[]; retrains: number; training: boolean; model: string | null } = { queue: matchQueue(), decided: [], retrains: 0, training: false, model: 'fs-test' }

export const resetMock = () => {
  matchState.queue = matchQueue(); matchState.decided = []; matchState.retrains = 0; matchState.training = false; matchState.model = 'fs-test'
  mock.settings = { ...defaultSettings }
  resetCovers()
  resetConfig()
  mock.books = [...initialBooks]
  mock.extras = {}
  mock.processed = null
  mock.queue = [
    { book_id: 1, title: 'Dune', author: 'Frank Herbert', state: 'proposed', error: '', updated: '2026-10-30T12:00:00+00:00', summary: planSummary() },
    { book_id: 2, title: 'Emma', author: 'Jane Austen', state: 'proposed', error: '', updated: '2026-10-30T12:01:00+00:00', summary: planSummary({ warnings: [] }) },
  ]
}

// Every endpoint the app calls has a handler here; one without a handler fails the test (setup.ts: onUnhandledFrame 'error').
const fgRoot = { id: 1, kind: 'root', label: 'audio', path: '/audio', file_count: 3, total_bytes: 3e9, group_count: 1 }
const fgDetail = {
  1: { group: fgRoot, ancestors: [], children: [{ id: 2, kind: 'folder', label: 'Red Dragon', path: '/audio/Red Dragon', file_count: 2, total_bytes: 2e9, group_count: 0, ordinal: 2 }], files: [{ id: 9, ordinal: 1, name: 'Intro.mp3', path: '/audio/Intro.mp3', size: 5e6, duration_s: 3725, codec: 'mp3', tags: { title: 'Intro', artist: 'Thomas Harris' }, probe_error: null }] },
  2: { group: { id: 2, kind: 'folder', label: 'Red Dragon', path: '/audio/Red Dragon', file_count: 2, total_bytes: 2e9, group_count: 0 }, ancestors: [{ id: 1, label: 'audio', kind: 'root' }], children: [], files: [{ id: 10, ordinal: 1, name: 'CD1 01.mp3', path: null, size: 1e9, duration_s: null, codec: null, tags: {}, probe_error: 'moov atom not found' }, { id: 11, ordinal: 2, name: 'CD1 02.mp3', path: null, size: 1e9, duration_s: 95, codec: 'aac', tags: {}, probe_error: null }] },
}
const listenRows = [
  { id: 1, label: 'Red Dragon', kind: 'book', status: 'grouped', files: 136, bytes: 2e9, minutes: 725, group_id: 2, path: '/audio/Red Dragon', copies: 3, parts: 0, author: 'Thomas Harris', narrator: 'Steven Weber', year: 1981, kind_info: null },
  { id: 2, label: 'Persuader', kind: 'book', status: 'grouped', files: 99, bytes: 5e8, minutes: 45, group_id: 3, path: '/audio/Lee Child/Persuader', copies: 1, parts: 2, author: 'Lee Child', narrator: null, year: null, kind_info: kindInfos.close },
  { id: 3, label: 'Dead Ringers (season 11)', kind: 'radio_series', status: 'grouped', files: 6, bytes: 1e8, minutes: 0, group_id: null, path: null, copies: 1, parts: 0, author: null, narrator: null, year: null, kind_info: kindInfos.series },
]
export const coverState: { rows: import('../../src/api').CoverRow[]; waiting: number; chosen: { listen: number; image: number | null; via?: string }[]; runs: { ids: number[] | null }[]; sentBack: number[]; job: import('../../src/api').CoverJob } = { rows: [], waiting: 0, chosen: [], runs: [], sentBack: [], job: { running: false, queued: 0, current: '', error: '', failed: [] } }
export function resetCovers() {
  const src = (source: string, ref: string, image_id: number, width = 500, height = 500, bytes = 80_000) => ({ source, ref, label: '', url: null, image_id, width, height, bytes })
  const sig = (name: string, weight: number, detail = '') => ({ name, weight, detail })
  coverState.rows = [
    { listen_id: 1, label: 'Glass Orchard', kind: 'book', run_id: 1, searched: '2026-11-12T10:00:00', state: 'choose', chosen: null,
      covers: [{ image_id: 11, width: 2400, height: 2400, bytes: 1_500_000, sources: [src('apple', '5', 11, 2400, 2400, 1_500_000), src('audible', 'B0A', 11, 2400, 2400, 1_500_000)], members: [11], score: 5.4, share: 0.93, signals: [sig('audiobook cover', 2, 'apple, audible'), sig('resolution', 1.2, '2400×2400'), { ...sig('this recording', 1.5), prior: 1, learned: true }] }, { image_id: 12, width: 500, height: 750, bytes: 80_000, sources: [src('google', '978', 12, 500, 750)], members: [12], score: 2.6, share: 0.07, signals: [sig('print edition only', -0.5, 'google')] }],
      sources: [{ source: 'audible', status: 'found', detail: '', count: 1 }, { source: 'openlibrary', status: 'error', detail: 'error: down', count: 0 }, { source: 'google', status: 'limited', detail: 'rate limited: 9780000000002: HTTP Error 429', count: 0 }] , source: { title: 'Glass Orchard', author: 'Mara Quill', narrator: 'Anna Bell', minutes: 600, year: '1999', files: 12, folder: 'Glass Orchard' }, match: { title: 'Glass Orchard', author: 'Mara Quill', narrator: 'Ben Hart', minutes: 690, year: '1999', recordings: 2, probability: 0.99, accepted_by: 'model' } },
    { listen_id: 2, label: 'Salt Roads', kind: 'book', run_id: 2, searched: '2026-11-12T10:05:00', state: 'none', chosen: null, covers: [], sources: [{ source: 'audible', status: 'skipped', detail: 'no ASIN known for this book', count: 0 }, { source: 'apple', status: 'none', detail: '8 results, none with a matching title', count: 0 }], source: { title: 'Salt Roads', author: '', narrator: '', minutes: null, year: '', files: null, folder: '' }, match: null },
    { listen_id: 3, label: 'Third Book', kind: 'radio_show', run_id: 3, searched: '2026-11-12T10:09:00Z', state: 'chosen', chosen: 31, covers: [{ image_id: 31, width: 800, height: 800, bytes: 90_000, sources: [src('folder', 'cover.jpg', 31, 800, 800, 90_000)], members: [31, 32], score: 1.8, share: 1, signals: [sig('from the listen\'s files', 1)] }], sources: [], source: { title: 'Third Book', author: 'Mara Quill', narrator: 'Anna Bell', minutes: 600, year: '1999', files: 12, folder: 'Glass Orchard' }, match: { title: 'Glass Orchard', author: 'Mara Quill', narrator: 'Ben Hart', minutes: 690, year: '1999', recordings: 2, probability: 0.99, accepted_by: 'model' } },
  ]
  coverState.waiting = 2; coverState.chosen = []; coverState.runs = []; coverState.sentBack = []; coverState.job = { running: false, queued: 0, current: '', error: '', failed: [] }
}
resetCovers()

export const configState: { picks: { field: string; value: unknown; source: string }[]; config: import('../../src/api').ListenConfig } = { picks: [], config: undefined as never }
export function resetConfig() {
  const src = (source: string, label: string, ref = '', note = '') => ({ source, label, ref, note })
  configState.picks = []
  configState.config = {
    listen_id: 1, label: 'Red Dragon', kind: 'book', status: 'identified', cover: null,
    work: { id: 7, title: 'Red Dragon', authors: ['Thomas Harris'], year: 1981 },
    recordings: [{ asin: 'B0A', region: 'uk', title: 'Red Dragon', narrators: ['Steven Weber'], runtime_minutes: 725, release: '2004-05-01', abridged: false }],
    inputs: [{ part_id: 1, group_id: 2, path: '/audio/Red Dragon', files: 24, minutes: 725, relation: 'copy' }],
    fields: [
      { field: 'title', label: 'Title', value: 'Red Dragon', chosen_by: 'rule', options: [{ value: 'Red Dragon', chosen: true, sources: [src('tags', 'Embedded tag', 'album', '24 of 24 tagged files'), src('openlibrary', 'Open Library work')] }, { value: 'The Red Dragon', chosen: false, sources: [src('folder', 'Folder name')] }] },
      { field: 'narrator', label: 'Narrator', value: null, chosen_by: null, options: [{ value: 'Steven Weber', chosen: false, sources: [src('audible', 'Audible UK', 'B0A')] }] },
      { field: 'subtitle', label: 'Subtitle', value: null, chosen_by: null, options: [] },
    ],
  }
}
resetConfig()

export const handlers = [
  http.get('/api/listens/:id/config', () => HttpResponse.json(configState.config)),
  http.post('/api/listens/:id/config/:field', async ({ request, params }) => {
    const b = await request.json() as { value: string | number | null; source: string }
    if (params.field === 'year' && b.value === 'soon') return HttpResponse.json({ detail: 'the year must be four digits' }, { status: 400 })
    configState.picks.push({ field: String(params.field), value: b.value, source: b.source })
    configState.config = { ...configState.config, fields: configState.config.fields.map(f => f.field !== params.field ? f : { ...f, value: b.value, chosen_by: b.value == null ? null : 'user', options: f.options.map(o => ({ ...o, chosen: o.value === b.value })) }) }
    return HttpResponse.json(configState.config)
  }),
  http.get('/api/covers', ({ request }) => {
    const u = new URL(request.url), state = u.searchParams.get('state') ?? '', q = (u.searchParams.get('q') ?? '').toLowerCase()
    const rows = coverState.rows.filter(r => (!state || r.state === state) && q.split(' ').filter(Boolean).every(w => r.label.toLowerCase().includes(w)))
    const count = (k: string) => coverState.rows.filter(r => r.state === k).length
    return HttpResponse.json({ total: rows.length + (q === 'big' ? 30 : 0), counts: { choose: count('choose'), chosen: count('chosen'), none: count('none') }, waiting: coverState.waiting, rows, job: coverState.job })
  }),
  http.post('/api/covers/run', async ({ request }) => { const b = await request.json() as { listen_ids: number[] | null }; coverState.runs.push({ ids: b.listen_ids }); return HttpResponse.json({ queued: 1, listen_ids: b.listen_ids ?? [] }, { status: 202 }) }),
  http.post('/api/covers/:id/unaccept', ({ params }) => { coverState.sentBack.push(Number(params.id)); return HttpResponse.json({ ok: true }) }),
  http.post('/api/covers/:id/choose', async ({ request, params }) => {
    const b = await request.json() as { image_id: number | null; via: string }
    coverState.chosen.push({ listen: Number(params.id), image: b.image_id, via: b.via })
    coverState.rows = coverState.rows.map(r => r.listen_id === Number(params.id) ? { ...r, chosen: b.image_id, state: b.image_id ? 'chosen' : r.covers.length ? 'choose' : 'none' } : r)
    return HttpResponse.json({ ok: true, chosen: b.image_id })
  }),
  http.post('/api/jobs/:id/retry', () => HttpResponse.json({ queued: true })),
  http.get('/api/operations', () => HttpResponse.json({ rows: [], busy: false })),
  http.get('/api/finished', () => HttpResponse.json([])),
  http.get('/api/books/:id/output', () => HttpResponse.json({ book: {}, metadata: null, chapters: [] })),
  http.get('/api/listens', ({ request }) => {
    const u = new URL(request.url), q = (u.searchParams.get('q') ?? '').toLowerCase(), kind = u.searchParams.get('kind') ?? ''
    const rows = listenRows.filter(r => (!kind || (kind === 'review' ? r.kind_info?.band === 'review' : kind === 'not-book' ? r.kind !== 'book' : r.kind === kind)) && q.split(' ').filter(Boolean).every(w => `${r.label} ${r.path}`.toLowerCase().includes(w)))
    return HttpResponse.json({ total: rows.length + (q === 'big' ? 40 : 0), kinds: { book: 2, radio_series: 1 }, review: 1, not_book: 1, rows })
  }),
  http.post('/api/listens/:id/kind', async ({ request, params }) => {
    const b = await request.json() as { kind: string }
    kindState.chosen.push({ id: Number(params.id), kind: b.kind })
    return HttpResponse.json({ ok: true, retraining: false, kind_info: { ...kindInfos.close, kind: b.kind, decided_by: 'user', band: b.kind === 'book' ? 'book' : 'broadcast' } })
  }),
  http.get('/api/filegroups', () => HttpResponse.json({ root: fgRoot, groupings: 1 })),
  http.get('/api/filegroups/search', ({ request }) => {
    const q = (new URL(request.url).searchParams.get('q') ?? '').toLowerCase()
    return q.includes('dragon') ? HttpResponse.json({ groups: [fgDetail[2].group], files: [{ id: 10, name: 'CD1 01.mp3', path: '/audio/Red Dragon/CD1 01.mp3', group_id: 2, group_label: 'Red Dragon' }], more: { groups: 0, files: 3 } }) : HttpResponse.json({ groups: [], files: [], more: { groups: 0, files: 0 } })
  }),
  http.get('/api/filegroups/files/:id', ({ params }) => HttpResponse.json({ id: Number(params.id), sha256: 'ab'.repeat(32), size: 5e6, duration_s: 3725, codec: 'mp3', channels: 2, sample_rate: 44100, bit_rate: 64000, probe: params.id === '9' ? { format: { tags: { comment: 'unabridged' } } } : null, probe_error: params.id === '9' ? null : 'moov atom not found', locations: [{ path: '/audio/Intro.mp3', gone: false }, { path: '/old/Intro.mp3', gone: true }] })),
  http.get('/api/filegroups/:id', ({ params }) => params.id === '1' ? HttpResponse.json(fgDetail[1]) : params.id === '2' ? HttpResponse.json(fgDetail[2]) : HttpResponse.json({ detail: 'no such group' }, { status: 404 })),
  http.get('/api/health', () => HttpResponse.json({ ok: true, version: '0.1.0' })),
  http.get('/api/settings', () => HttpResponse.json(mock.settings)),
  http.put('/api/settings', async ({ request }) => {
    const body = (await request.json()) as Partial<Settings>
    if (body.data_dir && !body.data_dir.startsWith('/')) return HttpResponse.json({ detail: 'the data folder must be an absolute path' }, { status: 400 })
    mock.settings = { ...mock.settings, ...body }
    if (body.library_dirs) mock.settings.library_dirs = [mock.settings.library_dir, ...body.library_dirs].filter(Boolean)   // the server returns library_dir first, then the extra folders
    return HttpResponse.json(mock.settings)
  }),
  http.get('/api/books', ({ request }) => {
    const u = new URL(request.url), q = (u.searchParams.get('q') || '').toLowerCase(), status = u.searchParams.get('status'), review = u.searchParams.get('review'), kind = u.searchParams.get('kind')
    return HttpResponse.json(mock.books.filter(b => (!status || b.status === status) && (!review || b.review === review) && (!kind || b.kind === kind) && (!q || `${b.title} ${b.author}`.toLowerCase().includes(q))))
  }),
  http.get('/api/library/workflow', () => HttpResponse.json({ books: {} })),
  http.get('/api/books/:id/source', ({ params }) => {
    const book = mock.books.find(b => b.id === Number(params.id))
    return HttpResponse.json(book?.path ? { folder: '/inbox', files: [book.path.split('/').pop()], count: book.file_count ?? 1, available: true } : { folder: '', files: [], count: 0, available: false })
  }),
  http.post('/api/books', async ({ request }) => {
    const b = (await request.json()) as Partial<Book>
    if (!(b.title || '').trim()) return HttpResponse.json({ detail: 'a title is required' }, { status: 400 })
    const entry: Book = { id: mock.books.length + 1, title: b.title!.trim(), author: b.author || '', narrator: '', series: '', series_position: null, duration_seconds: null, path: '', status: 'unlistened', added: '2026-10-29T12:01:00Z', asin: '', kind: 'book', review: 'confirmed', file_count: null, suggestion: null, match_score: null, year: null, year_source: '' }
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
  http.get('/api/process', () => HttpResponse.json({ rows: mock.queue, worker: { busy: mock.queue.some(r => r.state === 'running') ? 'run' : null, queued: 0, last: {}, counts: {} }, result: { 3: { in_bytes: 300_000_000, out_bytes: 140_000_000, loudness_after: -18.1, true_peak_after: -1.6 } } })),
  http.post('/api/process/propose', () => {
    mock.queue = [...mock.queue, { book_id: 3, title: 'Persuasion', author: 'Jane Austen', state: 'proposed', error: '', updated: '2026-10-30T12:05:00+00:00', summary: planSummary({ hours: 1.5, tiers: { copy: 3 } }) }]
    return HttpResponse.json({ rows: mock.queue, worker: { busy: null, queued: 0, last: {}, counts: {} }, result: {} })
  }),
  http.post('/api/process/approve', async ({ request }) => {
    const { ids } = (await request.json()) as { ids: number[] }
    mock.queue = mock.queue.map(r => (ids.includes(r.book_id) && ['proposed', 'rejected'].includes(r.state) ? { ...r, state: 'approved' } : r))
    return HttpResponse.json({ changed: ids })
  }),
  http.post('/api/process/reject', async ({ request }) => {
    const { ids } = (await request.json()) as { ids: number[] }
    mock.queue = mock.queue.map(r => (ids.includes(r.book_id) ? { ...r, state: 'rejected' } : r))
    return HttpResponse.json({ changed: ids })
  }),
  http.post('/api/process/repropose', async ({ request }) => {
    const { ids } = (await request.json()) as { ids: number[] }
    mock.queue = mock.queue.filter(r => !ids.includes(r.book_id))
    return HttpResponse.json({ changed: ids })
  }),
  http.post('/api/process/run', () => {
    if (!mock.queue.some(r => r.state === 'approved')) return HttpResponse.json({ detail: 'nothing is approved yet' }, { status: 400 })
    mock.queue = mock.queue.map(r => (r.state === 'approved' ? { ...r, state: 'done' } : r))
    return HttpResponse.json({ rows: mock.queue, worker: { busy: null, queued: 0, last: {}, counts: {} }, result: {} })
  }),
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

export const server = setupServer(
  http.get('/api/matches', ({ request }) => {
    // The order and filters as the server applies them (src/app/linkage/queue.py), over the invented queue; the cursor is the listen id, which is enough here.
    const u = new URL(request.url), qp = (k: string) => u.searchParams.get(k) ?? ''; const after = qp('after'); const limit = Number(u.searchParams.get('limit') ?? 20)
    const open = matchState.queue.filter(i => !matchState.decided.some(d => d.listen_id === i.listen_id))
    const band = (i: typeof open[number]) => { const t = i.candidates[0], second = t.second ?? 0; return t.p >= 0.95 ? 'sure' : t.p - second < 0.15 && second > 0.05 ? 'close' : t.p >= 0.7 ? 'likely' : 'unsure' }
    const words = qp('q').toLowerCase().split(/\s+/).filter(Boolean)
    const fits = open.filter(i => (qp('auto') !== 'skip' || !i.candidates[0].auto) && (qp('auto') !== 'only' || i.candidates[0].auto) && (!qp('kind') || (qp('kind') === 'review' ? i.kind_info?.band === 'review' : qp('kind') === 'not-book' ? i.kind !== 'book' : i.kind === qp('kind'))) && (!qp('status') || i.status === qp('status'))
      && (!qp('certainty') || band(i) === qp('certainty')) && (!qp('audible') || (i.candidates[0].recordings.length > 0) === (qp('audible') === 'yes'))
      && words.every(w => `${i.label} ${Object.values(i.chosen).join(' ')} ${i.candidates.map(c => `${c.title} ${c.authors.join(' ')}`).join(' ')}`.toLowerCase().includes(w)))
    const sort = qp('sort') || 'certain', key = (i: typeof open[number]) => ({ certain: -i.p, uncertain: i.p, recent: -(i.added ?? 0), oldest: i.added ?? 0, 'kind-certain': -(i.kind_info?.confidence ?? 1), 'kind-uncertain': i.kind_info?.confidence ?? 1 } as Record<string, number>)[sort]
    const ordered = [...fits].sort((a, b) => key(a) - key(b) || (sort === 'recent' ? b.listen_id - a.listen_id : a.listen_id - b.listen_id))
    const start = after ? ordered.findIndex(i => i.cursor === after) + 1 : 0
    const count = (f: (i: typeof open[number]) => string | null) => open.reduce<Record<string, number>>((m, i) => { const v = f(i); if (v) m[v] = (m[v] ?? 0) + 1; return m }, {})
    const facets = { kind: { ...count(i => i.kind), ...(open.some(i => i.kind_info?.band === 'review') ? { review: open.filter(i => i.kind_info?.band === 'review').length } : {}), ...(open.some(i => i.kind !== 'book') ? { 'not-book': open.filter(i => i.kind !== 'book').length } : {}) }, status: count(i => i.status), certainty: count(band), auto: { skip: 0, only: 0, ...count(i => i.candidates[0].auto ? 'only' : 'skip') }, audible: { yes: 0, no: 0, ...count(i => i.candidates[0].recordings.length ? 'yes' : 'no') } }
    return HttpResponse.json({ summary: { waiting: open.length, auto_unconfirmed: open.filter(i => i.candidates[0].auto).length, confirmed: matchState.decided.filter(d => d.action === 'approve').length, not_found: matchState.decided.filter(d => d.action === 'none').length, statuses: {}, decisions: matchState.decided.length, since_train: null, model: matchState.model, auto_retrain: 25, training: { running: matchState.training, finished: null, error: '', counts: null } },
      total: fits.length, facets, items: ordered.slice(start, start + limit) })
  }),
  http.post('/api/matches/decide', async ({ request }) => {
    const b = await request.json() as { listen_id: number; action: string; part_id?: number }
    matchState.decided.push(b); return HttpResponse.json({ ok: true, status: 'identified', retraining: false })
  }),
  http.post('/api/matches/retrain', () => { matchState.retrains++; return HttpResponse.json({ started: true }) }),
  http.get('/api/jobs/:id', () => HttpResponse.json({ state: 'done', result: {}, error: '' })),
  http.get('/api/ingest', () => HttpResponse.json(ingestFixture())),
  http.post('/api/ingest/run', () => HttpResponse.json({ queued: true })),
  http.get('/api/ingest/runs/:id', () => HttpResponse.json({ evidence: 'preserved' })),
  http.get('/api/ingest/runs/:id/samples', ({ params }) => HttpResponse.json({ samples: Array.from({ length: 5 }, (_, index) => ({ index, start_seconds: 60 + index * 600, duration_seconds: 30, status: 'ready', url: `/api/ingest/runs/${params.id}/samples/${index}` })) })),
  http.post('/api/ingest/runs/:id/samples', ({ params }) => HttpResponse.json({ samples: Array.from({ length: 5 }, (_, index) => ({ index, start_seconds: 60 + index * 600, duration_seconds: 30, status: 'ready', url: `/api/ingest/runs/${params.id}/samples/${index}` })) })),
  http.post('/api/ingest/runs/:id/resume', () => HttpResponse.json({ queued: true })),
  http.post('/api/ingest/runs/:id/approve/:phase', () => HttpResponse.json({ queued: true, changed: true })),
  http.post('/api/ingest/runs/:id/evaluate', () => HttpResponse.json({ eligible: false })),
  http.put('/api/ingest/runs/:id/recording', async ({ request }) => HttpResponse.json(await request.json())),
  http.put('/api/ingest/runs/:id/quality', () => HttpResponse.json({ rating: 'medium' })),
  http.post('/api/ingest/runs/:id/enqueue', () => HttpResponse.json({ book_id: 1, approved: false })),
  http.put('/api/ingest/policy', async ({ request }) => HttpResponse.json(await request.json())),
...handlers)
