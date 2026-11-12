import { describe, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'
import FilesPage, { length, size } from '../../src/components/FilesPage'
import { setup, screen, within } from '../utils/render'
import { mock, server } from '../utils/server'

describe('FilesPage', () => {
  it('lists the folders scanned and adds or removes an extra one', async () => {
    mock.settings = { ...mock.settings, library_dir: '/audio', library_dirs: ['/audio', '/more'] }
    const { user } = setup(<FilesPage />, { route: '/files' })
    await user.click(await screen.findByRole('button', { name: /Folders scanned \(2\)/ }))
    expect(screen.getByText('library folder')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Remove.*more/ }))
    expect(await screen.findByText(/Folders scanned \(1\)/)).toBeInTheDocument()
    await user.type(screen.getByLabelText('Add a folder'), '/third{Enter}')
    expect(await screen.findByText(/Folders scanned \(2\)/)).toBeInTheDocument()
    expect(mock.settings.library_dirs).toEqual(['/audio', '/third'])
  })

  it('opens on the newest grouping and walks down and back up the tree', async () => {
    const { user } = setup(<FilesPage />, { route: '/files' })
    expect(await screen.findByText('3 files in 1 group, 3.0 GB')).toBeInTheDocument()
    expect(within(screen.getByRole('region', { name: 'Files in this group' })).getByText('Intro.mp3')).toBeInTheDocument()
    expect(screen.getByText('1:02:05')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /metadata of Intro.mp3/ }))
    const meta = await screen.findByRole('group', { name: 'File metadata' })
    expect(meta).toHaveTextContent('unabridged'); expect(meta).toHaveTextContent('/old/Intro.mp3 (no longer there)')
    await user.click(screen.getByRole('link', { name: 'Red Dragon' }))
    expect(await screen.findByText('CD1 02.mp3')).toBeInTheDocument()
    expect(screen.getByText('unreadable')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /metadata of CD1 01.mp3/ }))
    expect(await screen.findByRole('alert')).toHaveTextContent('moov atom not found')
    await user.click(within(screen.getByRole('navigation', { name: 'Group path' })).getByRole('link', { name: 'audio' }))
    expect(await screen.findByText('Intro.mp3')).toBeInTheDocument()
  })

  it('searches files and folders and jumps to the folder of a match', async () => {
    const { user } = setup(<FilesPage />, { route: '/files' })
    await screen.findByText('Intro.mp3')
    await user.type(screen.getByLabelText('Search files and folders'), 'dragon')
    const files = await screen.findByRole('region', { name: 'Files matching the search' })
    expect(within(files).getByText('CD1 01.mp3')).toBeInTheDocument()
    expect(screen.getByText(/more files match/)).toHaveTextContent('3 more files match')
    await user.click(within(screen.getByRole('region', { name: 'Groups matching the search' })).getByRole('link', { name: 'Red Dragon' }))
    expect(await screen.findByText('CD1 02.mp3')).toBeInTheDocument()                    // jumped into the folder, search gone
    expect(screen.queryByRole('region', { name: 'Files matching the search' })).toBeNull()
  })

  it('says so when nothing matches the search', async () => {
    const { user } = setup(<FilesPage />, { route: '/files?q=zzzz' })
    expect(await screen.findByText('Nothing matches')).toBeInTheDocument()
    await user.clear(screen.getByLabelText('Search files and folders'))
    expect(await screen.findByText('Intro.mp3')).toBeInTheDocument()                     // clearing the box returns to browsing
  })

  it('shows how to start when nothing has been grouped', async () => {
    server.use(http.get('/api/filegroups', () => HttpResponse.json({ root: null, groupings: 0 })))
    setup(<FilesPage />, { route: '/files' })
    expect(await screen.findByText('No files grouped yet')).toBeInTheDocument()
  })

  it('formats sizes and lengths', () => { expect([size(2e9), size(5e6), size(1500)]).toEqual(['2.0 GB', '5 MB', '2 KB']); expect([length(null), length(95), length(3725)]).toEqual(['', '1:35', '1:02:05']) })
})
