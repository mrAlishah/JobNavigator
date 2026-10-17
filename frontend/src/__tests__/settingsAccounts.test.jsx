// MT-13 · Settings › Accounts / Your account building blocks: the counts line, the
// MT-11 error codes in words, the PIN dialog's steps, the Add account dialog (owner
// PIN first, then the body it posts) and the typed-name Remove confirm.
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { render, fireEvent, act, cleanup, screen } from '@testing-library/react'

vi.mock('../api', () => {
  const api = { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() }
  return { default: api, authFailure: (e) => {
    const r = e?.response
    if (!r) return null
    if (r.status === 401 && r.data?.detail === 'wrong_pin') return { kind: 'wrong_pin', triesLeft: r.data.tries_left ?? null }
    if (r.status === 401 && r.data?.detail === 'pin_required') return { kind: 'pin_required' }
    return null
  } }
})

import api from '../api'
import {
  countsLine, errorText, PinDialog, AddAccountModal, RemoveAccountModal, ColorPicker, COPY_ITEMS,
  indexSections, rowKey, memberCanEdit, rowLocked, memberGroup, scopeTag, LockCard, UnlockPill,
  AccountsTable, EditAccountModal, PolicyToggle,
} from '../screens/SettingsAccounts'

const typePin = (pin, label) => {
  const cells = screen.getAllByLabelText(new RegExp(`${label} digit`))
  pin.split('').forEach((d, i) => fireEvent.change(cells[i], { target: { value: d } }))
}
const flush = () => act(() => Promise.resolve())

beforeEach(() => { vi.clearAllMocks() })
afterEach(() => cleanup())

describe('countsLine / errorText', () => {
  it('lists non-zero counts with singular/plural', () => {
    expect(countsLine({ jobs: 1204, applications: 1, resumes: 0 })).toBe('1,204 jobs · 1 application')
    expect(countsLine({})).toBe('no data yet')
  })
  it('words the MT-11 codes and PIN answers', () => {
    const e = (status, data) => ({ response: { status, data } })
    expect(errorText(e(409, { detail: 'account_limit', message: 'x' }))).toMatch(/5 accounts/)
    expect(errorText(e(503, { detail: 'backup_failed' }))).toMatch(/backup failed/)
    expect(errorText(e(400, { detail: 'confirm_name_mismatch' }))).toMatch(/exactly/)
    expect(errorText(e(409, { detail: 'account_busy' }))).toMatch(/background work/)
    expect(errorText(e(401, { detail: 'wrong_pin', tries_left: 3 }))).toBe('Wrong PIN · 3 tries left')
    expect(errorText(e(422, { detail: 'name is required' }))).toBe('name is required')
    expect(errorText(new Error('net'), 'fallback')).toBe('fallback')
  })
  it('QA F7 / R6-08: no raw code reaches a Settings flash', () => {
    const e = (status, data) => ({ response: { status, data } })
    for (const code of ['owner_only', 'own_account_only', 'no_account', 'gmail_not_configured', 'scheduled_scrape_running',
      'account_changed', 'invalid_account_token', 'account_required', 'No account to link']) {
      const t = errorText(e(403, { detail: code }))
      expect(t).not.toBe(code)
      expect(t).toMatch(/[A-Z].* .*[.—]/)
    }
  })
})

describe('ColorPicker', () => {
  it('offers five swatches and reports the pick', () => {
    const onPick = vi.fn()
    render(<ColorPicker name="Alex" value="#2f6fed" onPick={onPick} taken={['#d9534f']} />)
    const radios = screen.getAllByRole('radio')
    expect(radios).toHaveLength(5)
    expect(radios[0].getAttribute('aria-checked')).toBe('true')
    expect(radios[1].getAttribute('aria-label')).toMatch(/in use/)
    fireEvent.click(radios[2])
    expect(onPick).toHaveBeenCalledWith('#2e9e6b')
  })
})

