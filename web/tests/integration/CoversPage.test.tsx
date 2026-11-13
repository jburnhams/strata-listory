import { describe, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'
import CoversPage from '../../src/components/CoversPage'
import { setup, screen, waitFor, within } from '../utils/render'
import { coverState, server } from '../utils/server'

describe('CoversPage', () => {
  it('opens on the best fit already selected, with its sources, why and the count of other choices', async () => {
    setup(<CoversPage />, { route: '/covers' })
    expect(await screen.findByText('2 listens waiting for a search · 1 to choose · 1 chosen · 1 with none found')).toBeInTheDocument()
    const card = await screen.findByRole('article', { name: 'Glass Orchard' })
    expect(within(card).getByText('2 to choose from')).toBeInTheDocument()
    const best = within(card).getByRole('group', { name: 'Best fit cover for Glass Orchard' })
    expect(within(best).getAllByText(/2400×2400 · 2\sMB/)).toHaveLength(3)   // the heading and one line for each source
    const sources = within(best).getByRole('list', { name: 'Sources of this picture' })
    expect(within(sources).getByText('Apple Books')).toBeInTheDocument()
    expect(within(sources).getByText('Audible')).toBeInTheDocument()
    expect(within(best).getByText('Why this fit (score 5.4)')).toBeInTheDocument()
    expect(within(best).getByRole('button', { name: 'Accept' })).toBeInTheDocument()
    expect(within(best).getByRole('button', { name: /Expand 1\sother choice/  })).toBeInTheDocument()
    expect(within(card).queryByRole('button', { name: /^Select cover/ })).toBeNull()   // the alternatives wait behind Expand
    expect(screen.queryByRole('article', { name: 'Salt Roads' })).toBeNull()
  })

  it('accepts the best fit as it stands, and the saved cover can be cleared', async () => {
    const { user } = setup(<CoversPage />, { route: '/covers' })
    const card = await screen.findByRole('article', { name: 'Glass Orchard' })
    await user.click(within(card).getByRole('button', { name: 'Accept' }))
    await waitFor(() => expect(coverState.chosen).toEqual([{ listen: 1, image: 11, via: 'accept' }]))
    await user.click(await screen.findByRole('tab', { name: /Chosen/ }))
    const saved = await screen.findByRole('article', { name: 'Glass Orchard' })
    expect(within(saved).getByRole('group', { name: 'Saved cover for Glass Orchard' })).toBeInTheDocument()
    expect(within(saved).queryByRole('button', { name: 'Accept' })).toBeNull()
    await user.click(within(saved).getByRole('button', { name: 'Clear choice' }))
    await waitFor(() => expect(coverState.chosen.at(-1)).toEqual({ listen: 1, image: null, via: 'pick' }))
  })

  it('expands to every choice, highlights an alternative and saves it', async () => {
    const { user } = setup(<CoversPage />, { route: '/covers' })
    const card = await screen.findByRole('article', { name: 'Glass Orchard' })
    await user.click(within(card).getByRole('button', { name: /Expand 1\sother choice/  }))
    const all = within(card).getByRole('group', { name: 'Covers for Glass Orchard' })
    expect(within(all).getByRole('button', { name: /^Selected cover 2400 by 2400/, pressed: true })).toBeInTheDocument()
    expect(within(all).getByText(/Best fit/)).toBeInTheDocument()
    expect(within(all).getByText('Google Books')).toBeInTheDocument()
    await user.click(within(all).getByRole('button', { name: /^Select cover 500 by 750/ }))
    expect(within(all).getByRole('button', { name: /^Selected cover 500 by 750/, pressed: true })).toBeInTheDocument()
    await user.click(within(all).getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(coverState.chosen).toEqual([{ listen: 1, image: 12, via: 'pick' }]))
    await user.click(await screen.findByRole('tab', { name: /Chosen/ }))
    const chosen = await screen.findByRole('article', { name: 'Glass Orchard' })
    expect(within(chosen).getByRole('group', { name: 'Saved cover for Glass Orchard' })).toHaveTextContent('Saved cover · 500×750')
  })

  it('collapses again without saving, and says why a picture ranks where it does', async () => {
    const { user } = setup(<CoversPage />, { route: '/covers' })
    const card = await screen.findByRole('article', { name: 'Glass Orchard' })
    await user.click(within(card).getByText(/Why this fit \(score 5.4\)/))
    expect(within(card).getByText('audiobook cover')).toBeInTheDocument()
    expect(within(card).getByText(/learned from your choices \(was \+1.0\)/)).toBeInTheDocument()
    await user.click(within(card).getByRole('button', { name: /Expand 1\sother choice/  }))
    await user.click(within(card).getByRole('button', { name: 'Collapse' }))
    expect(within(card).getByRole('group', { name: 'Best fit cover for Glass Orchard' })).toBeInTheDocument()
    expect(coverState.chosen).toEqual([])
  })

  it('magnifies a picture in the lightbox and steps to the next choice', async () => {
    const { user } = setup(<CoversPage />, { route: '/covers' })
    const card = await screen.findByRole('article', { name: 'Glass Orchard' })
    await user.click(within(card).getByRole('button', { name: /^Magnify cover 2400 by 2400/ }))
    const box = await screen.findByRole('dialog', { name: 'Covers for Glass Orchard' })
    expect(within(box).getByText(/1 of 2/)).toBeInTheDocument()
    await user.keyboard('{ArrowRight}')
    expect(await within(box).findByText(/2 of 2/)).toBeInTheDocument()
    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
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

  it('marks a source that was rate limited, so the search can be repeated later', async () => {
    const { user } = setup(<CoversPage />, { route: '/covers' })
    const card = await screen.findByRole('article', { name: 'Glass Orchard' })
    await user.click(within(card).getByText('What each source said'))
    expect(within(card).getByText('rate limited')).toBeInTheDocument()
    expect(within(card).getByText('rate limited: 9780000000002: HTTP Error 429')).toBeInTheDocument()
  })

  it('starts the job for everything waiting and follows its progress', async () => {
    const { user } = setup(<CoversPage pollMs={30} />, { route: '/covers' })
    const button = await screen.findByRole('button', { name: 'Search for covers (2)' })
    coverState.job = { running: true, queued: 1, current: 'Glass Orchard', error: '', failed: [] }   // what the server reports once the click has started it
    await user.click(button)
    await waitFor(() => expect(coverState.runs).toEqual([{ ids: null }]))
    expect(await screen.findByText('Searching Glass Orchard · 1 more queued')).toBeInTheDocument()
    coverState.job = { running: false, queued: 0, current: '', error: 'RuntimeError: database gone', failed: [] }
    expect(await screen.findByRole('alert')).toHaveTextContent('RuntimeError: database gone')
    await waitFor(() => expect(screen.queryByText(/Searching \d/)).toBeNull())
  })

  it('names a failed search and queues it again', async () => {
    coverState.job = { running: false, queued: 0, current: '', error: '', failed: [{ job_id: 'j9', label: 'Salt Roads', error: 'database gone' }] }
    const retried: string[] = []
    server.use(http.post('/api/jobs/:id/retry', ({ params }) => { retried.push(String(params.id)); return HttpResponse.json({ queued: true }) }))
    const { user } = setup(<CoversPage />, { route: '/covers' })
    expect(await screen.findByText('Search for Salt Roads failed: database gone')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Retry Salt Roads' }))
    await waitFor(() => expect(retried).toEqual(['j9']))
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
    await user.click(within(card).getByRole('button', { name: 'Accept' }))
    expect(await within(card).findByRole('alert')).toHaveTextContent('no such picture')
  })
})
