import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { act, cleanup } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import ReviewPage, { confidence, folderOf } from '../../src/components/ReviewPage'
import { setup, screen, within, waitFor } from '../utils/render'
import { server, identifyQueue, identifyState } from '../utils/server'

// jsdom has no IntersectionObserver: a stand-in the test can fire, to scroll to the end of the list.
let observers: { callback: IntersectionObserverCallback; el: Element }[] = []
class FakeObserver { constructor(public callback: IntersectionObserverCallback) {} observe(el: Element) { observers.push({ callback: this.callback, el }) } disconnect() { observers = observers.filter(o => o.callback !== this.callback) } unobserve() {} takeRecords() { return [] } root = null; rootMargin = ''; thresholds = [] }
const scrollToEnd = () => act(() => { for (const o of observers) o.callback([{ isIntersecting: true, target: o.el } as IntersectionObserverEntry], {} as IntersectionObserver) })
beforeEach(() => { observers = []; ;(globalThis as { IntersectionObserver?: unknown }).IntersectionObserver = FakeObserver })
// Leaving the page saves an answer still inside its undo window; let that request land before the next test resets the fake server.
afterEach(async () => { delete (globalThis as { IntersectionObserver?: unknown }).IntersectionObserver; cleanup(); await new Promise(r => setTimeout(r, 40)) })

const cards = () => within(screen.getByRole('list', { name: 'Matches to review' })).getAllByRole('listitem')

