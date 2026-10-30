import { describe, expect, it } from 'vitest'
import BookPage from '../../src/components/BookPage'
import App from '../../src/App'
import { fireEvent } from '@testing-library/react'
import { setup, screen } from '../utils/render'
import { http, HttpResponse } from 'msw'
import { mock, server } from '../utils/server'

describe('BookPage', () => {
  it('shows the catalogue fields, Audible metadata, files, tags and a cover', async () => {
    setup(<BookPage id={1} />)
    expect(await screen.findByRole('heading', { name: 'Dune' })).toBeInTheDocument()
    expect(screen.getByAltText('Cover of Dune')).toHaveAttribute('src', '/api/books/1/cover')
    expect(await screen.findByText('Audible metadata file')).toBeInTheDocument()
    expect(screen.getByText('Scott Brick')).toBeInTheDocument()
    expect(screen.getByText('B000TEST01')).toBeInTheDocument()
    expect(screen.getByText('4.6 (12,000 ratings)')).toBeInTheDocument()
    expect(screen.getByText('A desert planet.')).toBeInTheDocument()
    expect(screen.getByText('2 audio files · 4.8 MB')).toBeInTheDocument()
    expect(screen.getByText('mp3, 64 kbps')).toBeInTheDocument()
    expect(screen.getByText('Embedded tags (first file)')).toBeInTheDocument()
    expect(screen.getAllByText('Frank Herbert', { selector: 'dd' }).length).toBeGreaterThan(1)
    expect(screen.getByText('1 chapters')).toBeInTheDocument()
    fireEvent.error(screen.getByAltText('Cover of Dune'))               // a book without a cover hides the broken image
    expect(screen.getByAltText('Cover of Dune')).toHaveStyle({ visibility: 'hidden' })
  })

  it('shows a suggestion, a hand-entered book without files, and not-found errors', async () => {
    mock.books[0] = { ...mock.books[0], review: 'suggested', match_score: 0.7, suggestion: { title: 'Dune Messiah', author: 'Frank Herbert', narrators: ['Scott Brick'], runtime_minutes: 1260, asin: 'B0X', series: 'Dune Chronicles', series_position: 2, matched_by: 'title', sources: ['audible', 'audiobookdb'], alternatives: [{ source: 'audible', title: 'Dune', author: 'Frank Herbert', asin: 'B0Y', score: 0.7 }, { source: 'audible', title: 'Other', author: '', asin: '', score: 0.61 }] } }
    mock.books.push({ ...mock.books[0], id: 2, title: 'By hand', suggestion: null, review: 'confirmed', path: '' })
    const { unmount } = setup(<BookPage id={1} />)
    expect(await screen.findByText('Match suggestion')).toBeInTheDocument()
    expect(screen.getByText('70% match (check)')).toBeInTheDocument()
    expect(screen.getByText('audible + audiobookdb')).toBeInTheDocument()
    expect(screen.getByText('2 other candidates')).toBeInTheDocument()
    expect(screen.getByText(/Other/, { selector: 'li' })).toHaveTextContent('unknown')
    unmount()
    setup(<BookPage id={2} />)
    expect(await screen.findByText('This book has no path (entered by hand).')).toBeInTheDocument()
  })

  it('reports a missing book and is reachable from the library', async () => {
    const { unmount } = setup(<BookPage id={99} />)
    expect(await screen.findByRole('alert')).toHaveTextContent('no such book')
    unmount()
    window.location.hash = '#/'
    const { user } = setup(<App />)
    await user.click(await screen.findByRole('link', { name: 'Dune' }))
    expect(await screen.findByRole('heading', { name: 'Dune' })).toBeInTheDocument()
  })
})

