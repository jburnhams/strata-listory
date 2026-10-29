import { describe, expect, it } from 'vitest'
import LibraryPage from '../../src/components/LibraryPage'
import { setup, screen } from '../utils/render'
import { mock } from '../utils/server'

describe('LibraryPage row actions', () => {
  it('reports a failed update when the book vanished,', async () => {
    const { user } = setup(<LibraryPage />)
    await screen.findByText('1 book')
    mock.books = []
    await user.selectOptions(screen.getByLabelText('Status of Dune'), 'listening')
    expect(await screen.findByRole('alert')).toHaveTextContent('no such book')
  })
})
