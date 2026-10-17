// Accounts on the client (MT-12): who this browser is, who else exists, and the
// switch itself. The server holds no "current account": this browser's choice is
// the signed httpOnly `jn_account` cookie that POST /api/auth/switch sets, so a
// switch is "set the cookie, drop what the previous account left in this
// browser, reload".
//
// Everything account-related in the UI renders only when `multi` is true (two or
// more accounts): a single-account install must look exactly as it did before
// multi-tenancy.
import { useEffect, useRef, useState } from 'react'
import api, { beginOwnSwitch, bindPageAccount, getPageAccount, markAccountChanged, waitForWrites } from './api'

// ── pending writes before a switch (QA F7 / R6-01) ──────────────────────────
// A debounced editor (Persona, résumé, letter, application notes, a Settings list
// box) registers a flush that sends what its timer still owes and resolves when
// that write has answered. A switch runs every flush and waits for every write
// already on the wire BEFORE it moves the cookie, so the edit lands in the
// account it was typed in, never in the next one.
const flushers = new Set()
export function registerFlush(fn) {
  flushers.add(fn)
  return () => { flushers.delete(fn) }
}
// The hook form: `fn` may change every render; the registration does not.
export function useFlushBeforeSwitch(fn) {
  const ref = useRef(fn)
  ref.current = fn
  useEffect(() => registerFlush(() => ref.current?.()), [])
}
export async function flushPendingWrites() {
  await Promise.allSettled([...flushers].map((f) => {
    try { return Promise.resolve(f()) } catch (e) { return Promise.reject(e) }
  }))
  await waitForWrites()
}

// ── other tabs (QA F7 / R6-01) ──────────────────────────────────────────────
// The switching tab tells the others (BroadcastChannel `jn-account`; a
// localStorage key + `storage` event where BroadcastChannel is missing). A tab
// that shows another account goes stale: api.js stops its writes and App.jsx
// shows the blocking reload notice.
export const SWITCH_CHANNEL = 'jn-account'
export const SWITCH_STORAGE_KEY = 'jn:account-switch'
export function announceSwitch(account) {
  const msg = { id: String(account?.id || ''), name: account?.name || '', t: Date.now() }
  try {
    if (typeof BroadcastChannel !== 'undefined') {
      const ch = new BroadcastChannel(SWITCH_CHANNEL); ch.postMessage(msg); ch.close()
    }
  } catch { /* the storage event below still reaches the other tabs */ }
  try { localStorage.setItem(SWITCH_STORAGE_KEY, JSON.stringify(msg)) } catch { /* ignore */ }
}
// A message from another tab: stale when it names an account this page does not show.
export function onSwitchMessage(msg) {
  const id = String(msg?.id || '').toLowerCase()
  const mine = getPageAccount()
  if (!id || !mine || id === mine) return false
  markAccountChanged({ id, name: msg?.name || '' })
  return true
}
// Installed once by App.jsx; returns the teardown.
export function listenForSwitches() {
  let ch = null
  try {
    if (typeof BroadcastChannel !== 'undefined') {
      ch = new BroadcastChannel(SWITCH_CHANNEL)
      ch.onmessage = (e) => onSwitchMessage(e.data)
    }
  } catch { ch = null }
  const onStorage = (e) => {
    if (e.key !== SWITCH_STORAGE_KEY || !e.newValue) return
    try { onSwitchMessage(JSON.parse(e.newValue)) } catch { /* ignore */ }
  }
  window.addEventListener('storage', onStorage)
  return () => { window.removeEventListener('storage', onStorage); try { ch?.close() } catch { /* ignore */ } }
}

// ── what a switch clears ────────────────────────────────────────────────────
// Browser storage is per BROWSER, not per account. These keys hold the previous
// account's data (or a filter naming its rows) and would paint it into the next
// account's screens, so a switch drops them:
//   jobnavigator_v2_railcache   rail badge counts + scrape-health snapshot
//   jobnavigator_v2_warm:*      useWarm's last-good screen numbers (feed, searches, …)
//   v2_feed_filters             Feed filters (companies, searches, statuses of that account)
//   company_query               Companies search box
//   company_filter_tiers        Companies tier filter
//   session: jobnavigator_v2_searchtest   a search test preview still in flight
//   session: jobnavigator_v2_flash        a toast queued for the next screen
//   session: jn:health_dismissed          the old (v1) health banner's dismissal, cleared for good measure
// Kept, because they belong to the device or the person at it rather than to the
// account's data: the API key, appearance + theme, rail collapsed, the welcome /
// what's-new marks, posting zoom, sort orders, open/closed panels and sections,
// the Settings tab, `v2_feed_frameable` (a per-host fact about framing) and
// `jobnavigator_v2_resume_reviewed` (résumé ids, unique across accounts).
export const SWITCH_CLEARS_LOCAL = ['jobnavigator_v2_railcache', 'v2_feed_filters', 'company_query', 'company_filter_tiers']
export const SWITCH_CLEARS_LOCAL_PREFIX = ['jobnavigator_v2_warm:']
export const SWITCH_CLEARS_SESSION = ['jobnavigator_v2_searchtest', 'jobnavigator_v2_flash', 'jn:health_dismissed']
const SWITCHED_KEY = 'jn:switched'   // sessionStorage: the toast the next page load shows

