import React, { useEffect, useState } from 'react'
import api, { authFailure } from '../api'
import { PromptDialog } from '../ConfirmDialog'
import {
  ACCOUNT_SWATCHES, Avatar, Button, Check, Heading, Helper, Input, Link, ModalPanel, Pill,
  PinPad, Segmented, Select, Surface, Switch, Tag, kb,
} from '../ui'

// Settings › Accounts (owner) and Settings › Your account (member) — MT-13.
//
// QA F2 / D2: the owner always gets an "Accounts" section first in the page — with
// one account it holds the owner's own row and "+ Add account", from two on the
// full table. A member gets "Your account" first. PINs are optional everywhere
// (D3), the owner's included.
//
// Every account change dispatches `jn:accounts-changed` (the rail switcher and
// the Settings page reload their lists) and `jn:counts-changed` (rail badges).

export const COPY_ITEMS = [
  ['persona', 'Persona', 'every node, Q&A bank included'],
  ['qa', 'Q&A bank only', 'autofill answers, without the rest of the Persona'],
  ['resumes', 'Base résumés', 'tailored copies stay behind'],
  ['companies', 'Companies', 'they ride the same scrapes'],
  ['searches', 'Searches', 'the built-in extension searches are always there'],
  ['settings', 'Settings', 'never keys, sessions, chat ids or Gmail'],
  ['voices', 'Cover-letter voices', 'presets and the default voice'],
  ['excludes', 'Global excludes', 'body, title and company excludes'],
]

const COUNT_LABELS = [
  ['jobs', 'job', 'jobs'], ['applications', 'application', 'applications'], ['resumes', 'résumé', 'résumés'],
  ['cover_letters', 'cover letter', 'cover letters'], ['searches', 'search', 'searches'], ['companies', 'company', 'companies'],
]

// "1,204 jobs · 12 applications · …" — zero counts are left out.
export function countsLine(counts, all = false) {
  const c = counts || {}
  const parts = COUNT_LABELS
    .filter(([k]) => all || Number(c[k]) > 0)
    .map(([k, one, many]) => `${Number(c[k] || 0).toLocaleString()} ${Number(c[k]) === 1 ? one : many}`)
  return parts.length ? parts.join(' · ') : 'no data yet'
}

export const accountsChanged = () => {
  window.dispatchEvent(new CustomEvent('jn:accounts-changed'))
  window.dispatchEvent(new CustomEvent('jn:counts-changed'))
}

// The MT-11 error bodies are `{detail: code, message}`; PIN answers carry their own numbers.
const CODE_TEXT = {
  account_limit: 'This install already has 5 accounts — remove one first.',
  name_taken: 'Another account already has that name.',
  account_busy: 'That account has background work running — try again when it finishes.',
  backup_failed: 'The database backup failed, so nothing was removed. See the server log.',
  confirm_name_mismatch: 'Type the account’s name exactly to confirm.',
  cannot_remove_owner: 'The owner account cannot be removed.',
  cannot_remove_self: 'You cannot remove the account you are using.',
  owner_only: 'Only the owner can do that.',
  // QA F7 / R6-08: every code a Settings action can answer with reads as a sentence
  own_account_only: 'You can only do that for your own account.',
  no_account: 'Pick an account first — this browser has none selected.',
  gmail_not_configured: 'Gmail is not set up on this install yet — the owner adds the Google OAuth client first.',
  scheduled_scrape_running: 'The scheduled scrape is running — try again when it finishes.',
  account_changed: 'This tab was switched to another account — reload it.',
  invalid_account_token: 'That account token is no longer valid — create a new one.',
  account_required: 'Pick an account first.',
  // QA F8 / R5-03
  chat_in_use: 'That Telegram chat is already linked to another account — use another chat, or your own bot.',
  'No account to link': 'Pick an account first — there is no account to link this chat to.',
}
export function errorText(err, fallback = 'Something went wrong — try again.') {
  const f = authFailure(err)
  if (f?.kind === 'wrong_pin') return f.triesLeft != null ? `Wrong PIN · ${f.triesLeft} ${f.triesLeft === 1 ? 'try' : 'tries'} left` : 'Wrong PIN'
  if (f?.kind === 'pin_required') return 'Enter the current PIN.'
  if (f?.kind === 'pin_cooldown') return `Too many tries — wait ${f.retryAfter}s.`
  const d = err?.response?.data
  if (d && typeof d.detail === 'string' && CODE_TEXT[d.detail]) return CODE_TEXT[d.detail]
  if (d && typeof d.message === 'string' && d.message) return d.message
  if (d && typeof d.detail === 'string' && d.detail) return d.detail
  return fallback
}

const ago = (iso) => {
  if (!iso) return 'never'
  const m = Math.floor((Date.now() - new Date(iso).getTime()) / 60000)
  if (!Number.isFinite(m)) return '—'
  if (m < 2) return 'just now'
  if (m < 60) return `${m} min ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h} h ago`
  const d = Math.floor(h / 24)
  return d < 30 ? `${d} d ago` : new Date(iso).toLocaleDateString()
}

// Export = a zip streamed by GET /accounts/{id}/export. Fetched through axios so the
// install key in the header works as well as the session cookie.
export async function downloadExport(account) {
  const r = await api.get(`/accounts/${account.id}/export`, { responseType: 'blob' })
  const cd = String(r.headers?.['content-disposition'] || '')
  const m = /filename="?([^";]+)"?/.exec(cd)
  const name = m ? m[1] : `jobnavigator-${account.name}.zip`
  const url = URL.createObjectURL(r.data)
  const a = document.createElement('a')
  a.href = url; a.download = name
  document.body.appendChild(a); a.click(); a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 4000)
}

