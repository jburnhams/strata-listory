import { describe, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'
import App from '../../src/App'
import { Card } from '../../src/components/ui'
import { setup, screen, within } from '../utils/render'
import { server } from '../utils/server'

const open = (hash: string) => { window.location.hash = hash; return setup(<App />) }

describe('App navigation and header', () => {
  it('shows the server version and navigates between Library and Settings', async () => {
    open('#/')
    expect(await screen.findByText('v0.1.0')).toBeInTheDocument()
    expect(await screen.findByText('Dune')).toBeInTheDocument()

    window.location.hash = '#/settings'
    window.dispatchEvent(new HashChangeEvent('hashchange'))
    expect(await screen.findByText('Application settings')).toBeInTheDocument()
  })

  it('renders cleanly when the health check fails and exercises the Card link', async () => {
    server.use(http.get('/api/health', () => new HttpResponse(null, { status: 500 })))
    open('#/')
    expect(await screen.findByText('Dune')).toBeInTheDocument()
    expect(screen.queryByText('v0.1.0')).toBeNull()

    setup(<Card to="#/settings">Go settings</Card>)
    expect(screen.getByRole('link', { name: 'Go settings' })).toHaveAttribute('href', '#/settings')
  })
})


it('orders the workflow navigation and gives final proposals their own page', async () => {
  const { user } = open('#/process')
  const nav = screen.getByRole('navigation', { name: 'Main navigation' })
  expect(within(nav).getAllByRole('link').map(a => a.textContent)).toEqual(['Library','Processing','Book identification','Automatic approvals','Match shortlist','Audiobook identification','Quality','Proposed','Finished','Settings'])
  await screen.findByRole('heading', { name: 'Processing' })
  expect(screen.queryByLabelText('Select Dune')).toBeNull()
  await user.click(within(nav).getByRole('link', { name: 'Proposed' }))
  expect(await screen.findByRole('heading', { name: 'Proposed' })).toBeInTheDocument()
  expect(await screen.findByLabelText('Select Dune')).toBeEnabled()
  expect(screen.queryByRole('checkbox', { name: 'Show completed' })).toBeNull()
  await user.click(within(nav).getByRole('link', { name: 'Processing' }))
  expect(await screen.findByRole('heading', { name: 'Processing' })).toBeInTheDocument()
  expect(screen.queryByLabelText('Select Dune')).toBeNull()
})
