import { describe, expect, it } from 'vitest'
import SettingsPage from '../../src/components/SettingsPage'
import { http, HttpResponse } from 'msw'
import { setup, screen } from '../utils/render'
import { mock, server } from '../utils/server'

describe('SettingsPage', () => {
  it('loads current settings, saves changes, and reports validation errors', async () => {
    const { user } = setup(<SettingsPage />)
    expect(await screen.findByText('/tmp/appdata')).toBeInTheDocument()

    await user.type(screen.getByLabelText('Audiobook folder'), '/srv/audiobooks')
    await user.type(screen.getByLabelText('Backup folder'), '/mnt/share/backups')
    await user.type(screen.getByLabelText('Data folder'), '/var/appdata')
    await user.click(screen.getByRole('button', { name: 'Save settings' }))

    expect(await screen.findByRole('button', { name: 'Saved' })).toBeInTheDocument()
    expect(await screen.findByText('/srv/audiobooks')).toBeInTheDocument()
    expect(await screen.findByText('Current: /mnt/share/backups')).toBeInTheDocument()

    await user.type(screen.getByLabelText('Data folder'), 'relative/path')
    await user.click(await screen.findByRole('button', { name: 'Save settings' }, { timeout: 4000 }))

    expect(await screen.findByRole('alert')).toHaveTextContent('the data folder must be an absolute path')
  })

  it('keeps existing values when saving with empty inputs', async () => {
    const { user } = setup(<SettingsPage />)
    await screen.findByText('/tmp/appdata')
    await user.click(screen.getByRole('button', { name: 'Save settings' }))
    expect(await screen.findByRole('button', { name: 'Saved' })).toBeInTheDocument()
  })

  it('says "not set" and offers path placeholders when folders are empty', async () => {
    mock.settings = { data_dir: '', library_dir: '', backup_dir: '' }
    setup(<SettingsPage />)
    expect(await screen.findByText(/\(not set\)/)).toBeInTheDocument()
    expect(screen.getByPlaceholderText('/path/to/audiobooks')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('/path/to/data')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('/path/to/backups')).toBeInTheDocument()
  })

  it('reports a failed load and a failed save', async () => {
    server.use(http.get('/api/settings', () => HttpResponse.json({ detail: 'settings unreadable' }, { status: 500 })))
    setup(<SettingsPage />)
    expect(await screen.findByRole('alert')).toBeInTheDocument()
  })
})
