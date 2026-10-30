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
