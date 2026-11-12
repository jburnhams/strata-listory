import { describe, expect, it } from 'vitest'
import ListensPage, { hours } from '../../src/components/ListensPage'
import { http, HttpResponse } from 'msw'
import { setup, screen, waitFor, within } from '../utils/render'
import { server } from '../utils/server'

describe('ListensPage', () => {
  it('lists listens with a finding by kind, and links each to its files', async () => {
    setup(<ListensPage />, { route: '/listens' })
    expect(await screen.findByText('3 listens: 2 books, 1 radio series')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Red Dragon' })).toHaveAttribute('href', '/files?group=2')
    expect(screen.getByText('12h 05m')).toBeInTheDocument()
    expect(screen.getByText('Thomas Harris · read by Steven Weber · 1981')).toBeInTheDocument()
    expect(screen.getByText('3 copies')).toBeInTheDocument()                       // linked copies of one recording
    expect(screen.queryByText('1 copies')).toBeNull()
    expect(screen.getByText('2 parts')).toBeInTheDocument()                          // numbered parts of one recording, not copies
    expect(screen.getByText('Dead Ringers (season 11)')).toBeInTheDocument()      // no files group: plain text, not a link
    expect(screen.queryByRole('link', { name: 'Dead Ringers (season 11)' })).toBeNull()
  })

  it('shows confirmed and accepted matches by default and asks for the other match states on request', async () => {
    const asked: string[] = []
    server.use(http.get('/api/listens', ({ request }) => { asked.push(new URL(request.url).searchParams.get('match') ?? 'none'); return HttpResponse.json({ total: 0, kinds: {}, review: 0, not_book: 0, rows: [] }) }))
    const { user } = setup(<ListensPage />, { route: '/listens' })
    await waitFor(() => expect(asked).toEqual(['matched']))
    expect(screen.getByRole('tab', { name: 'Confirmed and accepted', selected: true })).toBeInTheDocument()
    for (const [tab, match] of [['Confirmed by you', 'confirmed'], ['Accepted by the model', 'accepted'], ['Not matched yet', 'unmatched'], ['Any match state', '']]) {
      await user.click(screen.getByRole('tab', { name: tab }))
      await waitFor(() => expect(asked.at(-1)).toBe(match))
    }
  })

  it('searches and filters by kind', async () => {
    const { user } = setup(<ListensPage />, { route: '/listens' })
    await screen.findByText('Red Dragon')
    await user.type(screen.getByLabelText('Search listens'), 'lee child')
    await waitFor(() => expect(screen.queryByText('Red Dragon')).toBeNull())          // the box settles for a moment, then the list follows
    expect(await screen.findByText('Persuader')).toBeInTheDocument()
    await user.clear(screen.getByLabelText('Search listens'))
    await user.click(screen.getByRole('tab', { name: 'Radio series (1)' }))
    await waitFor(() => expect(screen.queryByText('Red Dragon')).toBeNull())
    expect(within(await screen.findByRole('grid', { name: 'Listens' })).getByText('Dead Ringers (season 11)')).toBeInTheDocument()
  })

  it('says how to start when there are none, and when nothing matches', async () => {
    setup(<ListensPage />, { route: '/listens?q=zzzz' })
    expect(await screen.findByText('No listens match')).toBeInTheDocument()
  })

  it('formats lengths', () => { expect([hours(0), hours(45), hours(725)]).toEqual(['', '45m', '12h 05m']) })
})