// ── colour swatches ─────────────────────────────────────────────────────────
// The five account colours as avatars carrying the account's initials; the
// chosen one is marked by the radio state, not by a hand-drawn ring.
export function ColorPicker({ name, value, onPick, taken = [], ariaLabel = 'Colour' }) {
  return (
    <div role="radiogroup" aria-label={ariaLabel} style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
      {ACCOUNT_SWATCHES.map((c, i) => {
        const on = (value || '').toLowerCase() === c
        const used = taken.includes(c) && !on
        return (
          <span key={c} role="radio" aria-checked={on} aria-label={`Colour ${i + 1}${used ? ' (in use)' : ''}`}
            onClick={() => onPick(c)} {...kb(() => onPick(c), 'radio')}
            title={used ? 'Another account uses this colour' : undefined}
            style={{ display: 'flex', padding: 2, cursor: 'pointer', opacity: used ? 0.5 : 1,
              borderBottom: `2px solid ${on ? 'var(--accent)' : 'transparent'}` }}>
            <Avatar name={name || '?'} color={c} size={26} />
          </span>
        )
      })}
    </div>
  )
}

// ── PIN dialog ──────────────────────────────────────────────────────────────
// One dialog for every PIN change: steps `current` (own PIN when one is set) →
// `new` → `confirm`. `mode`: 'set' (new + confirm) or 'remove' (current only).
// `submit({pin, current_pin})` does the PATCH and throws the axios error.
export function PinDialog({ title, who, needCurrent, mode = 'set', intro, onSubmit, onClose, onRemove }) {
  const steps = [...(needCurrent ? ['current'] : []), ...(mode === 'set' ? ['new', 'confirm'] : [])]
  const [step, setStep] = useState(0)
  const [vals, setVals] = useState({})
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [cooldown, setCooldown] = useState(0)
  const [attempt, setAttempt] = useState(0)
  const name = steps[step]
  const prompt = name === 'current' ? `Current PIN${who ? ` for ${who}` : ''}`
    : name === 'new' ? `New PIN${who ? ` for ${who}` : ''}` : 'Same PIN again'

  const finish = async (all) => {
    setBusy(true); setError('')
    try {
      await onSubmit(mode === 'set' ? { pin: all.new, current_pin: all.current } : { pin: null, current_pin: all.current })
    } catch (err) {
      const f = authFailure(err)
      if (f?.kind === 'pin_cooldown') setCooldown(f.retryAfter)
      setError(errorText(err))
      // a wrong current PIN sends the dialog back to that step
      if (f && needCurrent) setStep(0)
      setAttempt((n) => n + 1)
      setBusy(false)
    }
  }
  const complete = (pin) => {
    const all = { ...vals, [name]: pin }
    setVals(all); setError('')
    if (name === 'confirm' && pin !== all.new) {
      setError('The two PINs differ — enter the new PIN again.')
      setStep(steps.indexOf('new')); setAttempt((n) => n + 1); return
    }
    if (step < steps.length - 1) { setStep(step + 1); setAttempt((n) => n + 1); return }
    finish(all)
  }
  return (
    <ModalPanel width={400} title={title} onClose={busy ? undefined : onClose} zIndex={75}
      style={{ padding: '22px 24px 18px', gap: 12 }}>
      <Heading size={19} className="v2-dialogtitle">{title}</Heading>
      {intro && <Helper style={{ textWrap: 'pretty' }}>{intro}</Helper>}
      {/* T7-07: the prompt is the pad's own status line (it used to sit above the boxes and
          leave that line empty, ~50 px of dead space before Cancel) */}
      <PinPad key={name} onComplete={complete} busy={busy} error={error} resetKey={attempt} hint={prompt}
        cooldown={cooldown} onCooldownEnd={() => setCooldown(0)} ariaLabel={prompt} style={{ paddingTop: 4 }} />
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        {onRemove && <Link onClick={onRemove}>Remove PIN instead</Link>}
        <Button variant="secondary" size="sm" onClick={onClose} disabled={busy} style={{ marginLeft: 'auto' }}>Cancel</Button>
      </div>
    </ModalPanel>
  )
}