describe('BookPage with sparse or partial data', () => {
  const sparse = (over: object) => {
    const book = { ...mock.books[0], id: 5, title: 'Sparse', author: '', series: 'Saga', series_position: 3, kind: 'radio' as const, review: 'new' as const, duration_seconds: null, suggestion: { title: 'Sparse Match', matched_by: 'asin' }, match_score: 1 }
    mock.books.push(book)
    server.use(http.get('/api/books/5/details', () => HttpResponse.json({ book, path_exists: true, files: { count: 1, total_bytes: 900, truncated: true, list: [{ name: 'a.m4b', bytes: 900 }] }, probe: null, audible: null, ...over })))
  }

  it('copes with no ffprobe, a partial probe, and a thin Audible file', async () => {
    sparse({ probe: { available: false } })
    const { unmount } = setup(<BookPage id={5} />)
    expect(await screen.findByText('1+ audio file · 900 B')).toBeInTheDocument()
    expect(screen.getByText('Install ffmpeg to read tags and durations.')).toBeInTheDocument()
    expect(screen.getByText(/Saga #3/, { selector: 'p' })).toBeInTheDocument()
    expect(screen.getByText('unknown author', { exact: false })).toBeInTheDocument()
    unmount()

    server.use(http.get('/api/books/5/details', () => HttpResponse.json({
      book: mock.books.at(-1), path_exists: true, files: { count: 90, total_bytes: 2048, truncated: false, list: [] },
      probe: { available: true, files_probed: 60, complete: false, duration_seconds: 7200, codec: 'aac', bitrate_kbps: null, tags: {} },
      audible: { asin: 'B0', title: 'Thin', subtitle: '', authors: [], narrators: [], series: [], publisher: '', release_date: '', language: '', format: '', runtime_minutes: null, summary: '', rating: null, rating_count: null, categories: [], sample_url: '', chapters: [], cover_url: '' },
    })))
    setup(<BookPage id={5} />)
    expect(await screen.findByText('2h 00m (first 60 files)')).toBeInTheDocument()
    expect(screen.getByText('aac')).toBeInTheDocument()
    expect(screen.queryByText(/chapters$/)).toBeNull()
    expect(screen.queryByText('Embedded tags (first file)')).toBeNull()
  })
})

describe('BookPage suggestion with few details', () => {
  it('handles a suggestion without sources, narrators or runtime and a single alternative', async () => {
    const book = { ...mock.books[0], id: 7, title: 'Bare', review: 'suggested' as const, match_score: 0.9, suggestion: { title: 'Bare Match', alternatives: [{ source: 'audiobookdb', title: 'Alt', author: 'Some One', asin: '', score: 0.65 }] } }
    mock.books.push(book)
    server.use(http.get('/api/books/7/details', () => HttpResponse.json({ book, path_exists: false, files: null, probe: null, audible: null })))
    setup(<BookPage id={7} />)
    expect(await screen.findByText('1 other candidate')).toBeInTheDocument()
    expect(screen.getByText('90% match')).toBeInTheDocument()
    expect(screen.queryByText('Found by')).toBeNull()
  })
})


describe('BookPage community ratings and reviews', () => {
  it('fetches from Hardcover, shows ratings, reviews and spoilers, and reports errors', async () => {
    const { user } = setup(<BookPage id={1} />)
    expect(await screen.findByText('Not fetched yet.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Fetch from Hardcover' }))
    expect(await screen.findByText('4.31 / 5 from 12,345 ratings')).toBeInTheDocument()
    expect(screen.getByText('5,000 (4,000 finished)')).toBeInTheDocument()
    expect(screen.getByText('Violence')).toBeInTheDocument()
    expect(screen.getByText('Macmillan Audio 21 h')).toBeInTheDocument()
    expect(screen.getByText('A desert planet saga.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'hardcover.app' })).toHaveAttribute('href', 'https://hardcover.app/books/dune')
    await user.click(screen.getByText('3 top reviews'))
    expect(screen.getByText('A masterpiece.')).toBeInTheDocument()
    expect(screen.getByText(/^x+…$/)).toBeInTheDocument()                       // long reviews are cut
    expect(screen.getByText('Contains spoilers')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Refresh from Hardcover' })).toBeInTheDocument()

    server.use(http.post('/api/books/1/enrich', () => HttpResponse.json({ detail: 'Hardcover is not available' }, { status: 502 })))
    await user.click(screen.getByRole('button', { name: 'Refresh from Hardcover' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Hardcover is not available')
  })

  it('says so when the book is not on Hardcover, and offers nothing for unconfirmed books', async () => {
    mock.extras = { hardcover: { fetched: '2026-10-30T12:00:00+00:00', data: { not_found: true } } }
    const { unmount } = setup(<BookPage id={1} />)
    expect(await screen.findByText('This book was not found on Hardcover.')).toBeInTheDocument()
    unmount()
    mock.books[0] = { ...mock.books[0], review: 'new' }
    setup(<BookPage id={1} />)
    await screen.findByRole('heading', { name: 'Dune' })
    expect(screen.queryByText('Community ratings and reviews')).toBeNull()
  })
})

describe('BookPage with thin Hardcover data', () => {
  it('shows only what Hardcover has', async () => {
    mock.extras = { hardcover: { fetched: '2026-10-30T12:00:00+00:00', data: { id: '5', rating: null, reviews_count: 0, audiobook_editions: [{ asin: '', hours: null, publisher: '', released: '', format: '' }], series: [{ name: 'Saga', position: null }], reviews: [{ user: 'r', rating: 4, text: 'short', spoilers: false, likes: 0, date: '2022-02-02' }] } } }
    const { user } = setup(<BookPage id={1} />)
    expect(await screen.findByText('Saga')).toBeInTheDocument()
    expect(screen.queryByText(/ratings$/)).toBeNull()
    expect(screen.getByRole('link', { name: 'hardcover.app' })).toHaveAttribute('href', 'https://hardcover.app')
    await user.click(screen.getByText('1 top reviews'))
    expect(screen.getByText('r · 4★ · 2022-02-02 · 0 likes')).toBeInTheDocument()
    mock.extras = { hardcover: { fetched: '2026-10-30T12:00:00+00:00', data: { id: '6' } } }
  })
})


describe('BookPage when the audio share is down', () => {
  it('explains why there are no files and keeps the rest of the page', async () => {
    const book = { ...mock.books[0], id: 8, title: 'Offline' }
    mock.books.push(book)
    server.use(http.get('/api/books/8/details', () => HttpResponse.json({ book, extras: {}, path_exists: false, share_down: true, files: null, probe: null, audible: null })))
    setup(<BookPage id={8} />)
    expect(await screen.findByText(/network share that is not reachable right now/)).toBeInTheDocument()
    expect(screen.getByText('Catalogue')).toBeInTheDocument()
  })
})


describe('BookPage final library section', () => {
  it('shows what processing produced, and a failure', async () => {
    mock.processed = { status: 'done', out_dir: '/home/u/audiobooks/A/Dune', chapters: 12, chapter_source: 'Audible chapter list', tiers: { V5: 11, V4: 1 }, in_bytes: 400_000_000, out_bytes: 190_000_000, loudness_before: -22.1, loudness_after: -18.0, true_peak_after: -1.7, retries: 1, warnings: ['peaks limit the boost'], finished: '2026-10-30T12:00:00+00:00' }
    const { unmount } = setup(<BookPage id={1} />)
    expect(await screen.findByText('Final library')).toBeInTheDocument()
    expect(screen.getByText('/home/u/audiobooks/A/Dune')).toBeInTheDocument()
    expect(screen.getByText('12 (from Audible chapter list)')).toBeInTheDocument()
    expect(screen.getByText('11× V5 · 1× V4')).toBeInTheDocument()
    expect(screen.getByText('381 MB → 181 MB (48%)')).toBeInTheDocument()
    expect(screen.getByText('-22.1 → -18.0 LUFS')).toBeInTheDocument()
    expect(screen.getByText('-1.7 dBTP (gain lowered 1×)')).toBeInTheDocument()
    unmount()
    mock.processed = { status: 'failed', error: 'ffmpeg failed on chapter 3' }
    setup(<BookPage id={1} />)
    expect(await screen.findByText(/Processing failed: ffmpeg failed on chapter 3/)).toBeInTheDocument()
  })

  it('shows sizes in bytes, KB and GB and omits missing values', async () => {
    mock.processed = { status: 'done', out_dir: '/x', chapters: 1, chapter_source: 'whole file', in_bytes: 5 * 1024 ** 3, out_bytes: 900 }
    setup(<BookPage id={1} />)
    expect(await screen.findByText('5.0 GB → 900 B (0%)')).toBeInTheDocument()
    expect(screen.queryByText('Loudness')).toBeNull()
  })
})
