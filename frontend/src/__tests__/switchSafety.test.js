// QA F7 / R6-01: a switch never carries this account's pending writes into the
// next one, and a tab that another tab switched away goes read-only.
//   * switchAccount runs every registered flush and waits for in-flight writes
//     BEFORE it POSTs /auth/switch
//   * writes carry X-Expected-Account (the account the page was loaded for)
//   * a broadcast / storage message, a 409 account_changed, or an answer naming
//     another account raises jn:account-changed and blocks every later write
import { describe, it, expect, beforeEach, vi } from 'vitest'
import api, {
  _resetPageAccount, accountChanged, bindPageAccount, getPageAccount, waitForWrites, writeHeaders,
} from '../api'
import {
  SWITCH_STORAGE_KEY, finishSwitch, flushPendingWrites, listenForSwitches, onSwitchMessage,
  registerFlush, switchAccount,
} from '../accounts'

const A = '11111111-1111-1111-1111-111111111111'
const B = '22222222-2222-2222-2222-222222222222'

// Drive the real interceptors with a fake adapter: every request is recorded and
// answered by `answer(config)` (a promise of a response or a rejection).
let sent
let answer
beforeEach(() => {
  _resetPageAccount()
  sent = []
  answer = (config) => Promise.resolve({ data: {}, status: 200, statusText: 'OK', headers: { 'x-jn-account': A }, config })
  api.defaults.adapter = (config) => { sent.push(config); return answer(config) }
})

const changedEvents = () => {
  const seen = []
  const on = (e) => seen.push(e.detail)
  window.addEventListener('jn:account-changed', on)
  return { seen, off: () => window.removeEventListener('jn:account-changed', on) }
}

describe('expected account on writes', () => {
  it('the first answer binds the page; writes then carry it, reads do not', async () => {
    await api.get('/jobs')
    expect(getPageAccount()).toBe(A)
    await api.patch('/persona', { contact: {} })
    await api.get('/jobs')
    expect(sent[1].headers['X-Expected-Account']).toBe(A)
    expect(sent[2].headers['X-Expected-Account']).toBeUndefined()
    expect(writeHeaders()['X-Expected-Account']).toBe(A)
  })

  it('a 409 account_changed stales the page and blocks the next write before it is sent', async () => {
    bindPageAccount(A)
    const ev = changedEvents()
    answer = (config) => Promise.reject(Object.assign(new Error('409'), {
      config, response: { status: 409, data: { detail: 'account_changed', account: { id: B, name: 'Bea' } }, headers: {} },
    }))
    await expect(api.post('/companies', { name: 'X' })).rejects.toBeTruthy()
    expect(ev.seen).toEqual([{ id: B, name: 'Bea' }])
    expect(accountChanged()).toEqual({ id: B, name: 'Bea' })
    const before = sent.length
    await expect(api.patch('/persona', {})).rejects.toMatchObject({ code: 'ERR_ACCOUNT_CHANGED' })
    expect(sent.length).toBe(before)          // never reached the wire
    await api.get('/jobs').catch(() => {})    // reads still go out
    expect(sent.length).toBe(before + 1)
    ev.off()
  })

  it('an answer naming another account stales the page (once)', async () => {
    const ev = changedEvents()
    await api.get('/jobs')
    answer = (config) => Promise.resolve({ data: {}, status: 200, headers: { 'x-jn-account': B }, config })
    await api.get('/jobs')
    await api.get('/jobs')
    expect(ev.seen.length).toBe(1)
    expect(ev.seen[0].id).toBe(B)
    ev.off()
  })

  it('the switch answer itself never stales the page', async () => {
    bindPageAccount(A)
    const ev = changedEvents()
    answer = (config) => Promise.resolve({ data: { account: { id: B } }, status: 200, headers: { 'x-jn-account': B }, config })
    await api.post('/auth/switch', { account_id: B })
    expect(ev.seen).toEqual([])
    expect(accountChanged()).toBe(null)
    ev.off()
  })
})