// ── Add account ─────────────────────────────────────────────────────────────
// Name, colour, optional member PIN, "Start from a copy of…" with the items to
// copy. QA F2 / D3: no owner PIN is needed first (PINs are optional throughout).
export function AddAccountModal({ accounts, onClose, onCreated }) {
  const taken = accounts.map((a) => (a.color || '').toLowerCase())
  const [name, setName] = useState('')
  const [color, setColor] = useState(() => ACCOUNT_SWATCHES.find((c) => !taken.includes(c)) || ACCOUNT_SWATCHES[0])
  const [pin, setPin] = useState('')
  const [source, setSource] = useState('')
  const [items, setItems] = useState(['persona', 'resumes', 'companies', 'searches', 'settings', 'voices', 'excludes'])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const pinOk = pin === '' || /^\d{4}$/.test(pin)
  const ready = name.trim().length > 0 && pinOk && !busy
  const toggle = (k) => setItems((xs) => (xs.includes(k) ? xs.filter((x) => x !== k) : [...xs, k]))

  const create = async () => {
    if (!ready) return
    setBusy(true); setError('')
    const body = { name: name.trim(), color }
    if (pin) body.pin = pin
    if (source && items.length) body.copy_from = { account_id: source, items }
    try {
      const { data } = await api.post('/accounts', body)
      onCreated(data?.account || { name: body.name }, data?.copied || {})
    } catch (err) {
      setError(errorText(err, 'Could not add the account — try again.'))
      setBusy(false)
    }
  }

  const sources = [['', 'Nothing — start empty'], ...accounts.map((a) => [a.id, a.role === 'owner' ? `${a.name} (owner)` : a.name])]
  return (
    <ModalPanel width={520} title="Add account" onClose={busy ? undefined : onClose} zIndex={70}
      style={{ padding: '22px 24px 18px', gap: 14, maxHeight: '92vh', overflow: 'auto' }}>
      <Heading size={19} className="v2-dialogtitle">Add account</Heading>
      <Helper style={{ marginTop: -8, textWrap: 'pretty' }}>
        A person on this install with their own jobs, résumés, applications and settings. Scraping stays shared.
      </Helper>
      <Field label="Name">
        <Input value={name} onChange={setName} placeholder="e.g. Alex" ariaLabel="Account name" autoFocus width="100%"
          onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); create() } }} />
      </Field>
      <Field label="Colour"><ColorPicker name={name} value={color} onPick={setColor} taken={taken} /></Field>
      <Field label="PIN" hint="Optional · 4 digits · your own PIN opens this account too">
        <Input value={pin} onChange={(v) => setPin(v.replace(/\D/g, '').slice(0, 4))} type="password" inputMode="numeric"
          autoComplete="new-password" placeholder="none" ariaLabel="PIN for the new account" mono width="120px" invalid={!pinOk} />
      </Field>
      <Field label="Start from a copy of…">
        <Select value={source} options={sources} onPick={setSource} width="100%" ariaLabel="Copy from account" />
      </Field>
      {source && (
        <div role="group" aria-label="Items to copy" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px 16px', marginTop: -4 }}>
          {COPY_ITEMS.map(([k, label, hint]) => (
            <div key={k} style={{ display: 'flex', flexDirection: 'column', gap: 1, minWidth: 0 }}>
              <Check checked={items.includes(k)} onChange={() => toggle(k)} label={label} />
              <Helper size="xs" style={{ paddingLeft: 22 }}>{hint}</Helper>
            </div>
          ))}
        </div>
      )}
      {error && <Helper style={{ color: 'var(--bad)' }}>{error}</Helper>}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 2 }}>
        <Helper size="xs">No jobs, applications or letters are copied.</Helper>
        <Button variant="secondary" size="sm" onClick={onClose} disabled={busy} style={{ marginLeft: 'auto' }}>Cancel</Button>
        <Button size="sm" onClick={create} disabled={!ready} busy={busy}>Add account</Button>
      </div>
    </ModalPanel>
  )
}

function Field({ label, hint, children }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
      <span style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
        <span style={{ fontSize: 12.5, lineHeight: '18px', fontWeight: 500 }}>{label}</span>
        {hint && <Helper size="xs">{hint}</Helper>}
      </span>
      {children}
    </div>
  )
}

// ── Remove account ──────────────────────────────────────────────────────────
export function RemoveAccountModal({ account, onClose, onRemoved }) {
  const [typed, setTyped] = useState('')
  const [busy, setBusy] = useState(false)
  const [exporting, setExporting] = useState(false)
  const [error, setError] = useState('')
  const ok = typed === account.name && !busy
  const remove = async () => {
    if (!ok) return
    setBusy(true); setError('')
    try {
      const { data } = await api.delete(`/accounts/${account.id}`, { data: { confirm_name: typed } })
      onRemoved(account, data)
    } catch (err) {
      setError(errorText(err, 'Could not remove the account — try again.'))
      setBusy(false)
    }
  }
  const exp = async () => {
    setExporting(true)
    try { await downloadExport(account) } catch (err) { setError(errorText(err, 'The export failed — try again.')) }
    setExporting(false)
  }
  const c = account.counts || {}
  return (
    <ModalPanel width={460} title={`Remove ${account.name}?`} onClose={busy ? undefined : onClose} zIndex={70}
      style={{ padding: '22px 24px 18px', gap: 12 }}>
      <Heading size={19} className="v2-dialogtitle">Remove {account.name}?</Heading>
      <span className="v2-dialogtext" style={{ fontSize: 12.5, lineHeight: '18px', color: 'var(--helper-ink)' }}>
        Everything {account.name} has on this install is deleted. There is no undo.
      </span>
      <Surface radius="row" pad="8px 12px" style={{ display: 'flex', flexDirection: 'column', gap: 2 }} aria-label="What is deleted">
        {COUNT_LABELS.map(([k, one, many]) => (
          <span key={k} style={{ display: 'flex', fontSize: 12, lineHeight: '18px' }}>
            <span style={{ flex: 1 }}>{Number(c[k]) === 1 ? one : many}</span>
            <span style={{ fontFamily: 'var(--mono)' }}>{Number(c[k] || 0).toLocaleString()}</span>
          </span>
        ))}
        <Helper size="xs" style={{ paddingTop: 2 }}>plus their Persona, settings, links and run history</Helper>
      </Surface>
      <Helper style={{ textWrap: 'pretty' }}>
        A full database backup is taken first (kept in <span style={{ fontFamily: 'var(--mono)' }}>backups/</span>).{' '}
        <Link onClick={exporting ? undefined : exp}>{exporting ? 'Exporting…' : `Export ${account.name}’s data`}</Link> to keep a copy of your own.
      </Helper>
      <Field label={`Type “${account.name}” to confirm`}>
        <Input value={typed} onChange={setTyped} ariaLabel="Type the account name to confirm" autoComplete="off" width="100%"
          onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); remove() } }} />
      </Field>
      {error && <Helper style={{ color: 'var(--bad)' }}>{error}</Helper>}
      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 4 }}>
        <Button variant="secondary" size="sm" onClick={onClose} disabled={busy}>Cancel</Button>
        <Button variant="danger" size="sm" onClick={remove} disabled={!ok} busy={busy}>Remove {account.name}</Button>
      </div>
    </ModalPanel>
  )
}

