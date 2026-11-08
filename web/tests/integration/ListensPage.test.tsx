import { describe, expect, it } from 'vitest'
import ListensPage, { hours } from '../../src/components/ListensPage'
import { setup, screen, waitFor, within } from '../utils/render'

describe('ListensPage', () => {
  it('lists listens with a finding by kind, and links each to its files', async () => {
    setup(<ListensPage />, { route: '/listens' })
    expect(await screen.findByText('3 listens: 2 books, 1 radio series')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Red Dragon' })).toHaveAttribute('href', '/files?group=2')
    expect(screen.getByText('12h 05m')).toBeInTheDocument()
    expect(screen.getByText('Dead Ringers (season 11)')).toBeInTheDocument()      // no files group: plain text, not a link
    expect(screen.queryByRole('link', { name: 'Dead Ringers (season 11)' })).toBeNull()
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
    expect(within(await screen.findByRole('table', { name: 'Listens' })).getByText('Dead Ringers (season 11)')).toBeInTheDocument()
  })

  it('says how to start when there are none, and when nothing matches', async () => {
    setup(<ListensPage />, { route: '/listens?q=zzzz' })
    expect(await screen.findByText('No listens match')).toBeInTheDocument()
  })

  it('formats lengths', () => { expect([hours(0), hours(45), hours(725)]).toEqual(['', '45m', '12h 05m']) })
})