export function clearAccountCaches() {
  try {
    SWITCH_CLEARS_LOCAL.forEach((k) => localStorage.removeItem(k))
    const drop = []
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i)
      if (k && SWITCH_CLEARS_LOCAL_PREFIX.some((p) => k.startsWith(p))) drop.push(k)
    }
    drop.forEach((k) => localStorage.removeItem(k))
  } catch { /* storage blocked: nothing was cached either */ }
  try { SWITCH_CLEARS_SESSION.forEach((k) => sessionStorage.removeItem(k)) } catch { /* ignore */ }
}

// A detail route names a row of the previous account (/resumes/123), and a query
// may too (?job=…, ?company=…): after a switch those land on the list instead.
export function pathAfterSwitch(loc) {
  const path = loc.pathname.replace(/^\/(resumes|cover-letters)\/.+$/, '/$1')
  return path + loc.hash
}

// The switch is done (cookie set): clear, remember the toast, reload this screen.
export function finishSwitch(account) {
  beginOwnSwitch()
  announceSwitch(account)
  clearAccountCaches()
  try { sessionStorage.setItem(SWITCHED_KEY, JSON.stringify({ name: account?.name || '', color: account?.color || '' })) } catch { /* ignore */ }
  const loc = window.location
  const next = pathAfterSwitch(loc)
  if (next === loc.pathname + loc.hash && !loc.search) loc.reload()
  else loc.replace(next)
}

// Read (and consume) the "Switched to …" note the previous page left.
export function takeSwitchedNote() {
  try {
    const raw = sessionStorage.getItem(SWITCHED_KEY)
    if (!raw) return null
    sessionStorage.removeItem(SWITCHED_KEY)
    const v = JSON.parse(raw)
    return v && v.name ? v : null
  } catch { return null }
}

// POST /api/auth/switch. Resolves with the account, rejects with the axios error
// (classify it with api.js `authFailure`).
// Pending autosaves are flushed (and in-flight writes awaited) first, so nothing
// typed in this account is sent after the cookie names the next one.
export async function switchAccount(accountId, pin) {
  await flushPendingWrites()
  const body = { account_id: accountId }
  if (pin) body.pin = pin
  const { data } = await api.post('/auth/switch', body)
  return data?.account
}

// Ask the app-level picker (App.jsx) to open: on an account (its PIN pad), or
// without one (the grid).
export function openPicker(account) {
  window.dispatchEvent(new CustomEvent('jn:account-pick', { detail: { account: account || null } }))
}

// The switch shortcuts (QA F7 / R6-03): the CONTROL key + Shift + 1–5 on every
// platform — ⌃⇧1–5 on macOS, Ctrl+Shift+1–5 elsewhere. ⌘⇧3/4/5 are macOS
// screenshot keys (and ⌘⌃⇧3/4 their clipboard twins, hence no Meta at all);
// ⌃⇧digit has no macOS, Chrome, Firefox, Safari or Edge default, and the app's
// own keys (Feed letters/arrows, résumé Ctrl+B, picker bare 1–5) never use
// Ctrl+Shift+digit. Alt is left out: Alt+Shift switches the Windows input
// language and AltGr is Ctrl+Alt.
export const IS_MAC = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent || '')
export const SWITCH_MOD = IS_MAC ? '⌃⇧' : 'Ctrl⇧'
export const MAX_SHORTCUTS = 5
export const MAX_ACCOUNTS = 5   // backend routes_accounts MAX_ACCOUNTS (409 account_limit)

// `e` is a keydown: the 0-based account index for Ctrl+Shift+1–5 (⌃⇧ on a Mac), else -1.
// `code` rather than `key`: with Shift held, `key` is "!" / "@" / … per layout.
export function switchShortcutIndex(e) {
  if (!e.ctrlKey || !e.shiftKey || e.altKey || e.metaKey) return -1
  const m = /^Digit([1-9])$/.exec(e.code || '')
  if (!m) return -1
  const i = Number(m[1]) - 1
  return i < MAX_SHORTCUTS ? i : -1
}

export const roleLabel = (a) => (a?.role === 'owner' ? 'Owner' : 'Member')

// This browser's account and the install's accounts. `/auth/me` first; the
// list is only fetched when there is more than one account. Refreshes on
// `jn:accounts-changed` (Settings › Accounts dispatches it after an add/remove).
// /auth/me `owner_unlocked_until` as epoch ms, or null when locked / expired.
const unlockAt = (data) => {
  const t = Date.parse(data?.owner_unlocked_until || '')
  return Number.isFinite(t) && t > Date.now() ? t : null
}
export function useAccounts() {
  const [state, setState] = useState({ me: null, accounts: [], count: 0 })
  useEffect(() => {
    let live = true
    const load = () => {
      api.get('/auth/me').then(({ data }) => {
        if (!live || !data?.account) return
        bindPageAccount(data.account.id)
        const count = Number(data.accounts_count) || 1
        if (count < 2) { setState({ me: data.account, accounts: [], count, unlockedUntil: unlockAt(data) }); return }
        api.get('/auth/accounts').then(({ data: list }) => {
          if (live) setState({ me: data.account, accounts: Array.isArray(list) ? list : [], count, unlockedUntil: unlockAt(data) })
        }).catch(() => { /* the rail just stays single-account */ })
      }).catch(() => { /* 401s raise their own overlay through api.js */ })
    }
    load()
    window.addEventListener('jn:accounts-changed', load)
    return () => { live = false; window.removeEventListener('jn:accounts-changed', load) }
  }, [])
  const multi = state.count >= 2 && state.accounts.length >= 2 && !!state.me
  return { ...state, multi }
}