// ── API token for scripts (QA F1) ───────────────────────────────────────────
// POST /accounts/{id}/api-token mints a signed token for the caller's own account
// only; scripts send it as `X-Account-Token` with the install key. The dashboard
// itself never sends it (the jn_account cookie picks its account).
export function ApiTokenButton({ account, push, size = 'sm' }) {
  const [token, setToken] = useState(null)
  const [busy, setBusy] = useState(false)
  const mint = async () => {
    setBusy(true)
    try { const r = await api.post(`/accounts/${account.id}/api-token`); setToken(r.data.token) } catch (err) { push({ kind: 'error', msg: errorText(err) }) }
    setBusy(false)
  }
  return (
    <>
      <Pill size={size} onClick={busy ? undefined : mint} disabled={busy} ariaLabel="Create an API token for scripts">{busy ? 'Creating…' : 'API token'}</Pill>
      {token && (
        <PromptDialog title="API token" readOnly mono value={token} label="Done"
          body={`Send it as the X-Account-Token header, with the install key as X-API-Key, to act as ${account.name}. Valid for a year; a PIN change ends it.`}
          onSubmit={() => setToken(null)} onCancel={() => setToken(null)} />
      )}
    </>
  )
}

// ── Edit an account's name and colour (owner: any row; R6-07) ───────────────
export function EditAccountModal({ account, self = false, taken = [], onClose, onSaved }) {
  const [name, setName] = useState(account.name || '')
  const [color, setColor] = useState((account.color || '').toLowerCase())
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const n = name.trim()
  const nameChanged = n !== account.name
  const colorChanged = color !== (account.color || '').toLowerCase()
  const ready = n.length > 0 && (nameChanged || colorChanged) && !busy
  const save = async () => {
    if (!ready) return
    setBusy(true); setError('')
    const body = {}
    if (nameChanged) body.name = n
    if (colorChanged) body.color = color
    try {
      await api.patch(`/accounts/${account.id}`, body)
      onSaved({ ...account, ...body })
    } catch (err) {
      setError(errorText(err, 'Could not save — try again.'))
      setBusy(false)
    }
  }
  const title = self ? 'Your name and colour' : `Edit ${account.name}`
  return (
    <ModalPanel width={440} title={title} onClose={busy ? undefined : onClose} zIndex={70}
      style={{ padding: '22px 24px 18px', gap: 14 }}>
      <Heading size={19} className="v2-dialogtitle">{title}</Heading>
      <Field label="Name">
        <Input value={name} onChange={setName} ariaLabel="Account name" autoFocus width="100%"
          onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); save() } }} />
      </Field>
      <Field label="Colour"><ColorPicker name={n} value={color} onPick={setColor} taken={taken} ariaLabel="Account colour" /></Field>
      {error && <Helper style={{ color: 'var(--bad)' }}>{error}</Helper>}
      <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 2 }}>
        <Button variant="secondary" size="sm" onClick={onClose} disabled={busy}>Cancel</Button>
        <Button size="sm" onClick={save} disabled={!ready} busy={busy}>Save</Button>
      </div>
    </ModalPanel>
  )
}

// ── owner: the Accounts section ─────────────────────────────────────────────
// QA F2 / D2: always shown to the owner, first in Settings — one account = the
// owner's own row + "+ Add account"; more = every account.
//
// Member policies: with `onPolicyFor` (owner, two or more accounts) each member row
// gets an "Edit policies" switch. On for a member = every policy section head shows
// that member's Inherit / Own control (Settings renders them from `policyFor`); off
// only hides them, it changes no policy. One member at a time, never persisted.
// `policies` + `idx` give the row's count of sections off their default policy.
export function nonDefaultPolicies(memberId, policies, idx) {
  const mine = policies?.[memberId] || {}
  return Object.values(idx?.byId || {}).filter((s) => s.scope === 'policy' && mine[s.id] && mine[s.id] !== s.default).length
}