describe('switchAccount flushes first', () => {
  it('runs every registered flush and waits for its write before POST /auth/switch', async () => {
    bindPageAccount(A)
    const order = []
    let release
    // the persona PATCH the flush sends is slow: the switch must wait for it
    answer = (config) => {
      order.push(`${config.method} ${config.url}`)
      if (config.url === '/persona') return new Promise((r) => { release = () => r({ data: {}, status: 200, headers: {}, config }) })
      return Promise.resolve({ data: { account: { id: B, name: 'Bea' } }, status: 200, headers: {}, config })
    }
    const off = registerFlush(() => api.patch('/persona', { contact: { name: 'typed in A' } }))
    const p = switchAccount(B)
    await new Promise((r) => setTimeout(r, 10))
    expect(order).toEqual(['patch /persona'])     // the switch has not gone out yet
    release()
    await expect(p).resolves.toEqual({ id: B, name: 'Bea' })
    expect(order).toEqual(['patch /persona', 'post /auth/switch'])
    expect(sent[0].headers['X-Expected-Account']).toBe(A)   // the flush is still A's
    off()
  })

  it('waits for a write a timer already started (no flush registered for it)', async () => {
    let release
    answer = (config) => (config.url === '/applications/1'
      ? new Promise((r) => { release = () => r({ data: {}, status: 200, headers: {}, config }) })
      : Promise.resolve({ data: {}, status: 200, headers: {}, config }))
    api.patch('/applications/1', { notes: 'x' })
    let done = false
    const w = flushPendingWrites().then(() => { done = true })
    await new Promise((r) => setTimeout(r, 10))
    expect(done).toBe(false)
    release()
    await w
    expect(done).toBe(true)
    await waitForWrites()
  })

  it('a flush that throws does not block the switch', async () => {
    const off = registerFlush(() => { throw new Error('boom') })
    answer = (config) => Promise.resolve({ data: { account: { id: B } }, status: 200, headers: {}, config })
    await expect(switchAccount(B)).resolves.toEqual({ id: B })
    off()
  })
})

describe('other tabs', () => {
  it('a message naming another account stales this page; its own account does not', () => {
    bindPageAccount(A)
    const ev = changedEvents()
    expect(onSwitchMessage({ id: A, name: 'Ann' })).toBe(false)
    expect(onSwitchMessage({ id: B.toUpperCase(), name: 'Bea' })).toBe(true)
    expect(ev.seen).toEqual([{ id: B, name: 'Bea' }])
    ev.off()
  })

  it('a page not bound yet (picker up) ignores messages', () => {
    expect(onSwitchMessage({ id: B, name: 'Bea' })).toBe(false)
    expect(accountChanged()).toBe(null)
  })

  it('the storage-event fallback reaches the same handler', () => {
    bindPageAccount(A)
    const stop = listenForSwitches()
    const ev = changedEvents()
    window.dispatchEvent(new StorageEvent('storage', { key: SWITCH_STORAGE_KEY, newValue: JSON.stringify({ id: B, name: 'Bea' }) }))
    expect(ev.seen).toEqual([{ id: B, name: 'Bea' }])
    ev.off(); stop()
  })

  it('finishSwitch announces the new account and never stales its own tab', () => {
    bindPageAccount(A)
    const posted = []
    const RealBC = globalThis.BroadcastChannel
    globalThis.BroadcastChannel = class { constructor(n) { this.n = n } postMessage(m) { posted.push([this.n, m]) } close() {} }
    const reload = vi.fn()
    const loc = window.location
    Object.defineProperty(window, 'location', { configurable: true, value: { pathname: '/feed', hash: '', search: '', reload, replace: vi.fn() } })
    const ev = changedEvents()
    try {
      finishSwitch({ id: B, name: 'Bea' })
      expect(posted).toEqual([['jn-account', expect.objectContaining({ id: B, name: 'Bea' })]])
      expect(JSON.parse(localStorage.getItem(SWITCH_STORAGE_KEY))).toMatchObject({ id: B, name: 'Bea' })
      expect(reload).toHaveBeenCalled()
      // the reload's own late answers (already the new account) do not raise the notice here
      bindPageAccount(B)
      expect(ev.seen).toEqual([])
    } finally {
      ev.off()
      Object.defineProperty(window, 'location', { configurable: true, value: loc })
      globalThis.BroadcastChannel = RealBC
    }
  })
})
