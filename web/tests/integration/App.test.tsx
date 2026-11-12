import { describe, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'
import App from '../../src/App'
import { setup, screen, within } from '../utils/render'
import { server } from '../utils/server'

const open = (route: string) => setup(<App />, { route })
const nav = () => screen.getByRole('navigation', { name: /main|primary|site/i })

describe('App shell', () => {
  it('shows the server version and Matches as home', async () => {
    open('/')
    expect(await screen.findByText('v0.1.0')).toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: 'Matches' })).toBeInTheDocument()
  })

  it('renders cleanly when the health check fails', async () => {
    server.use(http.get('/api/health', () => new HttpResponse(null, { status: 500 })))
    open('/')
    expect(await screen.findByRole('heading', { name: 'Matches' })).toBeInTheDocument()
    expect(screen.queryByText('v0.1.0')).toBeNull()
  })

  it('keeps the older Review as a Pipeline tab', async () => {
    open('/pipeline?tab=review')
    expect(await screen.findByRole('heading', { name: 'Review' })).toBeInTheDocument()
  })

  it('lists the flow from Files and moves between sections from the side navigation', async () => {
    const { user } = open('/')
    expect(within(nav()).getAllByRole('link').map(a => a.textContent)).toEqual(['Files', 'Matches', 'Listens', 'Processing', 'Settings'])
    await user.click(within(nav()).getByRole('link', { name: 'Listens' }))
    expect(await screen.findByRole('heading', { name: 'Listens' })).toBeInTheDocument()
    await user.click(within(nav()).getByRole('link', { name: 'Settings' }))
    expect(await screen.findByRole('heading', { name: 'Settings' })).toBeInTheDocument()
  })

  it('gives plans and finished books their own tabs under Processing', async () => {
    const { user } = open('/processing')
    expect(await screen.findByRole('heading', { name: 'Processing' })).toBeInTheDocument()
    expect(screen.queryByLabelText('Select Dune')).toBeNull()
    await user.click(screen.getByRole('tab', { name: 'Plans to approve' }))
    expect(await screen.findByLabelText('Select Dune')).toBeEnabled()
    await user.click(screen.getByRole('tab', { name: 'Queue' }))
    await screen.findByText('Process approved (0)')
    expect(screen.queryByLabelText('Select Dune')).toBeNull()
  })

  it('keeps the old page addresses working', async () => {
    open('/book-identification')
    expect(await screen.findByRole('heading', { name: 'Pipeline' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Book', selected: true })).toBeInTheDocument()
  })

  it('moves through the pipeline stages as tabs', async () => {
    const { user } = open('/pipeline')
    await screen.findByRole('heading', { name: 'Pipeline' })
    for (const name of ['Audiobook', 'Quality', 'Automatic approvals', 'Match shortlist']) {
      await user.click(screen.getByRole('tab', { name }))
      expect(screen.getByRole('tab', { name, selected: true })).toBeInTheDocument()
    }
  })

  it('sends unknown paths to Matches', async () => {
    open('/nowhere')
    expect(await screen.findByRole('heading', { name: 'Matches' })).toBeInTheDocument()
  })
})