export function AccountsTable({ data, me, onAdd, onChanged, push, policyFor = '', onPolicyFor, policies, idx }) {
  const accounts = data?.accounts?.length ? data.accounts : (me ? [{ ...me, is_self: true }] : [])
  const max = data?.max_accounts || 5
  const [pinFor, setPinFor] = useState(null)      // { account, mode: 'set' | 'remove' } whose PIN dialog is up
  const [removing, setRemoving] = useState(null)
  const [editing, setEditing] = useState(null)
  const selfRow = accounts.find((a) => a.id === me?.id)
  const others = accounts.length > 1

  const pinDialog = pinFor && (() => {
    const acc = pinFor.account
    const self = acc.id === me.id
    const done = (msg) => { setPinFor(null); push({ kind: 'success', msg }); onChanged() }
    if (self && pinFor.mode === 'remove') {
      return (
        <PinDialog title="Remove your PIN" mode="remove" needCurrent
          intro={others ? 'Without your PIN anyone at a browser opens your account with one click, and a member’s Unlock of the shared settings needs no PIN.' : undefined}
          onSubmit={async (b) => { await api.patch(`/accounts/${acc.id}`, b); done('PIN removed') }}
          onClose={() => setPinFor(null)} />
      )
    }
    return (
      <PinDialog title={self ? (acc.has_pin ? 'Change your PIN' : 'Set your PIN') : `Reset ${acc.name}’s PIN`}
        who={self ? null : acc.name} mode="set" needCurrent={self && acc.has_pin}
        intro={self ? undefined : `${acc.name} uses the new PIN from now on; your own PIN keeps opening their account.`}
        onSubmit={async (b) => { await api.patch(`/accounts/${acc.id}`, b); done(self ? (acc.has_pin ? 'PIN changed' : 'PIN set') : `${acc.name}’s PIN reset`) }}
        onRemove={acc.has_pin ? (self ? () => setPinFor({ account: acc, mode: 'remove' }) : async () => {
          try { await api.patch(`/accounts/${acc.id}`, { pin: null }); done(`${acc.name} opens without a PIN now`) } catch (err) { push({ kind: 'error', msg: errorText(err) }) }
        }) : undefined}
        onClose={() => setPinFor(null)} />
    )
  })()

  return (
    <div style={{ display: 'flex', flexDirection: 'column' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '6px 0 10px' }}>
        <Helper style={{ flex: 1, minWidth: 0, textWrap: 'pretty' }}>
          {others
            ? `Everyone on this install has their own jobs, résumés, applications and settings; scraping is shared. ${accounts.length} of ${max} accounts${accounts.length >= max ? ' — remove one to add another' : ''}.`
            : `Just you. Add people to this install — each gets their own jobs, résumés, applications and settings; scraping stays shared. Up to ${max} accounts.`}
          {others && selfRow && !selfRow.has_pin && ' Your account has no PIN, so the shared settings are only a privacy latch.'}
        </Helper>
        <Button size="sm" onClick={onAdd} disabled={accounts.length >= max}
          title={accounts.length >= max ? `At most ${max} accounts` : undefined}
          ariaLabel={accounts.length >= max ? `Add account — the limit is ${max} accounts` : undefined}>+ Add account</Button>
      </div>
      <div role="table" aria-label="Accounts" style={{ display: 'flex', flexDirection: 'column' }}>
        {accounts.map((a) => {
          const self = a.id === me.id
          const owner = a.role === 'owner'
          const offDefault = onPolicyFor && !owner ? nonDefaultPolicies(a.id, policies, idx) : 0
          return (
            <div role="row" key={a.id} data-account-row={a.name}
              style={{ display: 'flex', alignItems: 'center', gap: 12, minHeight: 52, padding: '8px 0', borderBottom: '1px solid var(--line-soft)' }}>
              <Avatar name={a.name} color={a.color} size={26} />
              <div role="cell" style={{ flex: '1 1 0', minWidth: 0, display: 'flex', flexDirection: 'column', gap: 1 }}>
                <span style={{ display: 'flex', alignItems: 'center', gap: 7, fontSize: 13, lineHeight: '18px', fontWeight: 500, minWidth: 0 }}>
                  <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{a.name}</span>
                  {self && <Helper size="xs">you</Helper>}
                </span>
                {/* T7-05: the full line on hover; Win98's wider type gets two lines (theme.css v2-acctstats) */}
                <Helper size="xs" title={countsLine(a.counts)} className="v2-acctstats" style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{countsLine(a.counts)}</Helper>
              </div>
              <span role="cell" style={{ flex: '0 0 74px' }}><Tag tone={owner ? 'accent' : 'neutral'}>{owner ? 'Owner' : 'Member'}</Tag></span>
              <span role="cell" style={{ flex: '0 0 96px' }}><Helper title={a.last_active_at || ''}>{self ? 'now' : ago(a.last_active_at)}</Helper></span>
              {onPolicyFor && (
                <span role="cell" style={{ flex: '0 0 216px', display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
                  {!owner && (
                    <Switch size="sm" on={policyFor === a.id} label="Edit policies" ariaLabel={`Edit ${a.name}’s policies`}
                      onChange={(on) => onPolicyFor(on ? a.id : '')} />
                  )}
                  {offDefault > 0 && (
                    <Helper size="xs" title={`Sections where ${a.name}’s Inherit / Own differs from the default`}
                      style={{ whiteSpace: 'nowrap' }}>{offDefault} {offDefault === 1 ? 'policy' : 'policies'} off default</Helper>
                  )}
                </span>
              )}
              {/* fixed width so the role and last-active columns line up whatever buttons a row has */}
              <span role="cell" style={{ flex: '0 0 240px', display: 'flex', flexWrap: 'wrap', gap: 6, justifyContent: 'flex-end' }}>
                <Pill size="sm" onClick={() => setEditing(a)} ariaLabel={self ? 'Rename or recolour your account' : `Rename or recolour ${a.name}`}>Edit</Pill>
                <Pill size="sm" onClick={() => setPinFor({ account: a, mode: 'set' })}
                  ariaLabel={self ? (a.has_pin ? 'Change your PIN' : 'Set your PIN') : (a.has_pin ? `Reset ${a.name}’s PIN` : `Set ${a.name}’s PIN`)}>
                  {self ? (a.has_pin ? 'Change PIN' : 'Set PIN') : (a.has_pin ? 'Reset PIN' : 'Set PIN')}
                </Pill>
                {self && <ApiTokenButton account={a} push={push} />}
                {!owner && !self && (
                  <Pill size="sm" hover="v2-hover-bad-bdc" onClick={() => setRemoving(a)} ariaLabel={`Remove ${a.name}`}>Remove</Pill>
                )}
              </span>
            </div>
          )
        })}
      </div>
      {pinDialog}
      {editing && (
        <EditAccountModal account={editing} self={editing.id === me?.id}
          taken={accounts.filter((x) => x.id !== editing.id).map((x) => (x.color || '').toLowerCase())}
          onClose={() => setEditing(null)}
          onSaved={(acc) => { setEditing(null); push({ kind: 'success', msg: `Saved ${acc.name}` }); onChanged() }} />
      )}
      {removing && (
        <RemoveAccountModal account={removing} onClose={() => setRemoving(null)}
          onRemoved={(acc, res) => {
            setRemoving(null)
            push({ kind: 'success', msg: `Removed ${acc.name}${res?.backup ? ` · backup ${res.backup}` : ''}` })
            onChanged()
          }} />
      )}
    </div>
  )
}

// ── member: Your account ────────────────────────────────────────────────────
export function YourAccount({ data, me, onChanged, push }) {
  const accounts = data?.accounts || []
  const mine = accounts.find((a) => a.id === me.id) || me
  const [name, setName] = useState(mine.name || '')
  const [pinMode, setPinMode] = useState(null)   // 'set' | 'remove'
  const [exporting, setExporting] = useState(false)
  useEffect(() => { setName(mine.name || '') }, [mine.name])
  const others = accounts.filter((a) => a.id !== me.id)
  const owner = accounts.find((a) => a.role === 'owner')

  const patch = async (body, msg) => {
    try { await api.patch(`/accounts/${me.id}`, body); push({ kind: 'success', msg }); onChanged(); return true } catch (err) { push({ kind: 'error', msg: errorText(err) }); return false }
  }
  const rename = () => {
    const n = name.trim()
    if (!n || n === mine.name) { setName(mine.name || ''); return }
    patch({ name: n }, `Renamed to ${n}`).then((ok) => { if (!ok) setName(mine.name || '') })
  }
  const exp = async () => {
    setExporting(true)
    try { await downloadExport(mine); push({ kind: 'success', msg: 'Export downloaded' }) } catch (err) { push({ kind: 'error', msg: errorText(err, 'The export failed — try again.') }) }
    setExporting(false)
  }
  const row = (label, help, right) => (
    <div style={{ display: 'flex', alignItems: 'center', gap: 24, minHeight: 52, padding: '9px 0', borderBottom: '1px solid var(--line-soft)' }}>
      <div style={{ flex: '0 1 356px', minWidth: 210, display: 'flex', flexDirection: 'column', gap: 2 }}>
        <span style={{ fontSize: 13, lineHeight: '18px', fontWeight: 500 }}>{label}</span>
        <Helper style={{ textWrap: 'pretty' }}>{help}</Helper>
      </div>
      <div style={{ flex: 1, minWidth: 0, display: 'flex', alignItems: 'center', gap: 8 }}>{right}</div>
    </div>
  )
  return (
    <div style={{ display: 'flex', flexDirection: 'column' }}>
      {row('Name', 'How you appear in the picker and the rail.',
        <Input value={name} onChange={setName} onBlur={rename} ariaLabel="Your account name" width="260px"
          onKeyDown={(e) => { if (e.key === 'Enter') e.currentTarget.blur() }} />)}
      {row('Colour', 'Your avatar colour — the standing cue for which account is open.',
        <ColorPicker name={mine.name} value={mine.color} onPick={(c) => c !== mine.color && patch({ color: c }, 'Colour saved')}
          taken={others.map((a) => (a.color || '').toLowerCase())} ariaLabel="Your colour" />)}
      {row('PIN', mine.has_pin ? 'Asked when someone opens your account. The owner’s PIN opens it too.' : 'Off — anyone at this browser can open your account with one click.',
        <>
          <Pill size="sm" onClick={() => setPinMode('set')}>{mine.has_pin ? 'Change PIN' : 'Set PIN'}</Pill>
          {mine.has_pin && <Pill size="sm" onClick={() => setPinMode('remove')}>Remove PIN</Pill>}
        </>)}
      {row('Export my data', 'A zip of your jobs, applications, résumés, letters, searches, companies, Persona and settings.',
        <Pill size="sm" onClick={exporting ? undefined : exp} disabled={exporting}>{exporting ? 'Exporting…' : 'Download zip'}</Pill>)}
      {row('API token', 'For scripts and API clients: send it as X-Account-Token with the install key to act as you. A PIN change ends it.',
        <ApiTokenButton account={mine} push={push} />)}
      <Helper style={{ padding: '12px 0 0', textWrap: 'pretty' }}>
        Other accounts: {others.map((a) => (a.role === 'owner' ? `${a.name} (owner)` : a.name)).join(', ') || 'none'}.
        {' '}Adding or removing people is {owner ? `${owner.name}’s` : 'the owner’s'} job — ask them.
      </Helper>
      {pinMode && (
        <PinDialog title={pinMode === 'remove' ? 'Remove your PIN' : (mine.has_pin ? 'Change your PIN' : 'Set your PIN')}
          mode={pinMode} needCurrent={!!mine.has_pin}
          onSubmit={async (b) => {
            await api.patch(`/accounts/${me.id}`, b)
            setPinMode(null)
            push({ kind: 'success', msg: pinMode === 'remove' ? 'PIN removed' : 'PIN saved' })
            onChanged()
          }}
          onClose={() => setPinMode(null)} />
      )}
    </div>
  )
}

// ════════════════════════════════════════════════════════════════════════════
// MT-14 · Settings by role
// ════════════════════════════════════════════════════════════════════════════

// The AI sections (the member's "AI · yours" nav group).
export const AI_SECTIONS = ['models', 'scoring', 'tailoring', 'letters', 'autofill', 'prep', 'emailclass']

// The setting key a row stands for (its lock follows that key's section), or a
// `lockKey` the row names; `ownerOnly` rows call owner-only endpoints.
export function rowKey(r) {
  if (!r) return null
  if (r.lockKey) return r.lockKey
  if (r.key) return r.key
  if (r.pKey) return r.pKey
  if (r.base) return `${r.base}_provider`
  if (r.kind === 'models') return 'llm_models_list'
  if (r.kind === 'apikey') return 'dashboard_api_key'
  return null
}

// Map GET /settings/sections into { byId, keySection }.
export function indexSections(list) {
  const byId = {}
  const keySection = {}
  for (const s of Array.isArray(list) ? list : []) {
    byId[s.id] = s
    for (const k of s.keys || []) keySection[k] = s.id
  }
  return { byId, keySection }
}

const OWNS = ['own', 'own_always']

// Can a member edit `key`? `_meta` (from GET /settings) first, else the section's mode.
export function memberCanEdit(key, meta, idx) {
  if (!key) return true
  const m = meta && meta[key]
  if (m && typeof m.editable === 'boolean') return m.editable
  const sid = idx?.keySection?.[key] || 'system'
  const sec = idx?.byId?.[sid]
  return !!sec && OWNS.includes(sec.mode)
}

// Is this row locked for the viewer? Only members, only while not unlocked.
export function rowLocked(r, { member, unlocked, meta, idx }) {
  if (!member || unlocked) return false
  if (r.ownerOnly) return true
  if (r.kind === 'appearance' || r.kind === 'theme' || r.href) return false
  return !memberCanEdit(rowKey(r), meta, idx)
}

// The scope tag beside a section title.
export function scopeTag(id, { idx, member, ownerName }) {
  if (id === 'appearance') return { label: 'Per device', tone: 'neutral' }
  const sec = idx?.byId?.[id]
  if (!sec) return null
  if (sec.scope === 'owner_only') return { label: 'Owner only', tone: 'good' }
  // a member's own section says so; the owner needs no badge there (no toggle, nothing shared)
  if (sec.scope === 'own_always') return member ? { label: 'Yours', tone: 'accent' } : null
  if (!member) return null   // the owner gets the Inherit / Own control instead
  return sec.mode === 'own' ? { label: 'Yours', tone: 'accent' } : { label: `Inherited from ${ownerName || 'the owner'}`, tone: 'neutral' }
}

export function ScopeTag({ tag }) {
  if (!tag) return null
  return <Tag tone={tag.tone}>{tag.label}</Tag>
}

// Owner: "<member>'s policy: Inherit | Own" for one policy section (T7-06: it sets the
// member's policy, never the owner's own state).
export function PolicyToggle({ memberName, mode, onChange, sectionTitle }) {
  // height 26 = the section head's line: the inset control (28-30px) centres in it and
  // overflows into the head's padding instead of growing the head by 2-4px
  return (
    <span data-policy-for={memberName} style={{ display: 'flex', alignItems: 'center', gap: 7, marginLeft: 'auto', flex: '0 0 auto', height: 26 }}>
      <Helper size="xs">{memberName}’s policy:</Helper>
      <Segmented variant="inset" grow={false} value={mode} onChange={onChange}
        ariaLabel={`${sectionTitle} for ${memberName}`}
        options={[{ value: 'inherit', label: 'Inherit', hint: `${memberName} sees your values, read-only` },
          { value: 'own', label: 'Own', hint: `${memberName} keeps their own values` }]} />
    </span>
  )
}

// POST /api/auth/verify-owner-pin; resolves with the unlock's end (ms), rejects with
// a message (and `retryAfter` on a cooldown).
export async function verifyOwnerPin(pin) {
  try {
    const { data } = await api.post('/auth/verify-owner-pin', { pin })
    const t = Date.parse(data?.unlocked_until || '')
    return Number.isFinite(t) ? t : Date.now() + 15 * 60000
  } catch (err) {
    const f = authFailure(err)
    const e = new Error(errorText(err, 'Could not check the PIN — try again.'))
    if (f?.kind === 'pin_cooldown') e.retryAfter = f.retryAfter
    throw e
  }
}

export const hhmm = (ms) => new Date(ms).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })

