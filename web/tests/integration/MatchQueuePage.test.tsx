import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { installFakeIntersectionObserver, installLayoutShims, type FakeIntersectionObserverControls } from 'strata-react/testing'
import { cleanup } from '@testing-library/react'
import MatchQueuePage, { certainty, evidence, runtimeDelta, subtitleOf, uniqueNames } from '../../src/components/MatchQueuePage'
import { setup, screen, within, waitFor } from '../utils/render'
import { http, HttpResponse } from 'msw'
import { matchState, server } from '../utils/server'

let io: FakeIntersectionObserverControls
let uninstallLayout: () => void
beforeEach(() => { io = installFakeIntersectionObserver(); uninstallLayout = installLayoutShims({ width: 800, height: 5000 }) })
afterEach(async () => { io.uninstall(); uninstallLayout(); cleanup(); await new Promise(r => setTimeout(r, 40)) })

const cards = () => within(screen.getByRole('list', { name: 'Matches to check' })).getAllByRole('article')

describe('subtitleOf', () => {
  const rec = (asin: string, title: string) => ({ asin, title, narrators: [], minutes: 565, fmt: 'unabridged', year: 2012 })
  it('takes the part of an Audible title after the work title and a colon', () => {
    expect(subtitleOf({ title: 'David Mitchell', recording: null, recordings: [rec('B1', 'David Mitchell: Back Story')] })).toBe('Back Story')
    expect(subtitleOf({ title: 'David Mitchell', recording: 'B2', recordings: [rec('B1', 'David Mitchell: Back Story'), rec('B2', 'David Mitchell: Back Story (Unabridged)')] })).toBe('Back Story')      // the chosen recording is read first, a format label is dropped
  })
  it('is empty when no recording continues the title', () => {
    expect(subtitleOf({ title: 'Dune', recording: null, recordings: [rec('B1', 'Dune')] })).toBe('')
    expect(subtitleOf({ title: 'Dune', recording: null, recordings: [rec('B1', 'Dune Messiah: Book Two')] })).toBe('')
    expect(subtitleOf({ title: null, recording: null, recordings: [rec('B1', 'X: Y')] })).toBe('')
    expect(subtitleOf({ title: 'Dune', recording: null, recordings: [] })).toBe('')
  })
})

describe('uniqueNames', () => {
  it('collapses spellings that differ only in case, accents or spacing, preferring the one that is not shouting', () => {
    expect(uniqueNames(['Walter Lord', 'Walter LORD'])).toEqual(['Walter Lord'])
    expect(uniqueNames(['Walter LORD', 'Walter Lord'])).toEqual(['Walter Lord'])
    expect(uniqueNames(['Jose  Saramago', 'José Saramago', 'Yves Rivière'])).toEqual(['Jose  Saramago', 'Yves Rivière'])
    expect(uniqueNames([])).toEqual([])
  })
})

