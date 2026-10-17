// MT-12 client contract: how a failed request is classified (api.js
// `authFailure` + the interceptor's events), the ⌘⇧1–5 shortcut mapping, and what
// a switch clears from this browser's storage.
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { authFailure } from '../api'
import { clearAccountCaches, pathAfterSwitch, switchShortcutIndex, takeSwitchedNote } from '../accounts'

const err = (status, data, headers = {}) => ({ response: { status, data, headers } })

describe('authFailure', () => {
  it('account_required (body or header) → the picker', () => {
    expect(authFailure(err(401, { detail: 'account_required' }, { 'x-jn-auth': 'account' }))).toEqual({ kind: 'account' })
    expect(authFailure(err(401, {}, { 'x-jn-auth': 'account' }))).toEqual({ kind: 'account' })
    expect(authFailure(err(401, { detail: 'invalid_account' }))).toEqual({ kind: 'account' })
  })

  it('PIN answers carry their numbers', () => {
    expect(authFailure(err(401, { detail: 'pin_required' }))).toEqual({ kind: 'pin_required' })
    expect(authFailure(err(401, { detail: 'wrong_pin', tries_left: 3 }))).toEqual({ kind: 'wrong_pin', triesLeft: 3 })
    expect(authFailure(err(429, { detail: 'pin_cooldown' }, { 'retry-after': '27' }))).toEqual({ kind: 'pin_cooldown', retryAfter: 27 })
    expect(authFailure(err(429, { detail: 'pin_cooldown' }))).toEqual({ kind: 'pin_cooldown', retryAfter: 30 })
  })

  it('any other 401 is the install-password sign-in; other errors are not auth', () => {
    expect(authFailure(err(401, { detail: 'API key required' }))).toEqual({ kind: 'login' })
    expect(authFailure(err(401, { detail: 'Invalid API key' }))).toEqual({ kind: 'login' })
    expect(authFailure(err(401, undefined))).toEqual({ kind: 'login' })
    expect(authFailure(err(429, { detail: 'Too many failed attempts' }))).toBe(null)
    expect(authFailure(err(403, { detail: 'owner_only' }))).toBe(null)
    expect(authFailure(err(500, {}))).toBe(null)
    expect(authFailure(new Error('Network Error'))).toBe(null)
  })
})

describe('the api interceptor', () => {
  it('raises jn:account-required, jn:unauthorized, and nothing for a PIN answer', async () => {
    const { default: api } = await import('../api')
    const reject = api.interceptors.response.handlers.find((h) => h && h.rejected).rejected
    const seen = []
    const on = (e) => seen.push(e.type)
    window.addEventListener('jn:account-required', on)
    window.addEventListener('jn:unauthorized', on)
    await reject(err(401, { detail: 'account_required' })).catch(() => {})
    await reject(err(401, { detail: 'wrong_pin', tries_left: 2 })).catch(() => {})
    await reject(err(429, { detail: 'pin_cooldown' })).catch(() => {})
    await reject(err(401, { detail: 'Invalid API key' })).catch(() => {})
    window.removeEventListener('jn:account-required', on)
    window.removeEventListener('jn:unauthorized', on)
    expect(seen).toEqual(['jn:account-required', 'jn:unauthorized'])
  })
})

describe('switchShortcutIndex', () => {
  const k = (code, mods = {}) => ({ code, metaKey: false, ctrlKey: false, shiftKey: false, altKey: false, ...mods })
  it('maps Ctrl+Shift+1–5 (⌃⇧ on a Mac) to 0–4', () => {
    expect(switchShortcutIndex(k('Digit1', { ctrlKey: true, shiftKey: true }))).toBe(0)
    expect(switchShortcutIndex(k('Digit5', { ctrlKey: true, shiftKey: true }))).toBe(4)
  })
  it('never ⌘⇧3/4/5 (macOS screenshots) or ⌘⌃⇧ (screenshot to clipboard) — R6-03', () => {
    for (const d of ['Digit1', 'Digit3', 'Digit4', 'Digit5']) {
      expect(switchShortcutIndex(k(d, { metaKey: true, shiftKey: true }))).toBe(-1)
      expect(switchShortcutIndex(k(d, { metaKey: true, ctrlKey: true, shiftKey: true }))).toBe(-1)
    }
  })
  it('ignores everything else', () => {
    expect(switchShortcutIndex(k('Digit6', { ctrlKey: true, shiftKey: true }))).toBe(-1)
    expect(switchShortcutIndex(k('Digit1', { ctrlKey: true }))).toBe(-1)           // Ctrl+1 = browser tab
    expect(switchShortcutIndex(k('Digit1', { shiftKey: true }))).toBe(-1)
    expect(switchShortcutIndex(k('Digit1', { ctrlKey: true, shiftKey: true, altKey: true }))).toBe(-1)
    expect(switchShortcutIndex(k('KeyA', { ctrlKey: true, shiftKey: true }))).toBe(-1)
    expect(switchShortcutIndex(k('Equal', { ctrlKey: true, shiftKey: true }))).toBe(-1)   // ⌘± zoom
  })
})

describe('clearAccountCaches', () => {
  beforeEach(() => { localStorage.clear(); sessionStorage.clear() })
  it('drops the previous account’s data and keeps the device’s preferences', () => {
    const drop = ['jobnavigator_v2_railcache', 'jobnavigator_v2_warm:feed', 'jobnavigator_v2_warm:searches', 'v2_feed_filters', 'company_query', 'company_filter_tiers']
    const keep = ['jobnavigator_api_key', 'jobnavigator_appearance', 'jobnavigator_theme', 'jobnavigator_v2_rail', 'jobnavigator_welcomed', 'jobnavigator_newui_seen', 'jobnavigator_post_zoom', 'v2_feed_sort', 'company_sort', 'v2_feed_ui', 'settings_tab', 'v2_feed_frameable']
    ;[...drop, ...keep].forEach((key) => localStorage.setItem(key, 'x'))
    ;['jobnavigator_v2_searchtest', 'jobnavigator_v2_flash', 'jn:health_dismissed'].forEach((key) => sessionStorage.setItem(key, 'x'))
    sessionStorage.setItem('jn:welcome', '1')
    clearAccountCaches()
    drop.forEach((key) => expect(localStorage.getItem(key)).toBe(null))
    keep.forEach((key) => expect(localStorage.getItem(key)).toBe('x'))
    expect(sessionStorage.getItem('jobnavigator_v2_searchtest')).toBe(null)
    expect(sessionStorage.getItem('jobnavigator_v2_flash')).toBe(null)
    expect(sessionStorage.getItem('jn:health_dismissed')).toBe(null)
    expect(sessionStorage.getItem('jn:welcome')).toBe('1')
  })
})

describe('after a switch', () => {
  it('a detail route falls back to its list; query strings are dropped', () => {
    expect(pathAfterSwitch({ pathname: '/resumes/123', hash: '' })).toBe('/resumes')
    expect(pathAfterSwitch({ pathname: '/cover-letters/9', hash: '' })).toBe('/cover-letters')
    expect(pathAfterSwitch({ pathname: '/feed', hash: '' })).toBe('/feed')
    expect(pathAfterSwitch({ pathname: '/stats', hash: '#runs' })).toBe('/stats#runs')
  })
  it('the toast note is read once', () => {
    sessionStorage.setItem('jn:switched', JSON.stringify({ name: 'Alex', color: '#2f6fed' }))
    expect(takeSwitchedNote()).toEqual({ name: 'Alex', color: '#2f6fed' })
    expect(takeSwitchedNote()).toBe(null)
  })
})