// The owner-PIN pad used by the lock card and the header pill's dialog.
export function OwnerPinPad({ ownerName, onUnlocked, autoFocus = false }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [cooldown, setCooldown] = useState(0)
  const [attempt, setAttempt] = useState(0)
  const submit = async (pin) => {
    setBusy(true); setError('')
    try {
      const until = await verifyOwnerPin(pin)
      setBusy(false)
      onUnlocked(until)
    } catch (e) {
      setBusy(false); setError(e.retryAfter ? '' : e.message); setCooldown(e.retryAfter || 0); setAttempt((n) => n + 1)
    }
  }
  return (
    <PinPad onComplete={submit} busy={busy} error={error} cooldown={cooldown} onCooldownEnd={() => setCooldown(0)}
      resetKey={attempt} autoFocus={autoFocus} hint={`${ownerName}’s PIN`} ariaLabel={`${ownerName}’s PIN`} />
  )
}

// QA F2 / D3: when the owner has no PIN the unlock is a plain button (POST
// /auth/verify-owner-pin with no PIN); the owner sections are then only a privacy latch.
export function PlainUnlockButton({ ownerName, onUnlocked }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const go = async () => {
    setBusy(true); setError('')
    try { const until = await verifyOwnerPin(); setBusy(false); onUnlocked(until) } catch (e) { setBusy(false); setError(e.message) }
  }
  return (
    <span style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4, flex: '0 0 auto' }}>
      <Button size="sm" onClick={go} busy={busy} ariaLabel={`Unlock the shared settings as ${ownerName}`}>Unlock</Button>
      {error && <Helper size="xs" style={{ color: 'var(--bad)' }}>{error}</Helper>}
    </span>
  )
}