describe('PinDialog', () => {
  it('asks current → new → confirm and submits both', async () => {
    const onSubmit = vi.fn().mockResolvedValue()
    render(<PinDialog title="Change your PIN" mode="set" needCurrent onSubmit={onSubmit} onClose={() => {}} />)
    typePin('1111', 'Current PIN')
    typePin('2222', 'New PIN')
    typePin('2222', 'Same PIN again')
    await flush()
    expect(onSubmit).toHaveBeenCalledWith({ pin: '2222', current_pin: '1111' })
  })
  it('a mismatched confirm goes back to the new PIN', async () => {
    const onSubmit = vi.fn()
    render(<PinDialog title="Set your PIN" mode="set" onSubmit={onSubmit} onClose={() => {}} />)
    typePin('1234', 'New PIN')
    typePin('9999', 'Same PIN again')
    await flush()
    expect(onSubmit).not.toHaveBeenCalled()
    expect(screen.getByText(/two PINs differ/)).toBeTruthy()
    expect(screen.getAllByLabelText(/New PIN digit/)).toHaveLength(4)
  })
  it('remove mode sends pin null after the current PIN', async () => {
    const onSubmit = vi.fn().mockResolvedValue()
    render(<PinDialog title="Remove your PIN" mode="remove" needCurrent onSubmit={onSubmit} onClose={() => {}} />)
    typePin('1111', 'Current PIN')
    await flush()
    expect(onSubmit).toHaveBeenCalledWith({ pin: null, current_pin: '1111' })
  })
})

describe('AddAccountModal', () => {
  const owner = { id: 'o1', name: 'Viktor', color: '#2f6fed', role: 'owner', has_pin: true }
  it('an owner without a PIN goes straight to the form and adds a PIN-less member (D3)', async () => {
    api.post.mockResolvedValue({ data: { account: { id: 'm1', name: 'Alex' }, copied: {} } })
    const onCreated = vi.fn()
    render(<AddAccountModal accounts={[{ ...owner, has_pin: false }]} onClose={() => {}} onCreated={onCreated} />)
    expect(screen.queryByText('Set your PIN first')).toBeNull()
    fireEvent.change(screen.getByLabelText('Account name'), { target: { value: 'Alex' } })
    fireEvent.click(screen.getByText('Add account', { selector: '[role="button"], button' }))
    await flush()
    expect(api.patch).not.toHaveBeenCalled()
    expect(api.post).toHaveBeenCalledWith('/accounts', { name: 'Alex', color: '#d9534f' })
    expect(onCreated).toHaveBeenCalled()
  })
  it('posts name, first free colour, PIN and the copy-from items', async () => {
    api.post.mockResolvedValue({ data: { account: { id: 'm1', name: 'Alex' }, copied: {} } })
    const onCreated = vi.fn()
    const { container } = render(<AddAccountModal accounts={[owner]} me={owner} onClose={() => {}} onCreated={onCreated} />)
    fireEvent.change(screen.getByLabelText('Account name'), { target: { value: ' Alex ' } })
    fireEvent.change(screen.getByLabelText('PIN for the new account'), { target: { value: '12a34' } })
    expect(screen.queryByRole('group', { name: 'Items to copy' })).toBeNull()
    // pick the owner as the copy source through the Select's trigger
    fireEvent.click(screen.getByLabelText('Copy from account'))
    fireEvent.click(screen.getByText('Viktor (owner)'))
    const group = screen.getByRole('group', { name: 'Items to copy' })
    expect(group.querySelectorAll('[role="checkbox"]')).toHaveLength(COPY_ITEMS.length)
    fireEvent.click(screen.getByRole('checkbox', { name: 'Companies' }))      // untick one
    fireEvent.click(screen.getByText('Add account', { selector: '[role="button"], button' }))
    await flush()
    expect(api.post).toHaveBeenCalledWith('/accounts', {
      name: 'Alex', color: '#d9534f', pin: '1234',
      copy_from: { account_id: 'o1', items: ['persona', 'resumes', 'searches', 'settings', 'voices', 'excludes'] },
    })
    expect(onCreated).toHaveBeenCalled()
    expect(container).toBeTruthy()
  })
  it('shows the API message for a refused add', async () => {
    api.post.mockRejectedValue({ response: { status: 409, data: { detail: 'name_taken', message: 'x' } } })
    render(<AddAccountModal accounts={[owner]} me={owner} onClose={() => {}} onCreated={() => {}} />)
    fireEvent.change(screen.getByLabelText('Account name'), { target: { value: 'Viktor' } })
    fireEvent.click(screen.getByText('Add account', { selector: '[role="button"], button' }))
    await flush()
    expect(screen.getByText(/already has that name/)).toBeTruthy()
  })
})

