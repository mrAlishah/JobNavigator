// The per-search country field, classic Search Manager.
//
// The backend serves the jobspy vocabulary as {value: alias, label: full name} —
// "usa"/"United States", "uk"/"United Kingdom". The form must submit the ALIAS,
// because jobspy's `Country.from_string()` is what finally reads it. A label that
// reached the payload would look correct on screen and pick the wrong Indeed site.
//
// So both assertions here are about the payload, not about the pixels: the option
// the user sees as "Canada" must post `country: "canada"`, and an existing search
// must edit from its own stored alias rather than from the default.
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import SearchManager from '../classic/SearchManager'

vi.mock('../api', () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}))
const api = (await import('../api')).default

// Three rows are enough to separate an alias from its label: two differ, one does not.
const COUNTRIES = [
  { value: 'canada', label: 'Canada' },
  { value: 'uk', label: 'United Kingdom' },
  { value: 'usa', label: 'United States' },
]

const SEARCH_UK = {
  id: 's1', name: 'London TPM', search_mode: 'keyword', search_term: 'tpm',
  location: 'London, UK', country: 'uk', active: true, sources: ['indeed'],
}

const mountWith = (searches) => {
  api.get.mockImplementation((path) => {
    if (path === '/searches/countries') return Promise.resolve({ data: COUNTRIES })
    if (path === '/searches') return Promise.resolve({ data: searches })
    return Promise.resolve({ data: {} })
  })
  api.post.mockResolvedValue({ data: {} })
  api.patch.mockResolvedValue({ data: {} })
  return render(<SearchManager />)
}

// The country field is the only select that carries the country labels.
const countrySelect = () => screen.getByRole('option', { name: 'Canada' }).closest('select')

describe('SearchManager · country', () => {
  beforeEach(() => { vi.clearAllMocks() })

  it('posts the alias of the option the user sees, not its label', async () => {
    mountWith([])
    fireEvent.click(screen.getByText('New Search'))
    await waitFor(() => expect(countrySelect()).not.toBeNull())

    // The label is display text only — the value the DOM submits is the alias.
    expect(screen.getByRole('option', { name: 'Canada' }).value).toBe('canada')

    fireEvent.change(countrySelect(), { target: { value: 'canada' } })
    fireEvent.click(screen.getByTitle('Save'))

    await waitFor(() => expect(api.post).toHaveBeenCalled())
    expect(api.post.mock.calls[0][0]).toBe('/searches')
    expect(api.post.mock.calls[0][1].country).toBe('canada')
  })

  it('edits an existing search from its stored alias and patches an alias back', async () => {
    mountWith([SEARCH_UK])
    fireEvent.click(await screen.findByTitle('Edit'))
    await waitFor(() => expect(countrySelect()).not.toBeNull())

    // The stored alias, not DEFAULT_COUNTRY.
    expect(countrySelect().value).toBe('uk')

    fireEvent.click(screen.getByTitle('Save'))
    await waitFor(() => expect(api.patch).toHaveBeenCalled())
    expect(api.patch.mock.calls[0][0]).toBe('/searches/s1')
    expect(api.patch.mock.calls[0][1].country).toBe('uk')
  })
})