export const NO_OWNER_PIN_NOTE = 'Without an owner PIN this is only a privacy latch.'

// After the last row of a section a member cannot edit.
export function LockCard({ ownerName, inherited, sectionTitle, onUnlocked, ownerHasPin = true }) {
  if (!ownerHasPin) {
    return (
      <Surface radius="card" pad="12px 14px" data-lock-card={sectionTitle}
        style={{ display: 'flex', alignItems: 'center', gap: 18, marginTop: 12 }}>
        <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 3 }}>
          <span style={{ fontSize: 12.5, lineHeight: '18px', fontWeight: 500 }}>
            {inherited ? `Inherited from ${ownerName}` : `Shared by everyone on this install`}
          </span>
          <Helper style={{ textWrap: 'pretty' }}>
            {inherited ? `You see ${ownerName}’s values. ` : ''}Unlock to change the shared values as {ownerName} for 15 minutes. {NO_OWNER_PIN_NOTE}
          </Helper>
        </div>
        <PlainUnlockButton ownerName={ownerName} onUnlocked={onUnlocked} />
      </Surface>
    )
  }
  return (
    <Surface radius="card" pad="12px 14px" data-lock-card={sectionTitle}
      style={{ display: 'flex', alignItems: 'center', gap: 18, marginTop: 12 }}>
      <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 3 }}>
        <span style={{ fontSize: 12.5, lineHeight: '18px', fontWeight: 500 }}>
          {inherited ? `Inherited from ${ownerName}` : `Shared by everyone on this install`}
        </span>
        <Helper style={{ textWrap: 'pretty' }}>
          {inherited
            ? `You see ${ownerName}’s values. ${ownerName} can give you your own here, or enter their PIN to change the shared values for 15 minutes.`
            : `${ownerName} can change it. Ask them, or have them enter their PIN to unlock for 15 minutes.`}
        </Helper>
      </div>
      <OwnerPinPad ownerName={ownerName} onUnlocked={onUnlocked} />
    </Surface>
  )
}

