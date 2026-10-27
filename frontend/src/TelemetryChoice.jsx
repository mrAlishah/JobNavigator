import React, { useEffect, useState } from 'react'
import api from './api'
import { Button, FooterRow, Helper, ModalPanel, Radio, Select } from './ui'
import { initTelemetry, track } from './telemetry'
import { useTheme, themeAttrs } from './theme'

// The owner's telemetry choice (docs/superpowers/specs/2026-10-26-telemetry-design.md): Yes (full)
// or Essentials. Off exists only as JN_TELEMETRY=off, and every place that asks says so.
export const PURPOSE = 'I’d like to collect this to improve JobNavigator and to see what needs maintenance most, since my time for it is limited.'
export const LEVELS = [
  ['full', 'Yes — health and how the app is used'],
  ['essentials', 'Essentials — scraper and AI health only'],
]
export const SENT = {
  essentials: 'Once a day from the server: the version, how many accounts, which search types, job boards and AI providers are in use, how often scrapes and AI calls succeed or fail (error types only), and rough activity counts.',
  full: 'Also: which screens are opened (no ids) and actions such as saving a job, running a search or exporting a résumé (no text), passed on by this server, and the address of a job board whose scrape fails (e.g. nvidia.wd5.myworkdayjobs.com, which can name the company).',
  never: 'Never: job titles, search terms, résumé, letter or persona text, anything you type, keys, your browser or IP, or this install’s address; company names only inside a failing job board’s address, with Yes.',
  off: 'To turn it off completely, set JN_TELEMETRY=off in .env.',
}

function SentList() {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
      <Helper>{SENT.essentials}</Helper>
      <Helper>{SENT.full}</Helper>
      <Helper>{SENT.never}</Helper>
      <Helper>{SENT.off}</Helper>
    </div>
  )
}

async function choose(level) {
  await api.patch('/settings', { telemetry_level: level })
  await initTelemetry()   // Yes: the browser part starts now, not on the next load
  track('telemetry_chosen', { level })
}

// Asked once, of the owner, when no choice is stored and JN_TELEMETRY does not decide.
export function TelemetryDialog({ onDone }) {
  const look = useTheme()
  const [level, setLevel] = useState('full')
  const [more, setMore] = useState(false)
  const [busy, setBusy] = useState(false)
  const save = async () => {
    setBusy(true)
    try { await choose(level); onDone?.() } catch { setBusy(false) }
  }
  return (
    <ModalPanel width={440} title="Help improve JobNavigator" onClose={onDone} escapeCapture zIndex={9997}
      scrimProps={{ className: 'jn-v2', ...themeAttrs(look) }} scrimStyle={{ padding: 16 }}
      style={{ maxWidth: '100%', overflow: 'hidden' }}>
      <div style={{ padding: '20px 24px 6px', display: 'flex', flexDirection: 'column', gap: 12 }}>
        <span className="v2-dialogtitle" style={{ fontWeight: 600 }}>Allow telemetry?</span>
        <Helper>{PURPOSE}</Helper>
        <div role="radiogroup" aria-label="Allow telemetry" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {LEVELS.map(([v, label]) => <Radio key={v} checked={level === v} onChange={() => setLevel(v)} label={label} />)}
        </div>
        {more ? <SentList /> : <Helper onClick={() => setMore(true)} ariaLabel="What is sent">What is sent?</Helper>}
      </div>
      <FooterRow pad="12px 24px" soft bg="page" gap="normal">
        <Button size="xs" busy={busy} onClick={save} style={{ marginLeft: 'auto' }}>Save</Button>
      </FooterRow>
    </ModalPanel>
  )
}

// App-level: decides whether to ask (owner, not asked, not locked, not the demo).
export function TelemetryAsk() {
  const [ask, setAsk] = useState(false)
  useEffect(() => {
    if (__DEMO__) return
    api.get('/telemetry').then(({ data }) => setAsk(!!data && data.owner && !data.asked && !data.locked)).catch(() => {})
  }, [])
  return ask ? <TelemetryDialog onDone={() => setAsk(false)} /> : null
}

// Settings › Telemetry: the choice, or what JN_TELEMETRY set.
export function TelemetrySettingRow() {
  const [state, setState] = useState(null)
  const load = () => api.get('/telemetry').then(({ data }) => setState(data)).catch(() => {})
  useEffect(() => { load() }, [])
  if (!state) return null
  if (state.locked) return <Helper style={{ flex: 1, minWidth: 0 }}>{`Set by JN_TELEMETRY=${state.level} in .env.`}</Helper>
  return (
    <Select value={state.asked ? state.level : ''} options={state.asked ? LEVELS : [['', 'Not chosen yet'], ...LEVELS]}
      width="300px" ariaLabel="Allow telemetry"
      onPick={async (v) => { if (v) { await choose(v); load() } }} />
  )
}