describe('MatchQueuePage', () => {
  it('shows the most certain match first with the files, the work, its recordings and the evidence', async () => {
    setup(<MatchQueuePage />)
    expect(await screen.findByText('4 matches to check, most certain first. 0 listens confirmed so far.')).toBeInTheDocument()
    const [first, second, third, fourth] = cards()
    expect(within(first).getByText('Sure · 99%')).toBeInTheDocument()
    expect(within(first).getByText('Accepted automatically, not yet confirmed')).toBeInTheDocument()
    expect(within(first).getByText('narrator matches')).toBeInTheDocument()
    expect(within(first).getByText(/Tom Vale · 5h 00m/)).toBeInTheDocument()
    expect(within(first).getByRole('link', { name: 'Open Library' })).toHaveAttribute('href', 'https://openlibrary.org/works/OL11W')
    expect(within(first).getByRole('link', { name: 'Audible' })).toHaveAttribute('href', 'https://www.audible.co.uk/pd/B0AAAAAAA1')
    expect(within(first).getByRole('img', { name: 'Cover of Glass Orchard' })).toBeInTheDocument()
    expect(within(second).getByText('Close call · 60%')).toBeInTheDocument()
    expect(within(second).getByText('author differs')).toBeInTheDocument()
    expect(within(second).getByText('Other candidates')).toBeInTheDocument()
    expect(within(third).queryByRole('list', { name: 'Audible recordings of this work' })).toBeNull()        // a work known only to Open Library
    expect(within(fourth).getAllByText('Untagged folder').length).toBeGreaterThan(0)
    expect(within(fourth).getByText(/no author/)).toBeInTheDocument()
    expect(within(fourth).getByText('? files · 2 copies')).toBeInTheDocument()
    expect(within(fourth).getByText(/unknown narrator/)).toBeInTheDocument()
    expect(within(fourth).getByText('Untitled')).toBeInTheDocument()
  })

  it('approves with the keyboard, approves another candidate, and says none of these', async () => {
    const { user } = setup(<MatchQueuePage />)
    await screen.findByRole('list', { name: 'Matches to check' })
    await user.keyboard('a')
    expect(screen.getByText('Approved Glass Orchard.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Approve Salt Roads Again' }))
    await waitFor(() => expect(matchState.decided).toEqual([{ listen_id: 1, action: 'approve', part_id: 11 }]))
    await user.click(within(cards()[0]).getByRole('button', { name: 'None of these' }))
    await waitFor(() => expect(matchState.decided).toContainEqual({ listen_id: 2, action: 'approve', part_id: 22 }))
    expect(screen.getByText('None of these: left for a search.')).toBeInTheDocument()
  })

  it('rejects and gives up on a card from the keyboard', async () => {
    const { user } = setup(<MatchQueuePage />)
    await screen.findByRole('list', { name: 'Matches to check' })
    await user.keyboard('r')
    expect(screen.getByText('Not Glass Orchard: the next candidate comes back after the model relearns.')).toBeInTheDocument()
    await user.keyboard('n')
    await waitFor(() => expect(matchState.decided).toEqual([{ listen_id: 1, action: 'reject', part_id: 11 }]))
  })

  it('rejects, can undo, and learns from the answers on request', async () => {
    const { user } = setup(<MatchQueuePage />)
    await screen.findByRole('list', { name: 'Matches to check' })
    await user.click(screen.getByRole('button', { name: 'Not Glass Orchard' }))
    expect(cards()).toHaveLength(3)
    await user.keyboard('u')
    await waitFor(() => expect(cards()).toHaveLength(4))
    await user.click(screen.getByRole('button', { name: 'Not Glass Orchard' }))
    await user.click(screen.getByRole('button', { name: 'Learn from my answers' }))
    await waitFor(() => expect(matchState.retrains).toBe(1))
    expect(matchState.decided).toEqual([{ listen_id: 1, action: 'reject', part_id: 11 }])      // flushed before learning
  })

  it('says how to start when nothing is identified', async () => {
    matchState.queue = []; matchState.model = null
    setup(<MatchQueuePage />)
    expect(await screen.findByText('Nothing identified yet')).toBeInTheDocument()
  })

  it('shows the finish line when everything is answered', async () => {
    matchState.queue = []
    setup(<MatchQueuePage />)
    expect(await screen.findByText('Every proposed match has an answer')).toBeInTheDocument()
  })

  it('shows a relearn in progress, then reloads the list when it is done', async () => {
    matchState.training = true
    setup(<MatchQueuePage />)
    expect(await screen.findByRole('button', { name: /Learning/ })).toBeInTheDocument()
    matchState.training = false; matchState.queue = matchState.queue.slice(1)
    await waitFor(() => expect(cards()).toHaveLength(3), { timeout: 6000 })
  }, 10000)

  it('says when relearning failed', async () => {
    server.use(http.get('/api/matches', () => HttpResponse.json({ summary: { waiting: 0, auto_unconfirmed: 0, confirmed: 2, not_found: 0, statuses: {}, decisions: 2, since_train: 2, model: 'fs-x', auto_retrain: 25, training: { running: false, finished: 1, error: 'nothing prepared', counts: null } }, items: [] })))
    setup(<MatchQueuePage />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Learning failed: nothing prepared')
    expect(screen.getByText('2 answers since the model last learned (it relearns every 25)')).toBeInTheDocument()
  })
})

describe('match helpers', () => {
  it('words the certainty, the evidence and the runtime difference', () => {
    expect(certainty(0.97)).toEqual({ tone: 'success', word: 'Sure · 97%' })
    expect(certainty(0.6, 0.5)).toEqual({ tone: 'warning', word: 'Close call · 60%' })
    expect(certainty(0.8, 0.1)).toEqual({ tone: 'info', word: 'Likely · 80%' })
    expect(certainty(0.4)).toEqual({ tone: 'warning', word: 'Unsure · 40%' })
    expect(evidence({ title: 'weak', author: null, runtime: '1pct', popularity: 'high' }).map(e => e.word)).toEqual(['title differs', 'runtime within 1.5%'])
    const rec = { asin: 'x', title: 't', narrators: [], minutes: 660, fmt: '', year: null }
    expect(runtimeDelta(rec, 600)).toBe('+10%'); expect(runtimeDelta(rec, 660)).toBe('same length'); expect(runtimeDelta({ ...rec, minutes: null }, 600)).toBe('')
  })
})
