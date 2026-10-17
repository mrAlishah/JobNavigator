// MT-20 "Copy from…": hidden with one account; with two it lists the other accounts'
// rows grouped by account, greys out names the caller already has, and one click
// clones and hands the new row to the screen.
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor, fireEvent } from '@testing-library/react'

const get = vi.fn()
const post = vi.fn()
vi.mock('../api', () => ({
  default: { get: (...a) => get(...a), post: (...a) => post(...a) },
  // accounts.js (useAccounts) binds the page's account from /auth/me (QA F7)
  bindPageAccount: () => {}, getPageAccount: () => null, markAccountChanged: () => {},
  beginOwnSwitch: () => {}, waitForWrites: () => Promise.resolve(),
}))

import CopyFrom from '../CopyFrom'

const ME = { id: 'a1', name: 'Viktor', role: 'owner', color: '#1f6feb' }
const SOURCES = { accounts: [{ id: 'b2', name: 'Partner', color: '#2e9e6b', role: 'member', items: [
  { id: 'c1', name: 'Acme', tier: 1, active: true, scrape_urls: ['https://boards.greenhouse.io/acme'], exists: false },
  { id: 'c2', name: 'Globex', tier: 2, active: true, scrape_urls: [], exists: true },
] }] }

function route(count) {
  get.mockImplementation((url) => {
    if (url === '/auth/me') return Promise.resolve({ data: { account: ME, accounts_count: count } })
    if (url === '/auth/accounts') return Promise.resolve({ data: [ME, { id: 'b2', name: 'Partner', color: '#2e9e6b', role: 'member' }] })
    if (url === '/companies/clone-sources') return Promise.resolve({ data: SOURCES })
    return Promise.reject(new Error('unexpected ' + url))
  })
}

beforeEach(() => { get.mockReset(); post.mockReset() })

describe('CopyFrom', () => {
  it('renders nothing with one account', async () => {
    route(1)
    const { container } = render(<CopyFrom kind="companies" pushToast={() => {}} onCloned={() => {}} />)
    await waitFor(() => expect(get).toHaveBeenCalledWith('/auth/me'))
    expect(container.textContent).toBe('')
  })

  it('lists other accounts grouped, disables names already owned, clones on click', async () => {
    route(2)
    post.mockResolvedValue({ data: { id: 'new1', name: 'Acme' } })
    const onCloned = vi.fn()
    render(<CopyFrom kind="companies" pushToast={() => {}} onCloned={onCloned} />)
    fireEvent.click(await screen.findByText('Copy from…'))
    expect(await screen.findByText('Partner')).toBeTruthy()
    expect(screen.getByText('You have it')).toBeTruthy()
    fireEvent.click(screen.getByText('Globex'))          // already owned: no request
    expect(post).not.toHaveBeenCalled()
    fireEvent.click(screen.getByText('Acme'))
    await waitFor(() => expect(onCloned).toHaveBeenCalledWith({ id: 'new1', name: 'Acme' }))
    expect(post).toHaveBeenCalledWith('/companies/clone', { source_id: 'c1' })
  })

  it('shows the server message and stays open when the clone is refused', async () => {
    route(2)
    post.mockRejectedValue({ response: { data: { detail: 'name_taken', message: 'You already have a company named Acme.' } } })
    const pushToast = vi.fn()
    const onCloned = vi.fn()
    render(<CopyFrom kind="companies" pushToast={pushToast} onCloned={onCloned} />)
    fireEvent.click(await screen.findByText('Copy from…'))
    fireEvent.click(await screen.findByText('Acme'))
    await waitFor(() => expect(pushToast).toHaveBeenCalledWith({ kind: 'error', msg: 'You already have a company named Acme.' }))
    expect(onCloned).not.toHaveBeenCalled()
    expect(screen.getByText('Globex')).toBeTruthy()
  })
})
