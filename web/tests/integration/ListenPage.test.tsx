import { describe, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'
import { Route, Routes } from 'react-router'
import ListenPage from '../../src/components/ListenPage'
import { setup, screen, waitFor, within } from '../utils/render'
import { configState, server } from '../utils/server'

const page = () => setup(<Routes><Route path="/listens/:id" element={<ListenPage />} /></Routes>, { route: '/listens/1' })

describe('ListenPage', () => {
  it('shows the final configuration field by field with every source\'s value and where it came from', async () => {
    page()
    expect(await screen.findByRole('heading', { name: 'Red Dragon' })).toBeInTheDocument()
    expect(screen.getByText('Matched to “Red Dragon” by Thomas Harris · 1 Audible recording · 24 files, 12h 05m')).toBeInTheDocument()
    const title = screen.getByRole('region', { name: 'Title' })
    expect(within(title).getByText('from the tags or folder name')).toBeInTheDocument()
    const values = within(title).getByRole('group', { name: 'Values for Title' })
    expect(within(values).getByRole('button', { name: /^Red Dragon.*Tags \(24 of 24 tagged files\) · Open Library$/, pressed: true })).toBeInTheDocument()
    expect(within(values).getByRole('button', { name: /^The Red Dragon.*Folder$/, pressed: false })).toBeInTheDocument()
    expect(within(screen.getByRole('region', { name: 'Narrator' })).getByText('not set')).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Audible recordings' })).toHaveTextContent('B0A · UK · Steven Weber · 12h 05m · 2004')
    expect(screen.getByRole('link', { name: '/audio/Red Dragon' })).toHaveAttribute('href', '/files?group=2')
    expect(screen.getByRole('link', { name: 'Find and choose one on the Covers page' })).toHaveAttribute('href', '/covers?state=all&q=Red%20Dragon')
  })

  it('picks another source\'s value, types one, and clears a choice', async () => {
    const { user } = page()
    const title = await screen.findByRole('region', { name: 'Title' })
    await user.click(within(title).getByRole('button', { name: /^The Red Dragon/ }))
    await waitFor(() => expect(configState.picks).toEqual([{ field: 'title', value: 'The Red Dragon', source: 'Folder' }]))
    expect(await within(screen.getByRole('region', { name: 'Title' })).findByText('chosen by you')).toBeInTheDocument()
    const narrator = screen.getByRole('region', { name: 'Narrator' })
    await user.type(within(narrator).getByLabelText('Type a narrator'), 'Someone Typed{Enter}')
    await waitFor(() => expect(configState.picks.at(-1)).toEqual({ field: 'narrator', value: 'Someone Typed', source: '' }))
    await user.click(await within(screen.getByRole('region', { name: 'Narrator' })).findByRole('button', { name: 'Clear' }))
    await waitFor(() => expect(configState.picks.at(-1)).toEqual({ field: 'narrator', value: null, source: '' }))
    await user.type(within(screen.getByRole('region', { name: 'Subtitle' })).getByLabelText('Type a subtitle'), '   {Enter}')
    expect(configState.picks).toHaveLength(3)                               // a blank value is not sent
  })

  it('shows a refused value', async () => {
    server.use(http.post('/api/listens/:id/config/:field', () => HttpResponse.json({ detail: 'the year must be four digits' }, { status: 400 })))
    const { user } = page()
    const narrator = await screen.findByRole('region', { name: 'Narrator' })
    await user.type(within(narrator).getByLabelText('Type a narrator'), 'soon{Enter}')
    expect(await within(narrator).findByRole('alert')).toHaveTextContent('the year must be four digits')
  })

  it('shows the chosen cover, an unmatched listen and a missing one', async () => {
    configState.config = { ...configState.config, cover: { image_id: 11, width: 2400, height: 2400 }, work: null, recordings: [], inputs: [{ part_id: 1, group_id: null, path: '/audio/A', files: 1, minutes: 30, relation: 'copy' }, { part_id: 2, group_id: 5, path: '/audio/B', files: 2, minutes: 30, relation: 'part' }] }
    page()
    expect(await screen.findByText('Cover chosen, 2400×2400')).toBeInTheDocument()
    expect(screen.getByText('Not matched to a work yet · 0 Audible recordings · 3 files, 30m')).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Source files' })).toHaveTextContent('/audio/A · 1 file · 30m · copy')
    expect(screen.getByRole('region', { name: 'Source files' })).toHaveTextContent('/audio/B · 2 files · 30m · part')
    expect(screen.queryByRole('region', { name: 'Audible recordings' })).toBeNull()
  })

  it('says so when the listen does not exist', async () => {
    server.use(http.get('/api/listens/:id/config', () => HttpResponse.json({ detail: 'no such listen' }, { status: 404 })))
    page()
    expect(await screen.findByText(/no such listen/i)).toBeInTheDocument()
  })
})