describe('RemoveAccountModal', () => {
  const acc = { id: 'm1', name: 'Alex', counts: { jobs: 3, applications: 1 } }
  it('needs the exact name, then deletes with it', async () => {
    api.delete.mockResolvedValue({ data: { deleted: {}, backup: 'pre_remove_x.sql' } })
    const onRemoved = vi.fn()
    render(<RemoveAccountModal account={acc} onClose={() => {}} onRemoved={onRemoved} />)
    const btn = screen.getByText('Remove Alex', { selector: '[role="button"], button' })
    expect(btn.getAttribute('aria-disabled')).toBe('true')
    fireEvent.change(screen.getByLabelText('Type the account name to confirm'), { target: { value: 'alex' } })
    expect(btn.getAttribute('aria-disabled')).toBe('true')
    fireEvent.change(screen.getByLabelText('Type the account name to confirm'), { target: { value: 'Alex' } })
    fireEvent.click(btn)
    await flush()
    expect(api.delete).toHaveBeenCalledWith('/accounts/m1', { data: { confirm_name: 'Alex' } })
    expect(onRemoved).toHaveBeenCalled()
  })
  it('itemises the counts and words a failed backup', async () => {
    api.delete.mockRejectedValue({ response: { status: 503, data: { detail: 'backup_failed' } } })
    render(<RemoveAccountModal account={acc} onClose={() => {}} onRemoved={() => {}} />)
    expect(screen.getByText('jobs')).toBeTruthy()
    expect(screen.getByText('application')).toBeTruthy()
    fireEvent.change(screen.getByLabelText('Type the account name to confirm'), { target: { value: 'Alex' } })
    fireEvent.click(screen.getByText('Remove Alex', { selector: '[role="button"], button' }))
    await flush()
    expect(screen.getByText(/nothing was removed/)).toBeTruthy()
  })
})

