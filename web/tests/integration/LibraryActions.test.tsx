import { describe, expect, it } from 'vitest'
import LibraryPage from '../../src/components/LibraryPage'
import { fireEvent } from '@testing-library/react'
import { setup, screen } from '../utils/render'
import { mock, server } from '../utils/server'
import { http, HttpResponse } from 'msw'

describe('LibraryPage row actions', () => {
  it('reports a failed update when the book vanished,', async () => {
    const { user } = setup(<LibraryPage />)
    await screen.findByText('1 book')
    mock.books = []
    await user.selectOptions(screen.getByLabelText('Status of Dune'), 'listening')
    expect(await screen.findByRole('alert')).toHaveTextContent('no such book')
  })
})

describe('scan, match and review', () => {
  it('scans, matches, then confirms a suggestion and ignores another item', async () => {
    const { user } = setup(<LibraryPage />)
    await screen.findByText('1 book')
    await user.click(screen.getByRole('button', { name: 'Scan audiobook folder' }))
    expect(await screen.findByText('Scanned: 2 found, 2 new, 0 already known.')).toBeInTheDocument()
    expect(await screen.findByText('2 books')).toBeInTheDocument()          // the radio item is hidden until "Show radio"
    expect(screen.getByText('dune messiah')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Match new books' }))
    expect(await screen.findByText('Matched 1: 1 suggestions, 0 without a match, 0 still to try.')).toBeInTheDocument()
    expect(await screen.findByText('70% match (check)')).toBeInTheDocument()
    expect(screen.getByText('Dune Messiah')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'audiobookdb.org' })).toHaveAttribute('href', 'https://audiobookdb.org')

    await user.click(screen.getByRole('button', { name: 'Confirm dune messiah' }))
    expect(await screen.findByRole('button', { name: 'Remove Dune Messiah' })).toBeInTheDocument()

    await user.selectOptions(screen.getByLabelText('Filter by review'), 'new')
    expect(await screen.findByText('No books in the catalogue yet.')).toBeInTheDocument()
    await user.click(screen.getByLabelText('Show radio'))
    await user.click(await screen.findByRole('button', { name: 'Ignore Show S01E01' }))
    await user.selectOptions(screen.getByLabelText('Filter by review'), 'ignored')
    expect(await screen.findByText('Show S01E01')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Ignore Show S01E01' })).toBeNull()
  })

  it('shows an error when matching fails', async () => {
    server.use(http.post('/api/match', () => HttpResponse.json({ detail: 'no AudiobookDB API key' }, { status: 502 })))
    const { user } = setup(<LibraryPage />)
    await screen.findByText('1 book')
    await user.click(screen.getByRole('button', { name: 'Match new books' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('no AudiobookDB API key')
  })
})

describe('library thumbnails', () => {
  it('hides a thumbnail whose book has no cover', async () => {
    setup(<LibraryPage />)
    await screen.findByText('1 book')
    const img = document.querySelector('img[loading="lazy"]') as HTMLImageElement
    expect(img.getAttribute('src')).toBe('/api/books/1/cover')
    fireEvent.error(img)
    expect(img).toHaveStyle({ visibility: 'hidden' })
  })
})
