import React, { useCallback, useEffect, useState } from 'react'
import api, { authFailure } from './api'
import { Avatar, Card, Heading, Helper, ModalPanel, NavLink, PinPad, Spinner } from './ui'
import { useTheme, themeAttrs } from './theme'
import { finishSwitch, roleLabel, switchAccount } from './accounts'
import './theme.css'

// The account picker (MT-12). Same overlay shell as the sign-in (LoginModal):
// mounted by App.jsx outside the app shell, so it carries the look itself.
//
// Two ways in:
// * `required` — the API answered 401 `account_required` (an install with several
//   accounts and no account chosen in this browser). Grid of accounts, no way to
//   dismiss: every screen behind it needs an account.
// * `account` given — the rail's switcher picked an account that has a PIN; the
//   overlay opens straight on its PIN pad and Escape / the scrim cancel.
//
// Picking: a tile, or 1–5 on the keyboard. An account without a PIN switches at
// once; one with a PIN asks for it (the owner's PIN also opens a member's). A
// successful switch reloads the current screen (accounts.js `finishSwitch`).
export default function AccountPicker({ account: initial = null, required = false, onClose }) {
  const look = useTheme()
  const [accounts, setAccounts] = useState(null)   // null = loading
  const [loadErr, setLoadErr] = useState('')
  const [target, setTarget] = useState(initial)    // the account whose PIN pad is up
  const [busyId, setBusyId] = useState(null)       // a no-PIN switch in flight
  const [pinBusy, setPinBusy] = useState(false)
  const [error, setError] = useState('')
  const [cooldown, setCooldown] = useState(0)
  const [attempt, setAttempt] = useState(0)
  const [done, setDone] = useState(null)

  const dismissable = !required && !!onClose
  const owner = (accounts || []).find((a) => a.role === 'owner')

  const [reloadKey, setReloadKey] = useState(0)
  useEffect(() => {
    let live = true
    api.get('/auth/accounts')
      .then(({ data }) => { if (live) { setAccounts(Array.isArray(data) ? data : []); setLoadErr('') } })
      .catch(() => { if (live) { setAccounts([]); setLoadErr('Could not load the accounts — reload the page to try again.') } })
    return () => { live = false }
  }, [reloadKey])

  const failed = useCallback((err, acc) => {
    const f = authFailure(err)
    if (f?.kind === 'pin_required') { setTarget(acc); setError(''); setCooldown(0); setAttempt((n) => n + 1); return }
    if (f?.kind === 'wrong_pin') {
      setError(f.triesLeft != null ? `Wrong PIN · ${f.triesLeft} ${f.triesLeft === 1 ? 'try' : 'tries'} left` : 'Wrong PIN')
      setCooldown(0); setAttempt((n) => n + 1); return
    }
    if (f?.kind === 'pin_cooldown') { setTarget(acc); setError(''); setCooldown(f.retryAfter); setAttempt((n) => n + 1); return }
    if (err?.response?.status === 404) {
      // R6-04: the account is gone — back to a fresh grid
      setTarget(null); setReloadKey((n) => n + 1)
      setError('That account no longer exists.')
    } else setError('Could not switch — try again.')
    setAttempt((n) => n + 1)
  }, [])

  const go = useCallback(async (acc, pin) => {
    try {
      const a = await switchAccount(acc.id, pin)
      setDone(a || acc)
      finishSwitch(a || acc)
    } catch (err) {
      failed(err, acc)
    }
  }, [failed])

  const pick = useCallback((acc) => {
    if (busyId || pinBusy || done) return
    setError(''); setCooldown(0)
    if (acc.has_pin) { setTarget(acc); setAttempt((n) => n + 1); return }
    setBusyId(acc.id)
    go(acc).finally(() => setBusyId(null))
  }, [busyId, pinBusy, done, go])

  const submitPin = (pin) => {
    if (!target || pinBusy) return
    setPinBusy(true); setError('')
    go(target, pin).finally(() => setPinBusy(false))
  }

  // 1–5 picks from the grid (not while a PIN pad has the keyboard)
  useEffect(() => {
    if (target || !accounts?.length) return undefined
    const onKey = (e) => {
      if (e.ctrlKey || e.metaKey || e.altKey || e.shiftKey) return
      const n = Number(e.key)
      if (!Number.isInteger(n) || n < 1 || n > Math.min(5, accounts.length)) return
      e.preventDefault(); e.stopPropagation()
      pick(accounts[n - 1])
    }
    document.addEventListener('keydown', onKey, true)
    return () => document.removeEventListener('keydown', onKey, true)
  }, [target, accounts, pick])

  // Escape: from the PIN pad back to the grid (when the grid is where we came
  // from), else close when that is allowed.
  const back = () => { setTarget(null); setError(''); setCooldown(0) }
  const onEscape = initial ? (dismissable ? onClose : undefined) : (target ? back : (dismissable ? onClose : undefined))

  const pinHint = target && target.role !== 'owner' && owner?.has_pin
    ? `Enter ${target.name}'s PIN, or ${owner.name}'s`
    : target ? `Enter ${target.name}'s PIN` : ''

  return (
    <ModalPanel width={target ? 380 : 560} title={target ? 'Enter PIN' : 'Choose an account'} zIndex={9999}
      onClose={onEscape} escapeCapture trapFocus labelledBy={done ? undefined : (target ? 'jn-picker-pin-title' : 'jn-picker-title')}
      scrimProps={{ className: 'jn-v2', ...themeAttrs(look) }}
      style={{ padding: '26px 26px 22px', gap: 18 }}>
      {done ? (
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8, padding: '18px 0' }}>
          <Avatar name={done.name} color={done.color} size={44} />
          <Heading size={19}>Switching to {done.name}…</Heading>
        </div>
      ) : target ? (
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12 }} data-testid="pin-step">
          <Avatar name={target.name} color={target.color} size={44} />
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2 }}>
            <Heading size={18} id="jn-picker-pin-title">{target.name}</Heading>
            <Helper>{roleLabel(target)}</Helper>
          </div>
          <PinPad onComplete={submitPin} busy={pinBusy} error={error} hint={pinHint}
            cooldown={cooldown} resetKey={attempt} onCooldownEnd={() => setCooldown(0)}
            ariaLabel={`${target.name}'s PIN`} style={{ marginTop: 4 }} />
          {(!initial || dismissable) && (
            <NavLink onClick={initial ? onClose : back}>{initial ? 'Cancel' : '‹ Other accounts'}</NavLink>
          )}
        </div>
      ) : (
        <>
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}>
            {/* ui: keep — the picker's wordmark-sized title matches the sign-in overlay's serif 23/28px */}
            <span id="jn-picker-title" style={{ fontFamily: 'var(--serif)', fontSize: 23, fontWeight: 400, letterSpacing: '-.02em', lineHeight: '28px' }}>Who&rsquo;s job hunting?</span>
            <Helper>
              {accounts && accounts.length > 1 ? `Pick an account · 1–${Math.min(5, accounts.length)} on the keyboard` : ' '}
            </Helper>
          </div>
          {accounts === null ? (
            <div style={{ display: 'flex', justifyContent: 'center', padding: '28px 0' }}><Spinner size={16} /></div>
          ) : (
            <div role="list" style={{ display: 'flex', flexWrap: 'wrap', justifyContent: 'center', gap: 14 }}>
              {accounts.map((a, i) => (
                <div role="listitem" key={a.id}>
                  <Card interactive onClick={() => pick(a)} ariaLabel={`${a.name}, ${roleLabel(a)}${a.has_pin ? ', PIN' : ''} (${i + 1})`}
                    style={{ width: 116, padding: '14px 8px 12px', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 7 }}>
                    <Avatar name={a.name} color={a.color} size={44} />
                    <span style={{ maxWidth: '100%', fontSize: 13, lineHeight: '18px', fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{a.name}</span>
                    <span style={{ height: 16, display: 'flex', alignItems: 'center' }}>
                      {busyId === a.id
                        ? <Spinner size={11} />
                        : <Helper size="xs">{roleLabel(a)}{a.has_pin ? ' · PIN' : ''}</Helper>}
                    </span>
                  </Card>
                </div>
              ))}
            </div>
          )}
          {(error || loadErr) && <Helper style={{ color: 'var(--bad)', textAlign: 'center' }}>{error || loadErr}</Helper>}
          {dismissable && <div style={{ display: 'flex', justifyContent: 'center' }}><NavLink onClick={onClose}>Cancel</NavLink></div>}
        </>
      )}
    </ModalPanel>
  )
}
