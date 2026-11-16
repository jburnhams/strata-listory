import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { describe, expect, it, vi } from 'vitest'
import type { EnrichmentSummary } from '../../src/api'
import { EnrichmentPanel, RatingsLine } from '../../src/components/EnrichmentPanel'
import { server } from '../utils/server'

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>)
}

const fullEnrichment: EnrichmentSummary = {
  status: 'done',
  fetched_at: '2026-11-16T12:00:00',
  summary: 'Crisp spoiler-free summary of Dune.',
  summary_model: 'claude-haiku-5-5-api',
  synopsis: { source: 'hardcover', label: 'Hardcover', text: 'A desert epic.', url: 'https://hardcover.app/books/dune' },
  synopses: [
    { source: 'hardcover', label: 'Hardcover', text: 'A desert epic.', url: 'https://hardcover.app/books/dune' },
    { source: 'audible', label: 'Audible', text: 'Audible blurb.', url: null },
  ],
  ratings: [
    { source: 'hardcover', label: 'Hardcover', rating: 4.4, count: 1200, reviews_count: 80, url: 'https://hardcover.app/books/dune' },
    { source: 'openlibrary_local', label: 'Open Library', rating: 4.1, count: 50, reviews_count: null, url: null },
  ],
  reviews: [
    { source: 'hardcover', label: 'Hardcover', user: 'paul', rating: 5, title: '', text: 'Brilliant.', spoilers: false, likes: 10, date: '2025-01-02', url: 'https://hardcover.app/books/dune' },
    { source: 'audible', label: 'Audible', user: 'chani', rating: 4, title: 'Great audio', text: 'Spoiler ending!', spoilers: true, likes: 2, date: '2025-02-01', url: null },
  ],
  tags: { genres: ['Sci-Fi'], moods: ['Epic'], content_warnings: ['Violence'] },
  sources: [
    { source: 'hardcover', status: 'found', detail: '', count: 1 },
    { source: 'applebooks', status: 'none', detail: 'no match', count: 0 },
  ],
}

describe('EnrichmentPanel', () => {
  it('renders RatingsLine for queued, populated, and empty states', () => {
    const { rerender } = render(<RatingsLine enrichment={{ ...fullEnrichment, status: 'queued' }} />)
    expect(screen.getByText(/Fetching ratings, synopsis/)).toBeInTheDocument()

    rerender(<RatingsLine enrichment={fullEnrichment} />)
    expect(screen.getByText('4.4★')).toBeInTheDocument()
    expect(screen.getByText(/Open Library/)).toBeInTheDocument()

    const { container } = render(<RatingsLine enrichment={{ ...fullEnrichment, ratings: [] }} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('renders ratings, switches synopses, reveals spoilers, and refreshes', async () => {
    const onRefreshed = vi.fn()
    wrap(<EnrichmentPanel listenId={7} enrichment={fullEnrichment} onRefreshed={onRefreshed} />)

    expect(screen.getByText('Crisp spoiler-free summary of Dune.')).toBeInTheDocument()
    expect(screen.getByText('A desert epic.')).toBeInTheDocument()
    expect(screen.getByText('Sci-Fi')).toBeInTheDocument()
    expect(screen.getByText('Epic')).toBeInTheDocument()
    expect(screen.getByText('Violence')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Audible' }))
    expect(screen.getByText('Audible blurb.')).toBeInTheDocument()

    expect(screen.queryByText('Spoiler ending!')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Contains spoilers/ }))
    expect(screen.getByText('Spoiler ending!')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Refresh details' }))
    await waitFor(() => expect(onRefreshed).toHaveBeenCalledTimes(1))
  })

  it('renders empty state and surfaces fetch errors', async () => {
    server.use(http.post('/api/enrichment/:id/fetch', () => HttpResponse.json({ detail: 'queue offline' }, { status: 503 })))
    const { rerender } = wrap(
      <EnrichmentPanel
        listenId={7}
        enrichment={{ status: 'none', fetched_at: null, synopsis: null, synopses: [], ratings: [], reviews: [], tags: { genres: [], moods: [], content_warnings: [] }, sources: [] }}
        onRefreshed={vi.fn()}
      />,
    )
    expect(screen.getByText(/No ratings, synopsis, or reviews fetched yet/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Fetch details' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/queue offline|503/)

    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    rerender(
      <QueryClientProvider client={qc}>
        <EnrichmentPanel
          listenId={7}
          enrichment={{ ...fullEnrichment, status: 'running', synopses: [{ source: 'audible', label: 'Audible', text: 'Only one.', url: null }], reviews: [{ ...fullEnrichment.reviews[0], text: 'x'.repeat(750), url: null }] }}
          onRefreshed={vi.fn()}
        />
      </QueryClientProvider>,
    )
    expect(screen.getByText('Running')).toBeInTheDocument()
    expect(screen.getByText(/^x{700}…$/)).toBeInTheDocument()
  })
})
