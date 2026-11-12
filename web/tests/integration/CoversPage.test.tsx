import { describe, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'
import CoversPage from '../../src/components/CoversPage'
import { setup, screen, waitFor, within } from '../utils/render'
import { coverState, server } from '../utils/server'

describe('CoversPage', () => {
  it('opens on the listens with pictures to choose from, with the finding and each picture\'s size and sources', async () => {
    setup(<CoversPage />, { route: '/covers' })
    expect(await screen.findByText('2 listens waiting for a search · 1 to choose · 1 chosen · 1 with none found')).toBeInTheDocument()
    const card = await screen.findByRole('article', { name: 'Glass Orchard' })
    expect(within(card).getByText('2 to choose from')).toBeInTheDocument()
    expect(within(card).getByRole('button', { name: 'Choose cover 2400 by 2400, from Apple Books, Audible' })).toBeInTheDocument()
    expect(within(card).getByText('2400×2400')).toBeInTheDocument()
    expect(within(card).getByText(/2 MB · Apple Books, Audible/)).toBeInTheDocument()
    expect(screen.queryByRole('article', { name: 'Salt Roads' })).toBeNull()
  })

  it('saves the preferred picture and lets it be cleared', async () => {
    const { user } = setup(<CoversPage />, { route: '/covers' })
    const card = await screen.findByRole('article', { name: 'Glass Orchard' })
    await user.click(within(card).getByRole('button', { name: /Choose cover 500 by 750/ }))
    await waitFor(() => expect(coverState.chosen).toEqual([{ listen: 1, image: 12 }]))
    await user.click(await screen.findByRole('tab', { name: /Chosen/ }))
    const chosen = await screen.findByRole('article', { name: 'Glass Orchard' })
    expect(within(chosen).getByRole('button', { name: /^Chosen cover 500 by 750/, pressed: true })).toBeInTheDocument()
    await user.click(within(chosen).getByRole('button', { name: 'Clear choice' }))
    await waitFor(() => expect(coverState.chosen.at(-1)).toEqual({ listen: 1, image: null }))
  })

  it('says why nothing was found, for each source, and searches again for that listen', async () => {
    const { user } = setup(<CoversPage />, { route: '/covers?state=none' })
    const card = await screen.findByRole('article', { name: 'Salt Roads' })
    expect(within(card).getByText('No source had a picture for this book. The reasons are below.')).toBeInTheDocument()
    await user.click(within(card).getByText('What each source said'))
    expect(within(card).getByText('no ASIN known for this book')).toBeInTheDocument()
    expect(within(card).getByText('8 results, none with a matching title')).toBeInTheDocument()
    await user.click(within(card).getByRole('button', { name: 'Search again' }))
    await waitFor(() => expect(coverState.runs).toEqual([{ ids: [2] }]))
  })

  it('starts the job for everything waiting and follows its progress', async () => {
    const { user } = setup(<CoversPage pollMs={30} />, { route: '/covers' })
    const button = await screen.findByRole('button', { name: 'Search for covers (2)' })
    coverState.job = { running: true, done: 1, total: 2, current: 'Glass Orchard', finished: null, error: '' }   // what the server reports once the click has started it
    await user.click(button)
    await waitFor(() => expect(coverState.runs).toEqual([{ ids: null }]))
    expect(await screen.findByText('Searching 1 of 2 · Glass Orchard')).toBeInTheDocument()
    coverState.job = { running: false, done: 2, total: 2, current: '', finished: 1, error: 'RuntimeError: database gone' }
    expect(await screen.findByRole('alert')).toHaveTextContent('RuntimeError: database gone')
    await waitFor(() => expect(screen.queryByText(/Searching \d/)).toBeNull())
  })

  it('searches by title, shows all searched listens, and says when nothing is there', async () => {
    const { user } = setup(<CoversPage />, { route: '/covers' })
    await screen.findByRole('article', { name: 'Glass Orchard' })
    await user.click(screen.getByRole('tab', { name: 'All searched' }))
    expect(await screen.findByRole('article', { name: 'Third Book' })).toBeInTheDocument()
    expect(screen.getByRole('article', { name: 'Salt Roads' })).toBeInTheDocument()
    await user.type(screen.getByLabelText('Search covers'), 'zzz')
    expect(await screen.findByText('Nothing here yet')).toBeInTheDocument()
    expect(screen.getByText('Press Search for covers to look for the pictures.')).toBeInTheDocument()
    coverState.waiting = 0
    await user.clear(screen.getByLabelText('Search covers')); await user.type(screen.getByLabelText('Search covers'), 'zzz2')
    expect(await screen.findByText('No search has run on a listen like this.')).toBeInTheDocument()
  })

  it('shows a refused choice or search', async () => {
    server.use(http.post('/api/covers/:id/choose', () => HttpResponse.json({ detail: 'no such picture' }, { status: 404 })))
    const { user } = setup(<CoversPage />, { route: '/covers' })
    const card = await screen.findByRole('article', { name: 'Glass Orchard' })
    await user.click(within(card).getByRole('button', { name: /Choose cover 2400/ }))
    expect(await within(card).findByRole('alert')).toHaveTextContent('no such picture')
  })
})