// ── MT-14 · settings by role ────────────────────────────────────────────────
describe('role helpers', () => {
  const idx = indexSections([
    { id: 'models', scope: 'policy', default: 'inherit', mode: 'inherit', keys: ['llm_provider', 'scoring_llm_provider', 'llm_models_list'] },
    { id: 'exclude', scope: 'policy', default: 'own', mode: 'own', keys: ['title_exclude_global'] },
    { id: 'advanced', scope: 'owner_only', default: 'inherit', mode: 'owner_only', keys: ['proxy_url', 'dashboard_api_key'] },
    { id: 'notifications', scope: 'own_always', default: 'own', mode: 'own_always', keys: ['telegram_chat_id'] },
    { id: 'scheduler', scope: 'policy', default: 'inherit', mode: 'own', keys: ['reject_cron'] },
  ])
  it('rowKey follows the row kind', () => {
    expect(rowKey({ key: 'a' })).toBe('a')
    expect(rowKey({ kind: 'pair', pKey: 'llm_provider' })).toBe('llm_provider')
    expect(rowKey({ kind: 'llm', base: 'scoring_llm' })).toBe('scoring_llm_provider')
    expect(rowKey({ kind: 'models' })).toBe('llm_models_list')
    expect(rowKey({ kind: 'apikey' })).toBe('dashboard_api_key')
    expect(rowKey({ kind: 'button', lockKey: 'telegram_chat_id' })).toBe('telegram_chat_id')
  })
  it('a member edits own sections; _meta wins over the section table', () => {
    expect(memberCanEdit('title_exclude_global', null, idx)).toBe(true)
    expect(memberCanEdit('telegram_chat_id', null, idx)).toBe(true)
    expect(memberCanEdit('proxy_url', null, idx)).toBe(false)
    expect(memberCanEdit('llm_provider', null, idx)).toBe(false)
    expect(memberCanEdit('llm_provider', { llm_provider: { editable: true } }, idx)).toBe(true)
    expect(memberCanEdit('unknown_key', null, idx)).toBe(false)   // → system, owner only
  })
  it('rows lock only for a member, only while not unlocked', () => {
    const ctx = { member: true, unlocked: false, meta: null, idx }
    expect(rowLocked({ key: 'proxy_url' }, ctx)).toBe(true)
    expect(rowLocked({ key: 'title_exclude_global' }, ctx)).toBe(false)
    expect(rowLocked({ kind: 'appearance' }, ctx)).toBe(false)
    expect(rowLocked({ kind: 'button', ownerOnly: true }, ctx)).toBe(true)
    expect(rowLocked({ key: 'proxy_url' }, { ...ctx, unlocked: true })).toBe(false)
    expect(rowLocked({ key: 'proxy_url' }, { ...ctx, member: false })).toBe(false)
  })
  it('member nav groups', () => {
    expect(memberGroup('account', idx, 'Viktor')).toBe('You')
    expect(memberGroup('models', idx, 'Viktor')).toBe('AI · yours')
    expect(memberGroup('exclude', idx, 'Viktor')).toBe('Yours')
    expect(memberGroup('scheduler', idx, 'Viktor')).toBe('Yours')      // flipped to own
    expect(memberGroup('advanced', idx, 'Viktor')).toBe('Shared · Viktor')
    expect(memberGroup('advanced', idx, 'Viktor', true)).toBe('Shared · Viktor')
  })
  it('scope tags', () => {
    expect(scopeTag('appearance', { idx, member: false })).toEqual({ label: 'Per device', tone: 'neutral' })
    expect(scopeTag('advanced', { idx, member: true })).toEqual({ label: 'Owner only', tone: 'good' })
    expect(scopeTag('models', { idx, member: true, ownerName: 'Viktor' }).label).toBe('Inherited from Viktor')
    expect(scopeTag('exclude', { idx, member: true }).label).toBe('Yours')
    expect(scopeTag('notifications', { idx, member: false }).label).toBe('Per account')
    expect(scopeTag('models', { idx, member: false })).toBe(null)    // the owner gets the toggle
  })
})