// Header pill: "🔒 Owner sections locked · Unlock as X" ↔ "Unlocked as X · until HH:MM · Lock".
export function UnlockPill({ ownerName, unlockedUntil, onUnlocked, onLock, ownerHasPin = true }) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  if (unlockedUntil) {
    return (
      <span style={{ display: 'flex', alignItems: 'center', gap: 8, flex: '0 0 auto' }}>
        <Tag tone="warn">Unlocked as {ownerName} · until {hhmm(unlockedUntil)}</Tag>
        <Pill size="sm" disabled={busy} ariaLabel="Lock the owner sections again"
          onClick={async () => { setBusy(true); try { await onLock() } finally { setBusy(false) } }}>Lock</Pill>
      </span>
    )
  }
  return (
    <>
      <Pill size="sm" onClick={() => setOpen(true)} ariaLabel={`Owner sections locked — unlock as ${ownerName}`}>
        Owner sections locked · Unlock as {ownerName}
      </Pill>
      {open && (
        <ModalPanel width={380} title={`Unlock as ${ownerName}`} onClose={() => setOpen(false)} zIndex={75}
          style={{ padding: '22px 24px 18px', gap: 12 }}>
          <Heading size={19} className="v2-dialogtitle">Unlock as {ownerName}</Heading>
          <Helper style={{ textWrap: 'pretty' }}>
            {ownerHasPin
              ? `${ownerName} enters their PIN; for 15 minutes this browser can change the shared settings as ${ownerName}.`
              : `For 15 minutes this browser can change the shared settings as ${ownerName}. ${NO_OWNER_PIN_NOTE}`}
          </Helper>
          <div style={{ display: 'flex', justifyContent: 'center', padding: '4px 0' }}>
            {ownerHasPin
              ? <OwnerPinPad ownerName={ownerName} autoFocus onUnlocked={(t) => { setOpen(false); onUnlocked(t) }} />
              : <PlainUnlockButton ownerName={ownerName} onUnlocked={(t) => { setOpen(false); onUnlocked(t) }} />}
          </div>
          <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
            <Button variant="secondary" size="sm" onClick={() => setOpen(false)}>Cancel</Button>
          </div>
        </ModalPanel>
      )}
    </>
  )
}
