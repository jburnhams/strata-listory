import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { installFakeIntersectionObserver, installLayoutShims, type FakeIntersectionObserverControls } from 'strata-react/testing'
import { cleanup } from '@testing-library/react'
import MatchQueuePage, { certainty, evidence, runtimeDelta, subtitleOf, uniqueNames, displayTitle } from '../../src/components/MatchQueuePage'
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

describe('displayTitle', () => {
  const rec = (asin: string, title: string) => ({ asin, title, narrators: [], minutes: 303, fmt: 'unabridged', year: 2015 })
  it('prefers the matched recording\'s title over the work title', () => {
    expect(displayTitle({ title: 'La Nuit du Titanic', recording: 'B2', recordings: [rec('B1', 'A Night to Remember (1955)'), rec('B2', 'A Night to Remember')] })).toBe('A Night to Remember')
    expect(displayTitle({ title: 'David Mitchell', recording: 'B1', recordings: [rec('B1', 'David Mitchell: Back Story')] })).toBe('David Mitchell: Back Story')
  })
  it('falls back to the work title, with an Audible subtitle when no recording is chosen', () => {
    expect(displayTitle({ title: 'David Mitchell', recording: null, recordings: [rec('B1', 'David Mitchell: Back Story')] })).toBe('David Mitchell: Back Story')
    expect(displayTitle({ title: 'Dune', recording: null, recordings: [] })).toBe('Dune')
    expect(displayTitle({ title: null, recording: null, recordings: [] })).toBe('Untitled')
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

  it('reads the order and the filters from the URL and answers from the keyboard within them', async () => {
    const { user } = setup(<MatchQueuePage />, { route: '/?sort=uncertain&auto=skip' })
    expect(await screen.findByText('3 of 4 matches fit the filters, least certain first. 0 listens confirmed so far.')).toBeInTheDocument()
    expect(screen.getByText('Is this the right book? Hardest first')).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: 'Order' })).toHaveValue('uncertain')
    expect(screen.getByRole('combobox', { name: 'Accepted by the model' })).toHaveValue('skip')
    await waitFor(() => expect(cards().map(c => c.getAttribute('aria-label'))).toEqual(['Untagged folder', 'Lune', 'Salt Roads']))      // the auto-accepted Glass Orchard is left out
    expect(within(cards()[0]).getByText('Radio series')).toBeInTheDocument()
    await user.keyboard('a')                                                                     // the first card of the filtered list
    expect(screen.getByText('Approved the match.')).toBeInTheDocument()
    await waitFor(() => expect(cards().map(c => c.getAttribute('aria-label'))).toEqual(['Lune', 'Salt Roads']))
    await user.keyboard('a')                                                                     // the next answer saves the first
    await waitFor(() => expect(matchState.decided).toEqual([{ listen_id: 4, action: 'approve', part_id: 41 }]))
  })

  it('changes the order and the filters, says how many each holds, and clears them', async () => {
    const { user } = setup(<MatchQueuePage />)
    await screen.findByText(/4 matches to check, most certain first/)
    const kind = screen.getByRole('combobox', { name: 'Kind of listen' })
    await waitFor(() => expect(within(kind).getByRole('option', { name: 'Radio series (1)' })).toBeInTheDocument())
    expect(within(screen.getByRole('combobox', { name: 'Audible' })).getByRole('option', { name: 'No Audible recording (1)' })).toBeInTheDocument()
    expect(within(screen.getByRole('combobox', { name: 'Certainty' })).getByRole('option', { name: 'Close call (1)' })).toBeInTheDocument()
    await user.selectOptions(screen.getByRole('combobox', { name: 'Order' }), 'recent')
    await waitFor(() => expect(cards().map(c => c.getAttribute('aria-label'))).toEqual(['Salt Roads', 'Glass Orchard', 'Lune', 'Untagged folder']))      // newest first; one with no time last
    expect(screen.queryByRole('button', { name: 'Clear filters' })).toBeNull()                 // an order is not a filter
    await user.selectOptions(kind, 'book')
    await user.selectOptions(screen.getByRole('combobox', { name: 'Certainty' }), 'unsure')
    await waitFor(() => expect(cards().map(c => c.getAttribute('aria-label'))).toEqual(['Lune']))
    expect(await screen.findByText(/^1 of 4 matches fit the filters, most recently added first/)).toBeInTheDocument()
    await user.selectOptions(screen.getByRole('combobox', { name: 'Audible' }), 'yes')
    expect(await screen.findByText('No matches fit these filters')).toBeInTheDocument()
    expect(screen.getByText('4 matches are waiting outside them.')).toBeInTheDocument()
    await user.click(screen.getAllByRole('button', { name: 'Clear filters' })[0])
    await waitFor(() => expect(cards()).toHaveLength(4))
    expect(screen.getByRole('combobox', { name: 'Order' })).toHaveValue('recent')               // clearing the filters keeps the order
  })

  it('searches the files and the proposed works after a short pause', async () => {
    const { user } = setup(<MatchQueuePage />)
    await screen.findByRole('list', { name: 'Matches to check' })
    await user.type(screen.getByRole('searchbox', { name: 'Search matches' }), 'salt again')        // words from another candidate's title
    await waitFor(() => expect(cards().map(c => c.getAttribute('aria-label'))).toEqual(['Salt Roads']))
    await user.clear(screen.getByRole('searchbox', { name: 'Search matches' }))
    await waitFor(() => expect(cards()).toHaveLength(4))
  })

  it('keeps a choice made while a search is still pausing', async () => {
    const { user } = setup(<MatchQueuePage />)
    await screen.findByRole('list', { name: 'Matches to check' })
    await user.type(screen.getByRole('searchbox', { name: 'Search matches' }), 'mara')
    await user.selectOptions(screen.getByRole('combobox', { name: 'Accepted by the model' }), 'skip')      // before the search reaches the URL
    await waitFor(() => expect(cards().map(c => c.getAttribute('aria-label'))).toEqual(['Salt Roads', 'Lune']))
    expect(screen.getByRole('combobox', { name: 'Accepted by the model' })).toHaveValue('skip')
  })

  it('keeps a filter value from the URL choosable when no card has it now', async () => {
    setup(<MatchQueuePage />, { route: '/?kind=radio_show&status=held&sort=nonsense' })
    expect(await screen.findByText('No matches fit these filters')).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: 'Kind of listen' })).toHaveValue('radio_show')
    expect(screen.getByRole('option', { name: 'Radio show (0)' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: 'held (0)' })).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: 'Order' })).toHaveValue('certain')              // an order it does not know reads as the default
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

  it('filters by radio, plays and programmes through the one kind filter, and shows why a listen is not a book', async () => {
    setup(<MatchQueuePage />, { route: '/?kind=not-book' })
    const list = await screen.findByRole('list', { name: 'Matches to check' })
    expect(within(list).getAllByRole('article')).toHaveLength(1)
    expect(within(list).getByRole('article', { name: 'Untagged folder' })).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: 'Kind of listen' })).toHaveValue('not-book')
    expect(screen.getByRole('option', { name: 'Radio, plays and programmes (1)' })).toBeInTheDocument()
  })

  it('has nothing to show for close calls when none is', async () => {
    setup(<MatchQueuePage />, { route: '/?kind=review' })
    expect(await screen.findByRole('combobox', { name: 'Kind of listen' })).toHaveValue('review')
    await waitFor(() => expect(screen.queryAllByRole('article')).toHaveLength(0))
  })

  it('sorts by how sure the kind is, from the URL', async () => {
    setup(<MatchQueuePage />, { route: '/?sort=kind-uncertain' })
    expect(await screen.findByRole('combobox', { name: 'Order' })).toHaveValue('kind-uncertain')
    expect(screen.getByRole('option', { name: 'Kind least sure first' })).toBeInTheDocument()
    await waitFor(() => expect(within(screen.getByRole('list', { name: 'Matches to check' })).getAllByRole('article')[0]).toHaveAccessibleName('Untagged folder'))      // the one with a verdict, 97% sure, before the unclassified (read as sure books)
  })

  it('shows a proposed radio programme with its channel, first broadcast, catalogue links and evidence', async () => {
    const [first] = matchState.queue
    const radio = { ...first.candidates[0], title: 'Harbour Lights', authors: [], recordings: [], recording: null, isbns: [], ol: [], key: 'radio:harbour-lights', p: 0.9, auto: false, state: 'candidate',
      evidence: { title: 'exact_rare', sources: 'radio', channel: 'match', form: 'agree' },
      programmes: [{ source: 'bbc_radio', ref: 'b0test01', title: 'Harbour Lights', service: 'bbc_radio_four', channel: 'BBC Radio 4', first_broadcast: '2012-05-01', type: 'series', episodes: 12, genres: ['Comedy'], synopsis: 'A harbour, a lamp and a rota.' },
        { source: 'wikidata_drama', ref: 'Q99', title: 'Harbour Lights', service: '', channel: '', first_broadcast: '', type: '', episodes: null, genres: [], synopsis: '' }] }
    matchState.queue = [{ ...first, candidates: [radio] }]
    setup(<MatchQueuePage />)
    const card = await screen.findByRole('region', { name: 'The proposed programme' })
    expect(within(card).getByText('Harbour Lights')).toBeInTheDocument()
    expect(within(card).getByText(/BBC Radio 4 · series · first broadcast 2012-05-01 · 12 episodes · Comedy/)).toBeInTheDocument()
    expect(within(card).getByRole('link', { name: 'BBC Programmes' })).toHaveAttribute('href', 'https://www.bbc.co.uk/programmes/b0test01')
    expect(within(card).getByRole('link', { name: 'Wikidata' })).toHaveAttribute('href', 'https://www.wikidata.org/wiki/Q99')
    expect(within(card).getByText('A harbour, a lamp and a rota.')).toBeInTheDocument()
    for (const word of ['radio programme catalogue', 'channel matches', 'kind fits']) expect(within(card).getByText(word)).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'The proposed work' })).toBeNull()
  })

  it('still shows the book card and the unknown-channel words for ordinary candidates', () => {
    expect(evidence({ form: 'conflict', channel: 'conflict' }).map(e => e.word)).toEqual(['channel differs', 'kind does not fit'])
    expect(evidence({ form: 'neutral' })[0].word).toBe('kind is a close call')
  })
})
