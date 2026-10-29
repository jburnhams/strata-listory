import { describe, expect, it } from 'vitest'
import { parse, href } from '../../src/route'
import { booksUrl, withBase } from '../../src/api'
import { describeCount, formatDuration, matchLabel } from '../../src/components/LibraryPage'

describe('routes', () => {
  it('parses library and settings pages', () => {
    expect(parse('')).toEqual({ page: 'library' })
    expect(parse('#/')).toEqual({ page: 'library' })
    expect(parse('#/settings')).toEqual({ page: 'settings' })
    expect(href.library()).toBe('#/')
    expect(href.settings()).toBe('#/settings')
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
