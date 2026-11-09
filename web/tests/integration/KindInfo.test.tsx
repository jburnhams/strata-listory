import { describe, expect, it } from 'vitest'
import KindInfo, { kindCertainty } from '../../src/components/KindInfo'
import ListensPage from '../../src/components/ListensPage'
import { kindChoices, kindInfos } from '../utils/server'
import { setup, screen, waitFor, within } from '../utils/render'

describe('kindCertainty', () => {
  it('says nothing without a verdict', () => expect(kindCertainty(null, 'book')).toBeNull())
  it('puts a close call forward as a call to check', () => expect(kindCertainty(kindInfos.close, 'book')).toEqual({ tone: 'warning', word: 'Check the kind · 50% broadcast' }))
  it('gives a clear broadcast as its probability', () => expect(kindCertainty(kindInfos.series, 'radio_series')).toEqual({ tone: 'info', word: '97% sure' }))
  it('credits a person with their own choice', () => expect(kindCertainty({ ...kindInfos.series, decided_by: 'user' }, 'book')).toEqual({ tone: 'success', word: 'Your choice' }))
  it('gives a clear book as the chance it is a book', () => expect(kindCertainty({ ...kindInfos.close, band: 'book', p_broadcast: 0.1 }, 'book')).toEqual({ tone: 'neutral', word: '90% sure' }))
})

describe('KindInfo', () => {
  it('shows the kind, why the classifier chose it, and what else it could be', async () => {
    const { user } = setup(<KindInfo listenId={9} kind="book" info={kindInfos.close} />)
    expect(screen.getByText('Book')).toBeInTheDocument()
    expect(screen.getByText('Could be: Play')).toBeInTheDocument()
    expect(screen.queryByRole('list', { name: 'Reasons for the kind' })).toBeNull()
    await user.click(screen.getByRole('button', { name: 'Why?' }))
    const reasons = within(screen.getByRole('list', { name: 'Reasons for the kind' }))
    expect(reasons.getByText('play length')).toBeInTheDocument()
    expect(reasons.getByText('+0.8')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Hide reasons' }))
    expect(screen.queryByRole('list', { name: 'Reasons for the kind' })).toBeNull()
  })

  it('saves a person\'s choice of kind', async () => {
    kindChoices.length = 0
    const { user } = setup(<KindInfo listenId={9} kind="book" info={kindInfos.close} />)
    await user.click(screen.getByRole('button', { name: 'Why?' }))
    expect(screen.queryByRole('button', { name: 'It is a book' })).toBeNull()      // it already is one
    await user.click(screen.getByRole('button', { name: 'It is a play' }))
    await waitFor(() => expect(kindChoices).toEqual([{ id: 9, kind: 'play' }]))
  })

  it('shows which weights were learned from your answers and which are still the prior', async () => {
    const { user } = setup(<KindInfo listenId={9} kind="radio_series" info={kindInfos.series} />)
    await user.click(screen.getByRole('button', { name: 'Why?' }))
    expect(screen.getByText('(learned, was +3)')).toBeInTheDocument()
    expect(screen.getByText('(prior)')).toBeInTheDocument()
  })

  it('counts "Kind is right" as an answer, and not again once it is your choice', async () => {
    kindChoices.length = 0
    const { user } = setup(<KindInfo listenId={9} kind="radio_series" info={kindInfos.series} />)
    await user.click(screen.getByRole('button', { name: 'Why?' }))
    await user.click(screen.getByRole('button', { name: 'Kind is right' }))
    await waitFor(() => expect(kindChoices).toEqual([{ id: 9, kind: 'radio_series' }]))
  })

  it('offers no confirmation for a kind you already chose', async () => {
    const { user } = setup(<KindInfo listenId={9} kind="book" info={{ ...kindInfos.close, decided_by: 'user' }} />)
    await user.click(screen.getByRole('button', { name: 'Why?' }))
    expect(screen.queryByRole('button', { name: 'Kind is right' })).toBeNull()
  })

  it('still lets you correct a listen the classifier saw no signals in', async () => {
    kindChoices.length = 0
    const { user } = setup(<KindInfo listenId={9} kind="book" info={null} />)
    await user.click(screen.getByRole('button', { name: 'Why?' }))
    expect(screen.getByText(/No signal pointed either way/)).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'It is a radio series' }))
    await waitFor(() => expect(kindChoices).toEqual([{ id: 9, kind: 'radio_series' }]))
  })
})

describe('ListensPage kind tabs', () => {
  it('lists only the close calls, then only the radio, plays and programmes', async () => {
    const { user } = setup(<ListensPage />, { route: '/listens' })
    await screen.findByText('Red Dragon')
    await user.click(screen.getByRole('tab', { name: 'Kind is a close call (1)' }))
    await waitFor(() => expect(screen.queryByText('Red Dragon')).toBeNull())
    expect(screen.getByText('Persuader')).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: 'Radio, plays and programmes (1)' }))
    expect(await screen.findByText('Dead Ringers (season 11)')).toBeInTheDocument()
    expect(screen.queryByText('Persuader')).toBeNull()
  })
})
