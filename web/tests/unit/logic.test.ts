import { sourceFolder, inFolder } from '../../src/components/FolderTree'
import { describe, expect, it } from 'vitest'
import { parse, href } from '../../src/route'
import { booksUrl, coverUrl, withBase } from '../../src/api'
import { formatBytes } from '../../src/components/BookPage'
import { describeCount, formatDuration, matchLabel } from '../../src/components/LibraryPage'

describe('routes', () => {
  it('parses library and settings pages', () => {
    expect(parse('')).toEqual({ page: 'library' })
    expect(parse('#/')).toEqual({ page: 'library' })
    expect(parse('#/settings')).toEqual({ page: 'settings' })
    expect(parse('#/book/12')).toEqual({ page: 'book', id: 12 })
    expect(parse('#/book/x')).toEqual({ page: 'library' })
    expect(parse('#/book')).toEqual({ page: 'library' })
    expect(parse('#/process')).toEqual({ page: 'process' })
    expect(href.process()).toBe('#/process')
    expect(parse('#/proposed')).toEqual({ page: 'proposed' })
    expect(href.proposed()).toBe('#/proposed')
    expect(href.library()).toBe('#/')
    expect(parse('#/nonsense/1')).toEqual({ page: 'library' })
    expect(href.book(3)).toBe('#/book/3')
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

describe('book page helpers', () => {
  it('formats sizes and builds the cover URL', () => {
    expect(formatBytes(500)).toBe('500 B')
    expect(formatBytes(2048)).toBe('2.0 KB')
    expect(formatBytes(150 * 1024 * 1024)).toBe('150 MB')
    expect(formatBytes(5 * 1024 ** 3)).toBe('5.0 GB')
    expect(coverUrl(7)).toBe('/api/books/7/cover')
  })
})

it('routes separate identification pages and preserves old metadata links', () => {
  expect(parse('#/book-identification')).toEqual({ page: 'metadata' })
  expect(parse('#/audiobook-identification')).toEqual({ page: 'audiobook' })
  expect(parse('#/metadata')).toEqual({ page: 'metadata' })
  expect(href.metadata()).toBe('#/book-identification')
  expect(href.audiobook()).toBe('#/audiobook-identification')
})


describe('source folder paths', () => {
  it('normalises file paths and respects directory boundaries across platforms', () => {
    expect(sourceFolder('/audio/book.M4B')).toBe('/audio')
    expect(sourceFolder('/audio/Books/')).toBe('/audio/Books')
    expect(sourceFolder('/book.mp3')).toBe('/')
    expect(sourceFolder('C:\\Books\\Novel.mp3')).toBe('C:/Books')
    expect(inFolder('c:/BOOKS/Novel.mp3', 'C:/Books')).toBe(true)
    expect(inFolder('//server/share/books/title.flac', '//server/share')).toBe(true)
    expect(inFolder('/audio/Bookshelf/book.mp3', '/audio/Books')).toBe(false)
    expect(inFolder('/audio/book.mp3', '/')).toBe(true)
    expect(inFolder('', null)).toBe(true)
    expect(inFolder('', '')).toBe(true)
    expect(inFolder('/audio', '')).toBe(false)
  })
})
