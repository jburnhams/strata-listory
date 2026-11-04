import { useEffect, useRef, useState } from 'react'
import { matchReviewApi, withBase, type MatchReviewPage } from './api'

export const REVIEW_PAGE_SIZE = 10
interface Prefetch { after: string; value: MatchReviewPage | null; promise: Promise<MatchReviewPage | null> }

/** Keep the visible batch stable while decisions shrink the underlying queue. */
export function useMatchReviewQueue(mode: 'automatic' | 'shortlist', query: string) {
  const [data, setData] = useState<MatchReviewPage | null>(null)
  const [loading, setLoading] = useState(true), [error, setError] = useState<string | null>(null)
  const current = useRef<MatchReviewPage | null>(null), ahead = useRef<Prefetch | null>(null), generation = useRef(0)
  const decided = useRef(new Set<string>())
  function excludeDecided(page: MatchReviewPage): MatchReviewPage {
    const rows = page.rows.filter(row => !decided.current.has(`${row.id}:${row.basis}`))
    return { ...page, rows, total: Math.max(0, page.total - (page.rows.length - rows.length)) }
  }
  const normalise = (page: MatchReviewPage, offset: number): MatchReviewPage => excludeDecided({ ...page, offset, limit: REVIEW_PAGE_SIZE, rows: page.rows.slice(0, REVIEW_PAGE_SIZE) })
  function prefetch(page: MatchReviewPage) {
    ahead.current = null
    if (!page.rows.length || page.offset + page.rows.length >= page.total) return
    const version = generation.current, offset = page.offset + page.rows.length
    const request: Prefetch = { after: page.rows[page.rows.length - 1].id, value: null, promise: Promise.resolve(null) }
    request.promise = matchReviewApi.list(mode, offset, query, request.after).then(result => {
      const value = normalise(result, offset)
      if (generation.current === version && ahead.current === request) {
        request.value = value
        for (const row of value.rows) if (row.cover) { const cover = new Image(); cover.src = withBase(`/api/books/${row.book_id}/cover`) }
      }
      return value
    }).catch(() => null) // Failed speculation must not disturb the visible batch.
    ahead.current = request
  }
  function display(page: MatchReviewPage) { const visible = excludeDecided(page); current.current = visible; setData(visible) }
  async function load(offset: number, cached?: Prefetch | null, total?: number, scroll = false): Promise<void> {
    const version = ++generation.current
    ahead.current = null; setError(null)
    if (!cached?.value) setLoading(true)
    try {
      const prepared = cached?.value ?? await cached?.promise
      let page = prepared ? { ...prepared, offset, total: total == null ? prepared.total : Math.min(total, prepared.total) } : normalise(await matchReviewApi.list(mode, offset, query, cached?.after), offset)
      if (version !== generation.current) return
      if (offset >= page.total && offset > 0) {
        const previous = Math.max(0, Math.ceil(page.total / REVIEW_PAGE_SIZE) - 1) * REVIEW_PAGE_SIZE
        page = normalise(await matchReviewApi.list(mode, previous, query), previous)
        if (version !== generation.current) return
      }
      if (!page.rows.length && page.total > 0) {
        page = normalise(await matchReviewApi.list(mode, 0, query), 0)
        if (version !== generation.current) return
      }
      display(page); setLoading(false); prefetch(page)
      if (scroll) window.scrollTo({ top: 0, behavior: 'auto' })
    } catch (e) { if (version === generation.current) { setError((e as Error).message); setLoading(false) } }
  }
  useEffect(() => {
    current.current = null; ahead.current = null; setData(null)
    void load(0)
    return () => { generation.current++; current.current = null; ahead.current = null }
  }, [mode, query])
  async function remove(id: string, basis: string) {
    // A list/preload already in flight may still contain the just-saved revision.
    decided.current.add(`${id}:${basis}`)
    const page = current.current
    if (!page || !page.rows.some(row => row.id === id)) return
    const remaining = { ...page, rows: page.rows.filter(row => row.id !== id), total: Math.max(0, page.total - 1) }
    display(remaining)
    if (remaining.rows.length) return
    if (remaining.total > remaining.offset) {
      const cached = ahead.current
      // All ten decisions removed their rows: the prefetched next page now
      // starts at the same offset, not offset + ten (which would skip books).
      await load(remaining.offset, cached, remaining.total, true)
    } else if (remaining.offset > 0) await load(Math.max(0, remaining.offset - REVIEW_PAGE_SIZE), null, undefined, true)
    else { display(remaining); ahead.current = null }
  }
  const next = () => {
    const page = current.current
    if (page) void load(page.offset + page.rows.length, ahead.current, page.total, true)
  }
  const previous = () => { if (current.current) void load(Math.max(0, current.current.offset - REVIEW_PAGE_SIZE), null, undefined, true) }
  const reload = () => { void load(current.current?.offset ?? 0) }
  return { data, loading, error, next, previous, reload, remove }
}
