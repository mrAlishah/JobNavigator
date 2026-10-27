import React, { useEffect, useState } from 'react'
import api from './api'
import { Button, FooterRow, Helper, Label, ModalPanel, Radio, Select } from './ui'
import { initTelemetry, track } from './telemetry'
import { useTheme, themeAttrs } from './theme'

// The owner's telemetry choice (docs/superpowers/specs/2026-10-26-telemetry-design.md): Yes (full)
// or Essentials. Off exists only as JN_TELEMETRY=off, and every place that asks says so.
export const PURPOSE = 'This data is collected to improve JobNavigator and to show which parts need maintaining first, as time for maintenance is limited.'
export const LEVELS = [
  ['full', 'Yes — health and how the app is used'],
  ['essentials', 'Essentials — scraper and AI health only'],
]
// What each level sends; Yes sends the Essentials list plus its own.
export const SENT = {
  essentials: [
    'The version and the number of accounts',
    'Which search types, job boards and AI providers are in use',
    'How often scrapes and AI calls succeed or fail (error types only)',
    'Rough activity counts',
  ],
  full: [
    'Which screens are opened (no ids)',
    'Actions such as saving a job, running a search or exporting a résumé (no text)',
    'The address of a job board whose scrape fails (e.g. nvidia.wd5.myworkdayjobs.com, which can name the company)',
  ],
  never: 'Job titles, search terms, résumé, letter or persona text, anything typed, keys, the browser or IP, or this install’s address.',
  off: 'To turn telemetry off completely, set JN_TELEMETRY=off in .env.',
}
// One paragraph for Settings' (i).
export const SENT_SUMMARY = `Essentials, once a day from the server: ${SENT.essentials.join('; ')}. Yes adds: ${SENT.full.join('; ')}. Never sent: ${SENT.never} ${SENT.off}`

function Bullets({ items }) {
  return items.map((t) => (
    <div key={t} style={{ display: 'flex', gap: 6 }}>
      <Helper style={{ flex: '0 0 auto' }}>•</Helper>
      <Helper>{t}</Helper>
    </div>
  ))
}

function SentFor({ level }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <Label>Sent once a day, from the server</Label>
      <Bullets items={SENT.essentials} />
      {level === 'full' && <>
        <Label style={{ marginTop: 8 }}>Also sent with Yes, as the app is used</Label>
        <Bullets items={SENT.full} />
      </>}
      <Label style={{ marginTop: 8 }}>Never sent</Label>
      <Helper>{SENT.never}</Helper>
      <Helper style={{ marginTop: 8 }}>{SENT.off}</Helper>
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
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const save = async () => {
    setBusy(true)
    try { await choose(level); onDone?.() } catch { setBusy(false) }
  }
  // No onClose: only Save closes it (Escape and a click outside do nothing), so a choice is always made.
  return (
    <ModalPanel width={460} title="Help improve JobNavigator" escapeCapture zIndex={9997}
      scrimProps={{ className: 'jn-v2', ...themeAttrs(look) }} scrimStyle={{ padding: 16 }}
      style={{ maxWidth: '100%', maxHeight: 'calc(100vh - 32px)', overflow: 'auto' }}>
      <div style={{ padding: '20px 24px 14px', display: 'flex', flexDirection: 'column', gap: 14 }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          <span className="v2-dialogtitle" style={{ fontWeight: 600 }}>Allow anonymous telemetry?</span>
          <Helper>{PURPOSE}</Helper>
        </div>
        <div role="radiogroup" aria-label="Allow anonymous telemetry" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {LEVELS.map(([v, label]) => <Radio key={v} checked={level === v} onChange={() => setLevel(v)} label={label} />)}
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          <Helper onClick={() => setOpen((o) => !o)} ariaLabel={open ? 'Hide what is sent' : 'What is sent?'}>
            {`${open ? '▾' : '▸'} What is sent?`}
          </Helper>
          {open && <SentFor level={level} />}
        </div>
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
      width="300px" ariaLabel="Allow anonymous telemetry"
      onPick={async (v) => { if (v) { await choose(v); load() } }} />
  )
}