describe('ReviewPage', () => {
  it('lists the doubtful matches most impactful first, with the model\'s match and how sure it is', async () => {
    setup(<ReviewPage />)
    expect(await screen.findByText('3 books to check. The first one settles 40 files.')).toBeInTheDocument()
    const [first, second] = cards()
    expect(within(first).getByText('Title 1 CD01 01')).toBeInTheDocument()
    expect(within(first).getByText('Title 1')).toBeInTheDocument()
    expect(within(first).getByText('Close call · 95%')).toBeInTheDocument()
    expect(within(first).getByText('Settles 40 files')).toBeInTheDocument()
    expect(within(first).getByRole('link', { name: /Open Library/ })).toHaveAttribute('href', 'https://openlibrary.org/works/OL1W')
    expect(within(second).getByText(/no author in the tags/)).toBeInTheDocument()
    expect(within(second).getByText('Unsure · 84%')).toBeInTheDocument()
    expect(screen.getByText('520')).toBeInTheDocument()   // accepted automatically
  })

  it('accepts with the button: the card folds away at once and the answer is saved when the next one is given', async () => {
    const { user } = setup(<ReviewPage />)
    await screen.findByRole('list', { name: 'Matches to review' })
    await user.click(screen.getByRole('button', { name: 'Accept Title 1' }))
    expect(cards()).toHaveLength(2)
    expect(screen.getByText('Accepted Title 1 for 40 files.')).toBeInTheDocument()
    expect(identifyState.decided).toEqual([])                       // still inside the undo window
    await user.click(screen.getByRole('button', { name: 'Not Title 2' }))
    await waitFor(() => expect(identifyState.decided).toEqual([{ group: identifyState.queue[0].group, decision: 'approve', work: '/works/OL1W' }]))
    expect(screen.getByText('Rejected Title 2 for 39 files.')).toBeInTheDocument()
  })

  it('does not bring an answered card back after leaving and returning', async () => {
    const { user, unmount, client } = setup(<ReviewPage />)
    await screen.findByRole('list', { name: 'Matches to review' })
    await user.click(screen.getByRole('button', { name: 'Accept Title 1' }))
    unmount()
    await waitFor(() => expect(identifyState.decided).toHaveLength(1))
    setup(<ReviewPage />, { client })
    await screen.findByRole('list', { name: 'Matches to review' })
    expect(cards()).toHaveLength(2)
    expect(screen.queryByText('Title 1')).toBeNull()
  })

  it('brings the card back on undo and saves nothing', async () => {
    const { user } = setup(<ReviewPage />)
    await screen.findByRole('list', { name: 'Matches to review' })
    await user.click(screen.getByRole('button', { name: 'Not Title 1' }))
    expect(cards()).toHaveLength(2)
    await user.click(screen.getByRole('button', { name: 'Undo' }))
    expect(cards()).toHaveLength(3)
    expect(within(cards()[0]).getByText('Title 1')).toBeInTheDocument()
    expect(identifyState.decided).toEqual([])
  })

  it('works from the keyboard: A accepts, R rejects, J and K move, U undoes', async () => {
    const { user } = setup(<ReviewPage />)
    await screen.findByRole('list', { name: 'Matches to review' })
    expect(cards()[0]).toHaveAttribute('aria-current', 'true')
    await user.keyboard('j')
    expect(cards()[1]).toHaveAttribute('aria-current', 'true')
    await user.keyboard('k')
    expect(cards()[0]).toHaveAttribute('aria-current', 'true')
    await user.keyboard('a')
    expect(cards()).toHaveLength(2)
    expect(cards()[0]).toHaveAttribute('aria-current', 'true')       // focus moved on to the next card
    await user.keyboard('u')
    expect(cards()).toHaveLength(3)
    await user.keyboard('r')
    await user.keyboard('r')
    await waitFor(() => expect(identifyState.decided.map(d => d.decision)).toEqual(['reject']))
    expect(cards()).toHaveLength(1)
  })

  it('ignores the shortcuts while typing in a field', async () => {
    const { user } = setup(<><input aria-label="Notes" /><ReviewPage /></>)
    await screen.findByRole('list', { name: 'Matches to review' })
    await user.type(screen.getByLabelText('Notes'), 'arj')
    expect(cards()).toHaveLength(3)
  })

  it('moves the highlight to the card that was clicked', async () => {
    const { user } = setup(<ReviewPage />)
    await screen.findByRole('list', { name: 'Matches to review' })
    await user.click(within(cards()[2]).getByText('Title 3'))
    expect(cards()[2]).toHaveAttribute('aria-current', 'true')
  })

  it('keeps loading further pages as the end of the list scrolls into view, continuing after the last group it holds', async () => {
    identifyState.queue = identifyQueue(45)
    const seen: (string | null)[] = []
    server.use(http.get('/api/identify/queue', ({ request }) => { seen.push(new URL(request.url).searchParams.get('after')); return undefined }))
    setup(<ReviewPage />)
    await screen.findByRole('list', { name: 'Matches to review' })
    expect(cards()).toHaveLength(20)
    await scrollToEnd()
    await waitFor(() => expect(cards()).toHaveLength(40))
    await scrollToEnd()
    await waitFor(() => expect(cards()).toHaveLength(45))
    expect(seen).toEqual([null, identifyState.queue[19].group, identifyState.queue[39].group])
    expect(within(cards()[44]).getByText('Title 45')).toBeInTheDocument()
  })

  it('shows placeholders while the next page loads', async () => {
    identifyState.queue = identifyQueue(45)
    let release: () => void = () => {}
    const gate = new Promise<void>(r => { release = r })
    setup(<ReviewPage />)
    await screen.findByRole('list', { name: 'Matches to review' })
    server.use(http.get('/api/identify/queue', async () => { await gate; return undefined }))
    await scrollToEnd()
    await waitFor(() => expect(document.querySelectorAll('[aria-hidden="true"]').length).toBeGreaterThan(2))
    release()
    await waitFor(() => expect(cards()).toHaveLength(40))
  })

  it('does not shift the next page when answers were saved in between', async () => {
    identifyState.queue = identifyQueue(45)
    const { user } = setup(<ReviewPage />)
    await screen.findByRole('list', { name: 'Matches to review' })
    await user.keyboard('a'); await user.keyboard('a')              // the first answer is saved at once, the second after the undo window
    await waitFor(() => expect(identifyState.decided).toHaveLength(1))
    await scrollToEnd()
    await waitFor(() => expect(cards()).toHaveLength(38))           // 20 - 2 answered + the next 20, nothing skipped or repeated
    expect(new Set(cards().map(c => c.textContent)).size).toBe(38)
    expect(within(cards()[17]).getByText('Title 20')).toBeInTheDocument()
    expect(within(cards()[18]).getByText('Title 21')).toBeInTheDocument()
  })

  it('shows a finished moment once every match has an answer, and offers to retrain', async () => {
    identifyState.queue = identifyQueue(1)
    const { user } = setup(<ReviewPage />)
    await user.click(await screen.findByRole('button', { name: 'Accept Title 1' }))
    expect(await screen.findByRole('heading', { name: /Answered 1.doubtful match/ })).toBeInTheDocument()
    await user.click(screen.getAllByRole('button', { name: 'Retrain with my answers' })[1])
    await waitFor(() => expect(identifyState.refreshes).toEqual(['true']))
    expect(identifyState.decided).toHaveLength(1)                   // the answer still in its undo window was saved before the retrain began
  })

  it('puts a card back and says why when the answer could not be saved', async () => {
    server.use(http.post('/api/identify/decisions', () => HttpResponse.json({ detail: 'no such queued group; refresh the queue' }, { status: 404 })))
    const { user } = setup(<ReviewPage />)
    await screen.findByRole('list', { name: 'Matches to review' })
    await user.keyboard('a'); await user.keyboard('a')
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not save that answer for Title 1: no such queued group; refresh the queue')
    expect(cards()).toHaveLength(2)
    expect(within(cards()[0]).getByText('Title 1')).toBeInTheDocument()
  })

  it('offers to score the library when nothing has been scored yet', async () => {
    identifyState.queue = []
    const { user } = setup(<ReviewPage />)
    expect(await screen.findByText('Nothing has been scored yet')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Score the library' }))
    await waitFor(() => expect(identifyState.refreshes).toEqual(['false']))
  })

  it('copes with a match that has no title, authors, edition count, Open Library work or path', async () => {
    identifyState.queue = [{ ...identifyQueue(1)[0], title: undefined, authors: undefined, editions: undefined, work: null, candidates: 1, item: { title: 'Loose file', author: 'Someone', path: '' } }]
    setup(<ReviewPage />)
    const card = (await screen.findAllByRole('listitem'))[0]
    expect(within(card).getByText('No title')).toBeInTheDocument()
    expect(within(card).getByText(/by unknown author/)).toBeInTheDocument()
    expect(within(card).getByText(/1 candidate considered/)).toBeInTheDocument()
    expect(within(card).queryByRole('link')).toBeNull()
    expect(within(card).getByText('Your files')).toBeInTheDocument()
  })

  it('says so when a retrain fails', async () => {
    server.use(http.post('/api/identify/refresh', () => HttpResponse.json({ detail: 'no trained model' }, { status: 500 })))
    const { user } = setup(<ReviewPage />)
    await screen.findByRole('list', { name: 'Matches to review' })
    await user.click(screen.getByRole('button', { name: 'Retrain with my answers' }))
    expect(await screen.findByRole('button', { name: 'Retrain failed' })).toBeInTheDocument()
  })

  it('says when the queue could not be loaded and tries again on request', async () => {
    server.use(http.get('/api/identify/queue', () => HttpResponse.json({ detail: 'queue unavailable' }, { status: 503 }), { once: true }))
    const { user } = setup(<ReviewPage />)
    expect(await screen.findByRole('alert')).toHaveTextContent('queue unavailable')
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByRole('list', { name: 'Matches to review' })).toBeInTheDocument()
  })
})

describe('review helpers', () => {
  it('words the model\'s confidence, treating a near tie as a close call whatever the score', () => {
    expect(confidence(.95, .01)).toEqual({ tone: 'warning', word: 'Close call · 95%' })
    expect(confidence(.95, .4)).toEqual({ tone: 'success', word: 'Likely · 95%' })
    expect(confidence(.6, .4)).toEqual({ tone: 'info', word: 'Unsure · 60%' })
  })
  it('shows the folder a book sits in rather than the whole path', () => {
    expect(folderOf('/home/u/audio/Books/Thomas Harris/The Red Dragon/CD01 01.mp3')).toBe('Thomas Harris / The Red Dragon')
    expect(folderOf('/Books/a.mp3')).toBe('Books')
    expect(folderOf('')).toBe('')
  })
})
