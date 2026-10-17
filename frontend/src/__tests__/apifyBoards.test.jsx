// The Apify board list belongs to the backend (GET /searches/apify-boards), so
// a board the frontend has never heard of must still render, default and label.
// (3.0 port: the classic SearchManager half now targets screens/Searches.jsx.)
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import Searches from '../screens/Searches'
import { apifySourceLabel, boardLabel, testCost, usd } from '../apifyBoards'

vi.mock('../api', () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}))
const api = (await import('../api')).default

// Board keys carry the `apify_` prefix (never a JobSpy board name); a key is also the Job.source.
const BOARDS = [
  { value: 'apify_glassdoor', label: 'Glassdoor', hint: 'Glassdoor hint', actor: 'x/glassdoor', price_per_1k: 0.4, test_max_usd: 0.1, preview_limit: 20, max_results: 200 },
  { value: 'apify_indeed', label: 'Indeed', hint: 'Indeed hint', actor: 'misceres/indeed-scraper', price_per_1k: 6, test_max_usd: 0.18, preview_limit: 20, max_results: 200 },
]

describe('Apify board helpers', () => {
  it('names a board, and an Apify job source, from the backend list', () => {
    expect(boardLabel(BOARDS, 'apify_glassdoor')).toBe('Glassdoor')
    expect(boardLabel(BOARDS, 'monster')).toBe('monster')
    expect(apifySourceLabel(BOARDS, 'apify_indeed')).toBe('Indeed (Apify)')
    expect(apifySourceLabel(BOARDS, 'apify_indeed', ' via Apify')).toBe('Indeed via Apify')
    expect(apifySourceLabel(BOARDS, 'jobspy_indeed')).toBeNull()
    expect(apifySourceLabel(BOARDS, 'indeed')).toBeNull()   // a JobSpy board is not an Apify job
    expect(apifySourceLabel([], 'apify_dice')).toBe('dice (Apify)')
  })

  it('estimates a Test run from preview_limit × price and sums the spend caps', () => {
    const { est, max } = testCost(BOARDS, ['apify_glassdoor', 'apify_indeed', 'apify_unknown'])
    expect(usd(est)).toBe('$0.13')     // 20 × $0.40/1k + 20 × $6/1k = $0.128
    expect(usd(max)).toBe('$0.28')
  })
})

describe('Apify boards · Searches', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    sessionStorage.clear()
    api.get.mockImplementation((path) => {
      if (path === '/searches/apify-boards') return Promise.resolve({ data: BOARDS })
      if (path === '/searches/countries') return Promise.resolve({ data: [{ value: 'usa', label: 'United States' }] })
      return Promise.resolve({ data: path === '/health/entities' ? {} : [] })
    })
    api.post.mockResolvedValue({ status: 200, data: {} })
  })

  const chip = (name) => screen.getAllByRole('button').find((b) => b.textContent.endsWith(name))

  it('offers every backend board, picks the first by default and saves the choice', async () => {
    render(<MemoryRouter><Searches /></MemoryRouter>)
    fireEvent.click((await screen.findAllByText('+ New search'))[0])
    fireEvent.click(await screen.findByLabelText('Mode'))
    fireEvent.click(await screen.findByRole('option', { name: /Apify/ }))

    await waitFor(() => expect(chip('Glassdoor')).toBeTruthy())
    expect(chip('Glassdoor').getAttribute('aria-pressed')).toBe('true')
    expect(chip('Indeed').getAttribute('aria-pressed')).toBe('false')
    expect(screen.getByText('Glassdoor: Glassdoor hint')).toBeTruthy()
    expect(screen.queryByText('Indeed: Indeed hint')).toBeNull()   // hints only for ticked boards

    fireEvent.click(chip('Indeed'))
    expect(screen.getByText('Indeed: Indeed hint')).toBeTruthy()
    fireEvent.change(screen.getByPlaceholderText('e.g. TPM roles — Tier 1'), { target: { value: 'PM' } })
    fireEvent.change(screen.getByPlaceholderText('e.g. technical program manager'), { target: { value: 'pm' } })
    fireEvent.click(screen.getByText('Create search'))
    await waitFor(() => expect(api.post).toHaveBeenCalled())
    const [, payload] = api.post.mock.calls.find(([p]) => p === '/searches')
    expect(payload.search_mode).toBe('apify')
    expect(payload.sources).toEqual(['apify_glassdoor', 'apify_indeed'])
    expect('active' in payload).toBe(false)     // the backend starts a new Apify search paused
  })

  it('caps Results wanted at 200 per board in the payload', async () => {
    render(<MemoryRouter><Searches /></MemoryRouter>)
    fireEvent.click((await screen.findAllByText('+ New search'))[0])
    fireEvent.click(await screen.findByLabelText('Mode'))
    fireEvent.click(await screen.findByRole('option', { name: /Apify/ }))
    await waitFor(() => expect(chip('Glassdoor')).toBeTruthy())
    expect(screen.getByText('Starts paused: resume it on its card when the boards look right')).toBeTruthy()
    fireEvent.change(screen.getByPlaceholderText('e.g. TPM roles — Tier 1'), { target: { value: 'PM' } })
    fireEvent.change(screen.getByLabelText('Results wanted · 1–200 per board'), { target: { value: '900' } })
    fireEvent.click(screen.getByText('Create search'))
    await waitFor(() => expect(api.post).toHaveBeenCalled())
    const [, payload] = api.post.mock.calls.find(([p]) => p === '/searches')
    expect(payload.results_wanted).toBe(200)
  })

  it('asks before a paid Test and names the cost', async () => {
    const row = { id: 's1', name: 'Apify PM', search_mode: 'apify', active: false, search_term: 'pm',
      sources: ['apify_glassdoor', 'apify_indeed'], results_wanted: 50 }
    api.get.mockImplementation((path) => {
      if (path === '/searches') return Promise.resolve({ data: [row] })
      if (path === '/searches/apify-boards') return Promise.resolve({ data: BOARDS })
      if (path === '/searches/countries') return Promise.resolve({ data: [{ value: 'usa', label: 'United States' }] })
      return Promise.resolve({ data: path === '/health/entities' ? {} : [] })
    })
    render(<MemoryRouter><Searches /></MemoryRouter>)
    const test = await waitFor(() => {
      const b = screen.getAllByRole('button').find((x) => x.textContent === 'Test')
      expect(b).toBeTruthy()
      return b
    })
    fireEvent.click(test)
    await waitFor(() => expect(screen.getAllByText('Run a paid test of “Apify PM”?').length).toBeGreaterThan(0))
    expect(screen.getByText(/Estimated cost \$0\.13; each run carries a spend cap, \$0\.28 at most in all/)).toBeTruthy()
    expect(api.post.mock.calls.some(([p]) => p === '/searches/s1/test')).toBe(false)
    fireEvent.click(screen.getByText('Run paid test'))
    await waitFor(() => expect(api.post.mock.calls.some(([p]) => p === '/searches/s1/test')).toBe(true))
  })
})