describe('LockCard / UnlockPill', () => {
  it('the lock card unlocks with the owner PIN', async () => {
    api.post.mockResolvedValue({ data: { ok: true, unlocked_until: '2026-10-05T12:15:00+00:00' } })
    const onUnlocked = vi.fn()
    render(<LockCard ownerName="Viktor" sectionTitle="Advanced" onUnlocked={onUnlocked} />)
    expect(screen.getByText(/Shared by everyone on this install/)).toBeTruthy()
    typePin('9999', 'Viktor’s PIN')
    await flush()
    expect(api.post).toHaveBeenCalledWith('/auth/verify-owner-pin', { pin: '9999' })
    expect(onUnlocked).toHaveBeenCalledWith(Date.parse('2026-10-05T12:15:00+00:00'))
  })
  it('a wrong owner PIN says so and stays locked', async () => {
    api.post.mockRejectedValue({ response: { status: 401, data: { detail: 'wrong_pin', tries_left: 4 } } })
    const onUnlocked = vi.fn()
    render(<LockCard ownerName="Viktor" sectionTitle="Advanced" inherited onUnlocked={onUnlocked} />)
    expect(screen.getByText(/Inherited from Viktor/)).toBeTruthy()
    typePin('1111', 'Viktor’s PIN')
    await flush()
    expect(onUnlocked).not.toHaveBeenCalled()
    expect(screen.getByText('Wrong PIN · 4 tries left')).toBeTruthy()
  })
  it('without an owner PIN the lock card is a plain Unlock button (D3)', async () => {
    api.post.mockResolvedValue({ data: { ok: true, unlocked_until: '2026-10-05T12:15:00+00:00' } })
    const onUnlocked = vi.fn()
    render(<LockCard ownerName="Viktor" sectionTitle="Advanced" ownerHasPin={false} onUnlocked={onUnlocked} />)
    expect(screen.queryAllByLabelText(/Viktor’s PIN digit/)).toHaveLength(0)
    expect(screen.getByText(/only a privacy latch/)).toBeTruthy()
    fireEvent.click(screen.getByText('Unlock', { selector: '[role="button"], button' }))
    await flush()
    expect(api.post).toHaveBeenCalledWith('/auth/verify-owner-pin', { pin: undefined })
    expect(onUnlocked).toHaveBeenCalledWith(Date.parse('2026-10-05T12:15:00+00:00'))
  })
  it('without an owner PIN the pill dialog has a plain Unlock too', async () => {
    api.post.mockResolvedValue({ data: { ok: true, unlocked_until: '2026-10-05T12:15:00+00:00' } })
    const onUnlocked = vi.fn()
    render(<UnlockPill ownerName="Viktor" unlockedUntil={null} ownerHasPin={false} onUnlocked={onUnlocked} onLock={() => {}} />)
    fireEvent.click(screen.getByLabelText('Owner sections locked — unlock as Viktor'))
    expect(screen.queryAllByLabelText(/Viktor’s PIN digit/)).toHaveLength(0)
    fireEvent.click(screen.getByLabelText('Unlock the shared settings as Viktor'))
    await flush()
    expect(onUnlocked).toHaveBeenCalled()
  })
  it('the pill reads locked ↔ unlocked and Lock calls back', async () => {
    const onLock = vi.fn().mockResolvedValue()
    const { rerender } = render(<UnlockPill ownerName="Viktor" unlockedUntil={null} onUnlocked={() => {}} onLock={onLock} />)
    expect(screen.getByText(/Owner sections locked · Unlock as Viktor/)).toBeTruthy()
    fireEvent.click(screen.getByLabelText('Owner sections locked — unlock as Viktor'))
    expect(screen.getAllByLabelText(/Viktor’s PIN digit/)).toHaveLength(4)
    rerender(<UnlockPill ownerName="Viktor" unlockedUntil={Date.now() + 600000} onUnlocked={() => {}} onLock={onLock} />)
    expect(screen.getByText(/Unlocked as Viktor · until/)).toBeTruthy()
    fireEvent.click(screen.getByText('Lock'))
    await flush()
    expect(onLock).toHaveBeenCalled()
  })
})

