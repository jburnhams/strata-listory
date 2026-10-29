import { describe, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'
import LibraryPage from '../../src/components/LibraryPage'
import { setup, screen } from '../utils/render'
import { server } from '../utils/server'

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
    expect(await screen.findByText('No books in the catalogue yet.')).toBeInTheDocument()
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
