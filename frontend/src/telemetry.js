// Telemetry from the browser (docs/superpowers/specs/2026-10-26-telemetry-design.md).
// Only when the owner chose Yes (`full`): page views with ids replaced and no query, and the named events
// below, posted to this install's backend (never to Umami directly). Every field takes only a value from its list
// (or a short id / a bucket); anything else is dropped, so no typed text, name or URL can be sent.
import api from './api'
import { IS_DEMO } from './demoHooks'

const ID = /^[a-z0-9_:.-]{1,32}$/
export const EVENTS = {
  session_start: { appearance: ['light', 'dark', 'system'], theme: 'id', device: ['phone', 'tablet', 'desktop'] },
  telemetry_chosen: { level: ['full', 'essentials'] },
  job_saved: {},
  job_skipped: {},
  job_opened: { link_kind: ['direct', 'board', 'easy_apply', 'none'] },
  feed_shortcut: { key: ['j', 'k', 's', 'x', 'e', 'o', 'f', 'g', 'a', 'r', 't', 'ArrowDown', 'ArrowUp', 'Enter'] },
  search_created: { mode: 'id' },
  search_run: { mode: 'id' },
  search_tested: { mode: 'id' },
  company_added: { ats: 'id' },
  company_scraped: { ats: 'id' },
  status_changed: { from: 'id', to: 'id' },
  tailor_started: { from: ['job', 'pasted'] },
  score_run: { depth: ['light', 'full'] },
  resume_exported: { format: ['pdf', 'docx'], template: 'id' },
  letter_generated: { voice: ['professional', 'warm', 'formal', 'confident', 'storytelling', 'custom'] },
  letter_exported: { format: ['pdf'] },
  theme_changed: { appearance: ['light', 'dark', 'system'] },
}

// The seeded voice presets by id; one the user wrote is just 'custom' (its id may be a name).
const BUILTIN_VOICES = ['professional', 'warm', 'formal', 'confident', 'storytelling']
export const voiceId = (v) => (BUILTIN_VOICES.includes(v) ? v : 'custom')

// Feed shortcuts that count: anything else typed on the feed never becomes an event.
const SHORTCUT_KEYS = new Set(EVENTS.feed_shortcut.key)
export const isShortcut = (key) => SHORTCUT_KEYS.has(key)

const MAX_EVENTS = 300
const DEDUPE_MS = 3000

let enabled = false
let sent = 0
const recent = new Map()

export function bucket(n) {
  const v = Number(n) || 0
  if (v <= 0) return '0'
  if (v === 1) return '1'
  if (v <= 5) return '2_5'
  if (v <= 20) return '6_20'
  if (v <= 100) return '21_100'
  return '101_plus'
}

// '/resumes/4f1c…' → '/resumes/:id'; query and hash never leave.
export function normalizePath(path) {
  const p = String(path || '/').split(/[?#]/)[0]
  const parts = p.split('/').map(seg => (
    /^[0-9a-f-]{16,}$/i.test(seg) || /^\d+$/.test(seg) || (seg.length >= 8 && /\d/.test(seg) && /^[A-Za-z0-9_-]+$/.test(seg))
      ? ':id' : seg))
  return parts.join('/') || '/'
}

export function sanitize(name, data) {
  const spec = EVENTS[name]
  if (!spec) return null
  const out = {}
  for (const [key, rule] of Object.entries(spec)) {
    const v = data?.[key]
    if (v === undefined || v === null) continue
    if (Array.isArray(rule)) { if (rule.includes(v)) out[key] = v }
    else if (rule === 'bucket') out[key] = bucket(v)
    else if (rule === 'id') { const s = String(v).toLowerCase(); if (ID.test(s)) out[key] = s }
  }
  return out
}

// The browser never talks to Umami: events go to this install's backend, which checks them
// again and forwards them (so Umami sees neither the user's browser and IP nor the install).
function send(body) {
  try { api.post('/telemetry/event', body).catch(() => {}) } catch { /* never break the app */ }
}

export function track(name, data) {
  if (!enabled || sent >= MAX_EVENTS) return
  const clean = sanitize(name, data)
  if (!clean) return
  const key = name + JSON.stringify(clean)
  const now = Date.now()
  const last = recent.get(key)
  recent.set(key, now)
  if (last && now - last < DEDUPE_MS) return
  sent++
  send({ name, data: clean, url: normalizePath(window.location.pathname) })
}

export function trackPage(pathname) {
  if (!enabled || sent >= MAX_EVENTS) return
  sent++
  send({ name: 'page_view', data: {}, url: normalizePath(pathname) })
}

const doNotTrack = () => {
  try { return navigator.doNotTrack === '1' || window.doNotTrack === '1' } catch { return false }
}

// On load and after the owner changes the choice: on only at `full`, never in the demo or when
// the browser asks not to be tracked; Essentials (or anything else) turns it off at once.
// Returns the /telemetry answer (the choice UI reads it).
export async function initTelemetry() {
  let state = null
  try { state = (await api.get('/telemetry')).data } catch { enabled = false; return null }
  enabled = !IS_DEMO && state?.level === 'full' && !doNotTrack()
  return state
}

export function __resetTelemetry() { enabled = false; sent = 0; recent.clear() }
export function __enableForTests() { enabled = true }
