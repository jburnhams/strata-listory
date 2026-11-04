import { expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'
import SourceFiles from '../../src/components/SourceFiles'
import { setup, screen } from '../utils/render'
import { server } from '../utils/server'

it('shows a folder and a sample of filenames with the remaining count', async () => {
  server.use(http.get('/api/books/:id/source', () => HttpResponse.json({ folder: '/source/Book', files: ['001.mp3','002.mp3','003.mp3'], count: 115, available: true })))
  setup(<SourceFiles bookId={1} />)
  expect(screen.getByText('Loading source files…')).toBeInTheDocument()
  expect(await screen.findByText(/001.mp3 · 002.mp3 · 003.mp3 · \+112 more \(115 total\)/)).toBeInTheDocument()
  expect(screen.getByText(/\/source\/Book/)).toBeInTheDocument()
})

it.each([true,false])('distinguishes an inventoried empty folder from a missing saved file list: available %s', async available => {
  server.use(http.get('/api/books/:id/source', () => HttpResponse.json({ folder: '/source/Book', files: [], count: null, available })))
  setup(<SourceFiles bookId={1} />)
  expect(await screen.findByText(available ? /No audio files found/ : /No saved file list/)).toBeInTheDocument()
})

it('keeps the audiobook usable when source details fail', async () => {
  server.use(http.get('/api/books/:id/source', () => HttpResponse.json({ detail: 'offline' }, { status: 503 })))
  setup(<SourceFiles bookId={1} />)
  expect(await screen.findByText('Source details unavailable')).toBeInTheDocument()
})

it('shows the evidence used to group chapter files into a volume', async () => {
  server.use(http.get('/api/books/:id/source', () => HttpResponse.json({ folder: '/source/Stories', files: ['Disc1/01.mp3'], count: 30, available: true, grouping: ['Shared album and ordered tracks', 'Publisher contents confirm Disc 2'] })))
  setup(<SourceFiles bookId={1} />)
  expect(await screen.findByText('Shared album and ordered tracks')).toBeInTheDocument()
  expect(screen.getByText('Publisher contents confirm Disc 2')).toBeInTheDocument()
})
