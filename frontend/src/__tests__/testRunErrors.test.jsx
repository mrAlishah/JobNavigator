// A board that fails during a Test run must show in the result with its text.
// Before this, the keyword preview dropped a failed board from the result silently.
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import SearchManager from '../classic/SearchManager'
import Searches from '../screens/Searches'

vi.mock('../api', () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}))
const api = (await import('../api')).default

const ZIP_TEXT = 'the server refuses the requests that this scraper sends: HTTP 403'
const SEARCH = {
  id: 's1', name: 'TPM', search_mode: 'keyword', search_term: 'tpm', location: '',
  country: 'usa', active: true, sources: ['indeed', 'zip_recruiter'],
}
// The keyword Test run result when ZipRecruiter failed and Indeed returned a row.
const TEST_RESULT = {
  search_name: 'TPM', duration: 1, raw_count: 1, after_filter: 1,
  source_breakdown: { indeed: 1 }, source_errors: { zip_recruiter: ZIP_TEXT },
  company_breakdown: {}, jobs: [], config: { sources: ['indeed', 'zip_recruiter'] },
}

const mockApi = (searches) => {
  api.get.mockImplementation((path) => {
    if (path === '/searches') return Promise.resolve({ data: searches })
    if (path === '/searches/countries') return Promise.resolve({ data: [{ value: 'usa', label: 'United States' }] })
    return Promise.resolve({ data: path === '/health/entities' ? {} : [] })
  })
  api.post.mockImplementation((path) => Promise.resolve(
    path.endsWith('/test') ? { status: 200, data: TEST_RESULT } : { status: 200, data: {} }))
  api.patch.mockResolvedValue({ data: {} })
}

describe('Test run · classic SearchManager', () => {
  beforeEach(() => { vi.clearAllMocks(); sessionStorage.clear() })

  it('shows a failed board and its text in the Test run result', async () => {
    mockApi([SEARCH])
    render(<SearchManager />)
    fireEvent.click(await screen.findByTitle('Test Search (dry run)'))
    expect(await screen.findByText(`zip_recruiter failed: ${ZIP_TEXT}`)).toBeTruthy()
    expect(screen.getByText('indeed (1)')).toBeTruthy()
  })
})

describe('Test run · v2 Searches', () => {
  beforeEach(() => { vi.clearAllMocks(); sessionStorage.clear() })

  const renderScreen = () => render(<MemoryRouter><Searches /></MemoryRouter>)

  it('shows a failed board and its text in the Test run result', async () => {
    mockApi([SEARCH])
    renderScreen()
    fireEvent.click(await screen.findByTitle(/^Preview run/))
    expect(await screen.findByText(`failed: ${ZIP_TEXT}`, { exact: false })).toBeTruthy()
    expect(screen.getByText('indeed 1')).toBeTruthy()
  })
})
