import { it, expect } from 'vitest'
import { fireEvent } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import FinishedPage from '../../src/components/FinishedPage'
import Player from '../../src/components/Player'
import { setup, screen } from '../utils/render'
import { server } from '../utils/server'

it('shows finished books with a link to the processing queue', async () => {
  server.use(http.get('/api/finished', () => HttpResponse.json([{ book_id: 1, title: 'Dune', author: 'Frank Herbert', result: { status: 'done', chapters: 2 } }])))
  setup(<FinishedPage />)
  expect(await screen.findByRole('link', { name: 'Dune' })).toHaveAttribute('href', '#/book/1')
  expect(screen.getByRole('link', { name: 'Processing queue' })).toHaveAttribute('href', '#/process')
})

it('plays selected chapters and displays finished metadata', async () => {
  server.use(http.get('/api/books/:id/output', () => HttpResponse.json({ book: { title: 'Dune' }, metadata: { source: 'Audible' }, chapters: [{ title: 'Opening', duration: 60 }, { title: 'Second', duration: 120 }] })))
  const { user, container } = setup(<Player id={1} />)
  await screen.findByRole('option', { name: /Opening/ })
  expect(container.querySelector('audio')).toHaveAttribute('src', '/api/books/1/audio/0')
  await user.selectOptions(screen.getByLabelText('Chapter'), '1')
  expect(container.querySelector('audio')).toHaveAttribute('src', '/api/books/1/audio/1')
  await user.click(screen.getByText('Finished metadata and provenance'))
  expect(screen.getByText(/Audible/)).toBeInTheDocument()
})


it('continues playback on the next chapter but not after the last', async () => {
  server.use(http.get('/api/books/:id/output', () => HttpResponse.json({ book: {}, metadata: {}, chapters: [{ title: 'Opening', duration: 60 }, { title: 'Second', duration: 120 }] })))
  const { container } = setup(<Player id={1} />)
  await screen.findByRole('option', { name: /Opening/ })
  const audio = container.querySelector('audio')!
  expect(audio).not.toHaveAttribute('autoplay')
  fireEvent.ended(audio)
  expect(audio).toHaveAttribute('src', '/api/books/1/audio/1')
  expect(audio).toHaveAttribute('autoplay')
  fireEvent.ended(audio)
  expect(audio).toHaveAttribute('src', '/api/books/1/audio/1')
})