// ── QA F2 · D2 Accounts section with one account, R6-07 rename/recolour, T7-06 ──
describe('AccountsTable (owner, always shown)', () => {
  const me = { id: 'o1', name: 'Viktor', color: '#2f6fed', role: 'owner', has_pin: false }
  const one = { accounts: [{ ...me, is_self: true, counts: { jobs: 12 } }], max_accounts: 5, self_id: 'o1' }
  it('with one account: own row, PIN, API token, Edit, Add account; no Remove', () => {
    const onAdd = vi.fn()
    render(<AccountsTable data={one} me={me} onAdd={onAdd} onChanged={() => {}} push={() => {}} />)
    const rows = screen.getAllByRole('row')
    expect(rows).toHaveLength(1)
    expect(rows[0].getAttribute('data-account-row')).toBe('Viktor')
    expect(screen.getByText('12 jobs')).toBeTruthy()
    expect(screen.getByLabelText('Set your PIN')).toBeTruthy()
    expect(screen.getByLabelText('Create an API token for scripts')).toBeTruthy()
    expect(screen.getByLabelText('Rename or recolour your account')).toBeTruthy()
    expect(screen.queryByText('Remove')).toBeNull()
    expect(screen.getByText(/Just you/)).toBeTruthy()
    fireEvent.click(screen.getByText('+ Add account', { selector: '[role="button"], button' }))
    expect(onAdd).toHaveBeenCalled()
  })
  it('falls back to the caller when GET /accounts has not answered', () => {
    render(<AccountsTable data={null} me={me} onAdd={() => {}} onChanged={() => {}} push={() => {}} />)
    expect(screen.getAllByRole('row')).toHaveLength(1)
  })
  it('a member row has Edit, Reset PIN and Remove; own PIN dialog offers Remove PIN instead', () => {
    const data = { accounts: [{ ...me, has_pin: true, is_self: true }, { id: 'm1', name: 'Alex', color: '#2e9e6b', role: 'member', has_pin: false }], max_accounts: 5 }
    render(<AccountsTable data={data} me={me} onAdd={() => {}} onChanged={() => {}} push={() => {}} />)
    expect(screen.getByLabelText('Rename or recolour Alex')).toBeTruthy()
    expect(screen.getByLabelText('Remove Alex')).toBeTruthy()
    fireEvent.click(screen.getByLabelText('Change your PIN'))
    fireEvent.click(screen.getByText('Remove PIN instead'))
    expect(screen.getAllByText('Remove your PIN').length).toBeGreaterThan(0)
    expect(screen.getAllByLabelText(/Current PIN digit/)).toHaveLength(4)
  })
  it('Edit renames and recolours through PATCH (R6-07)', async () => {
    api.patch.mockResolvedValue({ data: {} })
    const push = vi.fn()
    const onChanged = vi.fn()
    const data = { accounts: [{ ...me, is_self: true }, { id: 'm1', name: 'Alex', color: '#2e9e6b', role: 'member' }], max_accounts: 5 }
    render(<AccountsTable data={data} me={me} onAdd={() => {}} onChanged={onChanged} push={push} />)
    fireEvent.click(screen.getByLabelText('Rename or recolour Alex'))
    fireEvent.change(screen.getByLabelText('Account name'), { target: { value: 'Alexandra' } })
    fireEvent.click(screen.getAllByRole('radio')[3])
    fireEvent.click(screen.getByText('Save', { selector: '[role="button"], button' }))
    await flush()
    expect(api.patch).toHaveBeenCalledWith('/accounts/m1', { name: 'Alexandra', color: '#e0912b' })
    expect(onChanged).toHaveBeenCalled()
    expect(push).toHaveBeenCalledWith({ kind: 'success', msg: 'Saved Alexandra' })
  })
})

describe('EditAccountModal', () => {
  it('Save stays off until something changes, then sends only what changed', async () => {
    api.patch.mockResolvedValue({ data: {} })
    const onSaved = vi.fn()
    render(<EditAccountModal account={{ id: 'o1', name: 'Viktor', color: '#2f6fed' }} self onClose={() => {}} onSaved={onSaved} />)
    const save = screen.getByText('Save', { selector: '[role="button"], button' })
    expect(save.getAttribute('aria-disabled')).toBe('true')
    fireEvent.click(screen.getAllByRole('radio')[1])
    fireEvent.click(save)
    await flush()
    expect(api.patch).toHaveBeenCalledWith('/accounts/o1', { color: '#d9534f' })
    expect(onSaved).toHaveBeenCalledWith(expect.objectContaining({ color: '#d9534f', name: 'Viktor' }))
  })
})

describe('PolicyToggle (T7-06)', () => {
  it("is labelled as the member's policy", () => {
    render(<PolicyToggle memberName="Alex" mode="inherit" sectionTitle="Feed" onChange={() => {}} />)
    expect(screen.getByText('Alex’s policy:')).toBeTruthy()
  })
})
