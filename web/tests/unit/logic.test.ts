import { describe, expect, it, vi } from 'vitest'
import { href } from '../../src/route'
import { api, booksUrl, coverUrl, withBase } from '../../src/api'
import { formatBytes } from '../../src/components/BookPage'
import { describeCount, formatDuration, matchLabel } from '../../src/components/LibraryPage'

describe('links', () => {
  it('builds hash links to every page, with stages as tabs', () => {
    expect(href.library()).toBe('#/library')
    expect(href.review()).toBe('#/')
    expect(href.book(3)).toBe('#/book/3')
    expect(href.process()).toBe('#/processing')
    expect(href.proposed()).toBe('#/processing?tab=plans')
    expect(href.finished()).toBe('#/processing?tab=finished')
    expect(href.settings()).toBe('#/settings')
    expect(href.metadata()).toBe('#/pipeline?tab=book')
    expect(href.ingest()).toBe('#/pipeline?tab=book')
    expect(href.audiobook()).toBe('#/pipeline?tab=audiobook')
    expect(href.quality()).toBe('#/pipeline?tab=quality')
    expect(href.automatic()).toBe('#/pipeline?tab=automatic')
    expect(href.shortlist()).toBe('#/pipeline?tab=shortlist')
  })
})

describe('api helpers', () => {
  it('builds URLs with query parameters and respects withBase', () => {
    expect(withBase('/api/health')).toBe('/api/health')
    expect(withBase('relative')).toBe('relative')
    expect(booksUrl({})).toBe('/api/books')
    expect(booksUrl({ q: ' Frank & Co ', status: 'finished', review: 'new', kind: 'book' })).toBe('/api/books?q=Frank+%26+Co&status=finished&review=new&kind=book')
  })
})

describe('library formatting', () => {
  it('formats counts and durations', () => {
    expect(describeCount(0)).toBe('No books in the catalogue yet.')
    expect(describeCount(1)).toBe('1 book')
    expect(describeCount(3)).toBe('3 books')
    expect(formatDuration(null)).toBe('')
    expect(formatDuration(75600 + 5 * 60)).toBe('21h 05m')
    expect(matchLabel(null)).toBe('')
    expect(matchLabel(1)).toBe('100% match')
    expect(matchLabel(0.7)).toBe('70% match (check)')
  })
})

describe('book page helpers', () => {
  it('formats sizes and builds the cover URL', () => {
    expect(formatBytes(500)).toBe('500 B')
    expect(formatBytes(2048)).toBe('2.0 KB')
    expect(formatBytes(150 * 1024 * 1024)).toBe('150 MB')
    expect(formatBytes(5 * 1024 ** 3)).toBe('5.0 GB')
    expect(coverUrl(7)).toBe('/api/books/7/cover')
  })
})

it('awaits queued work through lightweight status requests and returns its result', async () => {
  vi.useFakeTimers()
  const fetcher=vi.fn()
    .mockResolvedValueOnce(Response.json({ job_id: 'scan-1' }, { status: 202 }))
    .mockResolvedValueOnce(Response.json({ state: 'queued' }))
    .mockResolvedValueOnce(Response.json({ state: 'running' }))
    .mockResolvedValueOnce(Response.json({ state: 'done', result: { found: 4, added: 1, existing: 3 } }))
  vi.stubGlobal('fetch',fetcher)
  try {
    const pending=api.scan()
    await vi.runAllTimersAsync()
    expect(await pending).toEqual({ found: 4, added: 1, existing: 3 })
    expect(fetcher.mock.calls.map(call=>call[0])).toEqual(['/api/scan','/api/jobs/scan-1','/api/jobs/scan-1','/api/jobs/scan-1'])
  } finally { vi.unstubAllGlobals();vi.useRealTimers() }
})

it('reports an asynchronous job failure instead of treating queue acceptance as success', async () => {
  vi.stubGlobal('fetch',vi.fn()
    .mockResolvedValueOnce(Response.json({ job_id: 'failed' }, { status: 202 }))
    .mockResolvedValueOnce(Response.json({ state: 'failed', error: 'Source files changed' })))
  try { await expect(api.scan()).rejects.toThrow('Source files changed') }
  finally { vi.unstubAllGlobals() }
})
