// "Scrape all" on the Searches screen fires POST /scrape/run-all and stays
// disabled while the backend's scrape_all job is active.
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Searches from '../screens/Searches'

vi.mock('../api', () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}))
const api = (await import('../api')).default

describe('Searches · Scrape all', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    sessionStorage.clear()
    api.get.mockImplementation((path) => Promise.resolve({ data: path === '/searches' || path === '/monitor/active' ? [] : {} }))
    api.post.mockResolvedValue({ data: { run_id: 'r1', status: 'running' } })
  })

  it('posts to /scrape/run-all and disables itself', async () => {
    render(<MemoryRouter><Searches /></MemoryRouter>)
    fireEvent.click(await screen.findByText('Scrape all'))
    await waitFor(() => expect(api.post).toHaveBeenCalledWith('/scrape/run-all'))
    fireEvent.click(await screen.findByText('Scraping…'))
    expect(api.post).toHaveBeenCalledTimes(1)
  })
})
