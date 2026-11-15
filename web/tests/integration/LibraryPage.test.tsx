import { describe, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'
import LibraryPage from '../../src/components/LibraryPage'
import { setup, screen, waitFor, within } from '../utils/render'
import { configState, server } from '../utils/server'

describe('LibraryPage', () => {
  it('shows listens with confirmed matches and chosen covers, collapsed metadata summary lines, and toggles their metadata table across Original, Audible, Open Library and Output columns', async () => {
    const { user } = setup(<LibraryPage />, { route: '/library' })
    expect(await screen.findByText('2 listens with a confirmed match and cover · 2 confirmed listens still need a cover')).toBeInTheDocument()

    const dragon = screen.getByRole('article', { name: 'Red Dragon' })
    expect(within(dragon).getByRole('img', { name: 'Cover for Red Dragon' })).toHaveAttribute('src', '/api/covers/image/11?w=240')
    expect(within(dragon).getByText('2400×2400')).toBeInTheDocument()
    expect(within(dragon).getByText('Matched to “Red Dragon” by Thomas Harris · 24 files · 12h 05m')).toBeInTheDocument()
    expect(within(dragon).getByRole('link', { name: '/audio/Red Dragon' })).toHaveAttribute('href', '/files?group=2')

    const dragonToggle = within(dragon).getByRole('button', { name: 'Toggle metadata for Red Dragon', expanded: false })
    expect(dragonToggle).toHaveTextContent('Title: Red Dragon · Author: Thomas Harris · Year: 1981')
    expect(dragonToggle).toHaveTextContent('Missing (in other sources): Narrator (Steven Weber, Alan Sklar) · Subtitle (Hannibal Lecter #1)')
    expect(within(dragon).queryByRole('table', { name: 'Metadata for Red Dragon' })).toBeNull()

    await user.click(dragonToggle)
    expect(within(dragon).getByRole('button', { name: 'Toggle metadata for Red Dragon', expanded: true })).toBeInTheDocument()
    const table = within(dragon).getByRole('table', { name: 'Metadata for Red Dragon' })
    expect(within(table).getByRole('button', { name: /^Original.*3 used/i, pressed: true })).toBeInTheDocument()
    expect(within(table).getByRole('button', { name: /^Audible UK · B0A.*2 used/i, pressed: true })).toBeInTheDocument()
    expect(within(table).getByRole('button', { name: /^Audible US · B0B.*5h 00m · abridged/i, pressed: false })).toBeInTheDocument()
    expect(within(table).getByRole('button', { name: /^Open Library.*3 used/i, pressed: true })).toBeInTheDocument()

    expect(within(table).getByRole('button', { name: 'Use Original for Title: Red Dragon', pressed: true })).toBeInTheDocument()
    expect(within(table).getByRole('button', { name: 'Use Audible US · B0B for Title: The Red Dragon', pressed: false })).toBeInTheDocument()
    expect(within(table).getByLabelText('Title for Red Dragon')).toHaveValue('Red Dragon')
    expect(within(table).getByLabelText('Narrator for Red Dragon')).toHaveValue('')

    await user.click(dragonToggle)
    expect(within(dragon).queryByRole('table', { name: 'Metadata for Red Dragon' })).toBeNull()

    const ringers = screen.getByRole('article', { name: 'Dead Ringers' })
    expect(within(ringers).getByText('Not matched to a work yet · 6 files · 3h 00m')).toBeInTheDocument()
    expect(within(ringers).getByText('/audio/Dead Ringers')).toBeInTheDocument()
    const ringersToggle = within(ringers).getByRole('button', { name: 'Toggle metadata for Dead Ringers', expanded: false })
    expect(ringersToggle).toHaveTextContent('Title: Dead Ringers · Author: BBC Radio 4 · Narrator: Jon Culshaw · Year: 2011')
    expect(ringersToggle).not.toHaveTextContent('Missing (in other sources)')
    await user.click(ringersToggle)
    expect(within(ringers).getByRole('button', { name: 'Open Library' })).toBeDisabled()
  })

  it('changes a field when clicking a source cell or a column header', async () => {
    const { user } = setup(<LibraryPage />, { route: '/library' })
    const dragon = await screen.findByRole('article', { name: 'Red Dragon' })
    await user.click(within(dragon).getByRole('button', { name: 'Toggle metadata for Red Dragon' }))
    const table = within(dragon).getByRole('table', { name: 'Metadata for Red Dragon' })

    await user.click(within(table).getByRole('button', { name: 'Use Audible UK · B0A for Narrator: Steven Weber' }))
    await waitFor(() => expect(configState.picks.at(-1)).toEqual({ field: 'narrator', value: 'Steven Weber', source: 'Audible UK · B0A' }))
    expect(await within(table).findByRole('button', { name: 'Use Audible UK · B0A for Narrator: Steven Weber', pressed: true })).toBeInTheDocument()
    expect(within(table).getByLabelText('Narrator for Red Dragon')).toHaveValue('Steven Weber')

    await user.click(within(table).getByRole('button', { name: /^Audible US · B0B/i }))
    await waitFor(() => expect(configState.bulkPicks.at(-1)).toEqual({
      listen_id: 1,
      values: { title: 'The Red Dragon', author: 'T. Harris', narrator: 'Alan Sklar', year: 2000 },
      source: 'Audible US · B0B',
    }))
    expect(await within(table).findByRole('button', { name: /^Audible US · B0B.*4 used/i, pressed: true })).toBeInTheDocument()
    expect(within(table).getByLabelText('Title for Red Dragon')).toHaveValue('The Red Dragon')
    expect(within(table).getByLabelText('Narrator for Red Dragon')).toHaveValue('Alan Sklar')
  })

  it('selects from the Output dropdown, clears a field, and auto-saves write-in values on Enter, blur, and debounce', async () => {
    const { user } = setup(<LibraryPage />, { route: '/library' })
    const dragon = await screen.findByRole('article', { name: 'Red Dragon' })
    await user.click(within(dragon).getByRole('button', { name: 'Toggle metadata for Red Dragon' }))
    const table = within(dragon).getByRole('table', { name: 'Metadata for Red Dragon' })

    await user.selectOptions(within(table).getByLabelText('Title for Red Dragon'), 'The Red Dragon')
    await waitFor(() => expect(configState.picks.at(-1)).toEqual({ field: 'title', value: 'The Red Dragon', source: 'dropdown' }))

    await user.selectOptions(within(table).getByLabelText('Title for Red Dragon'), '')
    await waitFor(() => expect(configState.picks.at(-1)).toEqual({ field: 'title', value: null, source: 'dropdown' }))

    const narratorInput = within(table).getByLabelText('Write in narrator for Red Dragon')
    await user.type(narratorInput, '   {Enter}')
    expect(configState.picks).toHaveLength(2)

    await user.type(narratorInput, 'Custom Narrator{Enter}')
    await waitFor(() => expect(configState.picks.at(-1)).toEqual({ field: 'narrator', value: 'Custom Narrator', source: 'typed' }))
    expect(within(table).getByLabelText('Narrator for Red Dragon')).toHaveValue('Custom Narrator')

    // Typing the same value again does not re-send
    await user.type(narratorInput, 'Custom Narrator{Enter}')
    expect(configState.picks).toHaveLength(3)

    // Blur flushes immediately
    const subtitleInput = within(table).getByLabelText('Write in subtitle for Red Dragon')
    await user.type(subtitleInput, 'A Novel')
    await user.tab()
    await waitFor(() => expect(configState.picks.at(-1)).toEqual({ field: 'subtitle', value: 'A Novel', source: 'typed' }))

    // Debounce flushes automatically after pausing and keeps the draft intact if the user keeps typing
    const authorInput = within(table).getByLabelText('Write in author for Red Dragon')
    await user.type(authorInput, 'THOMAS')
    await waitFor(() => expect(configState.picks.at(-1)).toEqual({ field: 'author', value: 'THOMAS', source: 'typed' }))
    expect(authorInput).toHaveValue('THOMAS')
    await user.type(authorInput, ' HARRIS')
    await waitFor(() => expect(configState.picks.at(-1)).toEqual({ field: 'author', value: 'THOMAS HARRIS', source: 'typed' }))
    expect(within(table).getByLabelText('Author for Red Dragon')).toHaveValue('THOMAS HARRIS')
  })

  it('surfaces errors when saving a field or column fails', async () => {
    const { user } = setup(<LibraryPage />, { route: '/library' })
    const dragon = await screen.findByRole('article', { name: 'Red Dragon' })
    await user.click(within(dragon).getByRole('button', { name: 'Toggle metadata for Red Dragon' }))
    const table = within(dragon).getByRole('table', { name: 'Metadata for Red Dragon' })

    await user.type(within(table).getByLabelText('Write in year for Red Dragon'), 'soon{Enter}')
    expect(await within(dragon).findByRole('alert')).toHaveTextContent('the year must be four digits')

    server.use(http.post('/api/listens/:id/config', () => HttpResponse.json({ detail: 'column save failed' }, { status: 500 })))
    await user.click(within(table).getByRole('button', { name: /^Audible US · B0B/i }))
    expect(await within(dragon).findByRole('alert')).toHaveTextContent('column save failed')
  })

  it('searches, filters, sorts, and paginates the library', async () => {
    const { user } = setup(<LibraryPage />, { route: '/library?page=2' })
    await screen.findByRole('article', { name: 'Red Dragon' })

    await user.selectOptions(screen.getByLabelText('Kind'), 'radio_series')
    await waitFor(() => expect(screen.queryByRole('article', { name: 'Red Dragon' })).toBeNull())
    expect(screen.getByRole('article', { name: 'Dead Ringers' })).toBeInTheDocument()
    expect(screen.getByText(/1 matching/)).toBeInTheDocument()

    await user.selectOptions(screen.getByLabelText('Kind'), '')
    await screen.findByRole('article', { name: 'Red Dragon' })

    await user.selectOptions(screen.getByLabelText('Metadata'), 'narrator')
    await waitFor(() => expect(screen.queryByRole('article', { name: 'Dead Ringers' })).toBeNull())
    expect(screen.getByRole('article', { name: 'Red Dragon' })).toBeInTheDocument()

    await user.selectOptions(screen.getByLabelText('Metadata'), '')
    await screen.findByRole('article', { name: 'Dead Ringers' })

    await user.selectOptions(screen.getByLabelText('Sort by'), 'title-desc')
    await waitFor(() => {
      const names = screen.getAllByRole('article').map(a => a.getAttribute('aria-label'))
      expect(names).toEqual(['Red Dragon', 'Dead Ringers'])
    })
    await user.selectOptions(screen.getByLabelText('Sort by'), 'file-recent')
    await waitFor(() => {
      const names = screen.getAllByRole('article').map(a => a.getAttribute('aria-label'))
      expect(names).toEqual(['Dead Ringers', 'Red Dragon'])
    })
    await user.selectOptions(screen.getByLabelText('Sort by'), 'file-oldest')
    await waitFor(() => {
      const names = screen.getAllByRole('article').map(a => a.getAttribute('aria-label'))
      expect(names).toEqual(['Red Dragon', 'Dead Ringers'])
    })
    await user.selectOptions(screen.getByLabelText('Sort by'), 'oldest')
    await waitFor(() => {
      const names = screen.getAllByRole('article').map(a => a.getAttribute('aria-label'))
      expect(names).toEqual(['Red Dragon', 'Dead Ringers'])
    })
    await user.selectOptions(screen.getByLabelText('Sort by'), 'title')
    await waitFor(() => {
      const names = screen.getAllByRole('article').map(a => a.getAttribute('aria-label'))
      expect(names).toEqual(['Dead Ringers', 'Red Dragon'])
    })

    await user.type(screen.getByLabelText('Search'), 'zzzz')
    expect(await screen.findByText('No listens match these filters')).toBeInTheDocument()
  })

  it('paginates across pages and handles the empty library state', async () => {
    configState.withoutCover = 0
    configState.library = Array.from({ length: 12 }, (_, i) => ({
      ...configState.library[0],
      listen_id: i + 1,
      label: `Book ${String(i + 1).padStart(2, '0')}`,
      cover: null,
      work: { id: i + 1, title: `Work ${i + 1}`, authors: [], year: 2000 },
      minutes: undefined,
      files: undefined,
      path: undefined,
      group_id: undefined,
      inputs: [],
    }))
    const { user } = setup(<LibraryPage />, { route: '/library?page=5' })
    expect(await screen.findByText('Showing 11–12 of 12 · Page 2 of 2')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Previous' }))
    expect(await screen.findByText('Showing 1–10 of 12 · Page 1 of 2')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Previous' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Next' }))
    expect(await screen.findByText('Showing 11–12 of 12 · Page 2 of 2')).toBeInTheDocument()
  })

  it('shows the empty state when no listens have confirmed matches and covers yet', async () => {
    configState.library = []
    setup(<LibraryPage />, { route: '/library' })
    expect(await screen.findByText('No listens with a confirmed match and cover yet')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Matches page' })).toHaveAttribute('href', '/')
    expect(screen.getByRole('link', { name: 'Covers page' })).toHaveAttribute('href', '/covers')
  })

  it('exports MP3 chapters with optional noise reduction, shows the media player when output exists, and displays export job status', async () => {
    configState.library[1] = {
      ...configState.library[1],
      output: {
        status: 'running',
        out_dir: null,
        finished: null,
        out_bytes: null,
        out_seconds: null,
        loudness_before: null,
        loudness_after: null,
        gain_db: null,
        noise_reduction: true,
        chapters: [],
        job: { id: 9, state: 'running', noise_reduction: true, progress: { current: 'Enhancing Chapter 1' }, error: '' },
      },
    }
    const { user } = setup(<LibraryPage />, { route: '/library' })
    const dragon = await screen.findByRole('article', { name: 'Red Dragon' })
    const exportSec = within(dragon).getByRole('region', { name: 'Export for Red Dragon' })

    // Running job state on Dead Ringers
    const ringers = screen.getByRole('article', { name: 'Dead Ringers' })
    expect(within(ringers).getByText('Enhancing Chapter 1')).toBeInTheDocument()
    expect(within(ringers).getByRole('button', { name: 'Exporting…' })).toBeDisabled()

    // Export Red Dragon with noise reduction enabled
    const noiseBox = within(exportSec).getByRole('checkbox', { name: 'Noise reduction' })
    expect(noiseBox).not.toBeChecked()
    await user.click(noiseBox)
    expect(noiseBox).toBeChecked()

    await user.click(within(exportSec).getByRole('button', { name: 'Export MP3s' }))
    await waitFor(() => expect(configState.exports.at(-1)).toEqual({ listen_id: 1, noise_reduction: true }))

    expect(await within(exportSec).findByText(/Exported 2 chapters · 12h 05m · Noise reduction/)).toBeInTheDocument()
    expect(within(exportSec).getByText('/final/Thomas Harris/[1981] Red Dragon')).toBeInTheDocument()
    expect(within(exportSec).getByRole('button', { name: 'Re-export MP3s' })).toBeInTheDocument()

    const chapterSelect = within(exportSec).getByLabelText('Chapter for Red Dragon')
    expect(chapterSelect).toHaveValue('0')
    await user.selectOptions(chapterSelect, '1')
    expect(chapterSelect).toHaveValue('1')

    // Re-export without noise reduction
    await user.click(noiseBox)
    await user.click(within(exportSec).getByRole('button', { name: 'Re-export MP3s' }))
    await waitFor(() => expect(configState.exports.at(-1)).toEqual({ listen_id: 1, noise_reduction: false }))
    expect(await within(exportSec).findByText('Exported 2 chapters · 12h 05m')).toBeInTheDocument()

    // Queued and failed job states
    server.use(http.post('/api/listens/:id/export', () => HttpResponse.json({
      ...configState.library[0],
      output: {
        status: 'queued',
        out_dir: null,
        finished: null,
        out_bytes: null,
        out_seconds: null,
        loudness_before: null,
        loudness_after: null,
        gain_db: null,
        noise_reduction: false,
        chapters: [{ index: 0, file: '001.mp3', title: 'Ch 1', duration: 0 }],
        job: { id: 10, state: 'queued', noise_reduction: false, progress: null, error: '' },
      },
    })))
    configState.library[0] = {
      ...configState.library[0],
      output: {
        status: 'failed',
        out_dir: null,
        finished: null,
        out_bytes: null,
        out_seconds: null,
        loudness_before: null,
        loudness_after: null,
        gain_db: null,
        noise_reduction: false,
        chapters: [],
        job: { id: 11, state: 'failed', noise_reduction: false, progress: null, error: 'ffmpeg exited with status 1' },
      },
    }
    await user.click(within(exportSec).getByRole('button', { name: 'Re-export MP3s' }))
    expect(await within(exportSec).findByRole('alert')).toHaveTextContent('ffmpeg exited with status 1')
  })
})
