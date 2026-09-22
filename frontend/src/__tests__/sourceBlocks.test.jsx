// Two JobSpy boards that refuse this scraper, in both search forms.
//
// The user keeps ZipRecruiter and Google Jobs selectable and in the default
// source lists. So each form must mark exactly those two boards and still
// submit a flagged board when the user selects it.
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import SearchManager from '../classic/SearchManager'
import Searches from '../screens/Searches'
import { BLOCKED_BADGE, SOURCE_BLOCKS } from '../sourceBlocks'

vi.mock('../api', () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}))
const api = (await import('../api')).default

const mockApi = (searches) => {
  api.get.mockImplementation((path) => {
    if (path === '/searches') return Promise.resolve({ data: searches })
    if (path === '/searches/countries') return Promise.resolve({ data: [{ value: 'usa', label: 'United States' }] })
    return Promise.resolve({ data: path === '/health/entities' ? {} : [] })
  })
  api.post.mockResolvedValue({ status: 200, data: {} })
  api.patch.mockResolvedValue({ data: {} })
}

// The reason sentences are visible text, so keyboard and screen-reader users get them.
const expectReasonsShown = () => {
  for (const reason of Object.values(SOURCE_BLOCKS)) {
    expect(screen.getByText(reason, { exact: false })).toBeTruthy()
  }
}

describe('Source blocks · classic SearchManager', () => {
  beforeEach(() => { vi.clearAllMocks(); sessionStorage.clear() })

  const sourceLabel = (name) => screen.getAllByText(name).map((n) => n.closest('label')).find(Boolean)

  it('flags only ZipRecruiter and Google Jobs, and a flagged board still submits', async () => {
    mockApi([])
    render(<SearchManager />)
    fireEvent.click(screen.getByText('New Search'))
    await waitFor(() => expect(sourceLabel('ZipRecruiter')).toBeTruthy())

    expect(within(sourceLabel('ZipRecruiter')).queryByText(BLOCKED_BADGE)).not.toBeNull()
    expect(within(sourceLabel('Google Jobs')).queryByText(BLOCKED_BADGE)).not.toBeNull()
    expect(within(sourceLabel('Indeed')).queryByText(BLOCKED_BADGE)).toBeNull()
    expect(within(sourceLabel('LinkedIn')).queryByText(BLOCKED_BADGE)).toBeNull()
    expectReasonsShown()

    // Off with the checkbox, back on with a click on the badge itself.
    const zip = within(sourceLabel('ZipRecruiter')).getByRole('checkbox')
    fireEvent.click(zip)
    expect(zip.checked).toBe(false)
    fireEvent.click(within(sourceLabel('ZipRecruiter')).getByText(BLOCKED_BADGE))
    expect(zip.checked).toBe(true)

    fireEvent.click(screen.getByTitle('Save'))
    await waitFor(() => expect(api.post).toHaveBeenCalled())
    expect(api.post.mock.calls[0][1].sources).toContain('zip_recruiter')
    expect(api.post.mock.calls[0][1].sources).toContain('google')
  })
})

describe('Source blocks · v2 Searches', () => {
  beforeEach(() => { vi.clearAllMocks(); sessionStorage.clear() })

  const renderScreen = () => render(<MemoryRouter><Searches /></MemoryRouter>)
  const chip = (name) => screen.getAllByRole('button').find((b) => b.textContent.includes(name))

  it('flags only ZipRecruiter and Google Jobs, and a flagged board still submits', async () => {
    mockApi([])
    renderScreen()
    fireEvent.click((await screen.findAllByText('+ New search'))[0])
    await waitFor(() => expect(chip('ZipRecruiter')).toBeTruthy())

    expect(within(chip('ZipRecruiter')).queryByText(BLOCKED_BADGE)).not.toBeNull()
    expect(within(chip('Google Jobs')).queryByText(BLOCKED_BADGE)).not.toBeNull()
    expect(within(chip('Indeed')).queryByText(BLOCKED_BADGE)).toBeNull()
    expect(within(chip('LinkedIn')).queryByText(BLOCKED_BADGE)).toBeNull()
    expectReasonsShown()

    // Off with the chip, back on with a click on the badge itself.
    fireEvent.click(chip('ZipRecruiter'))
    expect(chip('ZipRecruiter').getAttribute('aria-pressed')).toBe('false')
    fireEvent.click(within(chip('ZipRecruiter')).getByText(BLOCKED_BADGE))
    expect(chip('ZipRecruiter').getAttribute('aria-pressed')).toBe('true')

    fireEvent.change(screen.getByPlaceholderText('e.g. TPM roles — Tier 1'), { target: { value: 'TPM' } })
    fireEvent.click(screen.getByText('Create search'))
    await waitFor(() => expect(api.post).toHaveBeenCalled())
    expect(api.post.mock.calls[0][0]).toBe('/searches')
    expect(api.post.mock.calls[0][1].sources).toContain('zip_recruiter')
    expect(api.post.mock.calls[0][1].sources).toContain('google')
  })
})
