import { describe, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'
import ProcessPage from '../../src/components/ProcessPage'
import { setup, screen, within } from '../utils/render'
import { mock, server } from '../utils/server'

describe('ProcessPage', () => {
  it('shows proposals with their plans, lets you approve some, reject one and process the approved', async () => {
    const { user } = setup(<ProcessPage />)
    expect(await screen.findByText('Dune')).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'To review (2)' })).toBeInTheDocument()
    expect(screen.getAllByText(/8.2 h · 18 chapters \(from Audible chapter list\) · 17× V5 · 1× V3/)).toHaveLength(2)
    expect(screen.getAllByText(/ends 2.9 dB below the target/)).toHaveLength(1)         // only Dune has a warning
    expect(screen.getByRole('button', { name: 'Process approved (0)' })).toBeDisabled()
    await user.click(screen.getAllByText('Chapters and output')[0])
    expect(screen.getAllByText('/home/u/audiobooks/A/Dune')).toHaveLength(2)
    expect(screen.getAllByText('29.4 min · V5')[0]).toBeInTheDocument()
    expect(screen.getAllByText('21.4 min · kept as is')[0]).toBeInTheDocument()

    await user.click(screen.getByLabelText('Select Dune'))
    await user.click(screen.getByRole('button', { name: 'Approve selected (1)' }))
    expect(await screen.findByRole('tab', { name: 'To review (1)' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Process approved (1)' })).toBeEnabled()
    await user.click(screen.getByLabelText('Select Emma'))
    await user.click(screen.getByRole('button', { name: 'Reject selected' }))
    expect(await screen.findByRole('tab', { name: 'Rejected (1)' })).toBeInTheDocument()

    await user.click(screen.getByRole('tab', { name: 'Rejected (1)' }))
    await user.click(screen.getByLabelText('Select Emma'))
    await user.click(screen.getByRole('button', { name: 'Plan again' }))
    expect(await screen.findByText('Nothing here.')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Process approved (1)' }))
    expect(await screen.findByRole('tab', { name: 'Done (1)' })).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: 'Done (1)' }))
    expect(screen.getByText('Dune')).toBeInTheDocument()
  })

  it('finds more candidates, selects all, and reports errors', async () => {
    const { user } = setup(<ProcessPage />)
    await screen.findByText('Dune')
    await user.click(screen.getByRole('button', { name: 'Find candidates (5 more)' }))
    expect(await screen.findByText('Persuasion')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Select all' }))
    expect(screen.getByRole('button', { name: 'Approve selected (3)' })).toBeInTheDocument()
    server.use(http.post('/api/process/approve', () => HttpResponse.json({ detail: 'cannot approve' }, { status: 500 })))
    await user.click(screen.getByRole('button', { name: 'Approve selected (3)' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('cannot approve')
  })

  it('shows results, failures and an empty queue', async () => {
    mock.queue = [
      { book_id: 3, title: 'Finished', author: 'A', state: 'done', error: '', updated: '2026-10-30T12:00:00+00:00', summary: null },
      { book_id: 4, title: 'Broken', author: 'B', state: 'failed', error: 'its files are not available', updated: '2026-10-30T12:00:00+00:00', summary: null },
    ]
    const { user } = setup(<ProcessPage />)
    expect(await screen.findByText('Nothing to review. Use “Find candidates”.')).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: 'Done (1)' }))
    expect(screen.getByText(/Finished: 286 MB → 134 MB · -18.1 LUFS · peak -1.6 dBTP/)).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: 'Failed (1)' }))
    expect(screen.getByText('its files are not available')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Plan these again' }))
    expect(await within(screen.getByRole('tablist')).findByRole('tab', { name: 'Failed (0)' })).toBeInTheDocument()
  })
})


describe('ProcessPage progress', () => {
  it('keeps refreshing while something is running and shows the result when it finishes', async () => {
    mock.queue = [{ book_id: 5, title: 'Running book', author: 'R', state: 'running', error: '', updated: '2026-10-30T12:00:00+00:00', summary: null }]
    const { user } = setup(<ProcessPage pollMs={30} />)
    expect(await screen.findByText(/Working… run/)).toBeInTheDocument()
    await user.click(screen.getByRole('tab', { name: 'Approved (1)' }))
    expect(screen.getByText('Running book')).toBeInTheDocument()
    mock.queue = [{ ...mock.queue[0], state: 'done' }]
    expect(await screen.findByRole('tab', { name: 'Done (1)' })).toBeInTheDocument()
    expect(screen.queryByText(/Working…/)).toBeNull()
  })

  it('refuses to run when nothing is approved', async () => {
    mock.queue = [{ book_id: 6, title: 'Pending', author: 'P', state: 'approved', error: '', updated: '2026-10-30T12:00:00+00:00', summary: null }]
    const { user } = setup(<ProcessPage />)
    await screen.findByRole('tab', { name: 'Approved (1)' })
    mock.queue = []
    await user.click(screen.getByRole('button', { name: 'Process approved (1)' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('nothing is approved yet')
  })
})
