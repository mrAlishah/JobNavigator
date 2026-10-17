// Settings › member policies (owner, two or more accounts). The Inherit / Own controls
// on the policy sections are hidden until the owner flips a member's "Edit policies"
// switch in the Accounts section; one member at a time; switching off only hides them (no note, no Done link)
// (no PATCH); nothing is persisted, so a fresh render starts off.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('../api', () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  authFailure: () => null,
}))
// vite.config.js defines __APP_VERSION__ for the app build; vitest.config.js does not
globalThis.__APP_VERSION__ = 'test'
const api = (await import('../api')).default
const { default: Settings } = await import('../screens/Settings')
const { AccountsTable, nonDefaultPolicies } = await import('../screens/SettingsAccounts')

const OWNER = { id: 'o1', name: 'Viktor', color: '#2f6fed', role: 'owner', has_pin: false }
const MILA = { id: 'm1', name: 'Mila', color: '#2e9e6b', role: 'member', has_pin: false }
const ALEX = { id: 'm2', name: 'Alex', color: '#d9534f', role: 'member', has_pin: false }
const SECTIONS = [
  { id: 'prep', scope: 'policy', default: 'inherit', mode: 'inherit', keys: ['prep_include'] },
  { id: 'exclude', scope: 'policy', default: 'own', mode: 'own', keys: ['body_exclusion_phrases'] },
  { id: 'advanced', scope: 'owner_only', keys: [] },
]
const POLICIES = { m1: { prep: 'own', exclude: 'own' }, m2: { prep: 'inherit', exclude: 'own' } }

const mockApi = () => {
  api.get.mockImplementation((path) => {
    const data = {
      '/auth/me': { account: OWNER, accounts_count: 3, owner_has_pin: false },
      '/accounts': { accounts: [{ ...OWNER, is_self: true }, MILA, ALEX], max_accounts: 5, self_id: 'o1' },
      '/settings/sections': SECTIONS,
      '/settings/policies': POLICIES,
      '/resumes': [],
      '/settings': { cover_letter_voice_presets: [], autofill_field_patterns: {}, autofill_option_synonyms: {} },
    }[path]
    return Promise.resolve({ data: data ?? {} })
  })
  api.post.mockResolvedValue({ data: {} })
  api.patch.mockResolvedValue({ data: {} })
}

const toggles = () => document.querySelectorAll('[data-policy-for]')
const sw = (name) => screen.getByLabelText(`Edit ${name}’s policies`)
const renderSettings = async () => {
  render(<MemoryRouter initialEntries={['/settings']}><Settings /></MemoryRouter>)
  await waitFor(() => expect(screen.getByLabelText('Edit Mila’s policies')).toBeTruthy())
}

beforeEach(() => { vi.clearAllMocks(); mockApi() })
afterEach(() => cleanup())

describe('Settings › member policies (owner)', () => {
  it('off by default: no header picker, no Inherit / Own control on any section', async () => {
    await renderSettings()
    expect(document.querySelector('[data-member-policies]')).toBeNull()
    expect(screen.queryByText('Member policies')).toBeNull()
    expect(toggles()).toHaveLength(0)
    expect(sw('Mila').getAttribute('aria-checked')).toBe('false')
    expect(sw('Alex').getAttribute('aria-checked')).toBe('false')
    // the owner's own row has no switch
    const ownerRow = document.querySelector('[data-account-row="Viktor"]')
    expect(within(ownerRow).queryByRole('switch')).toBeNull()
  })

  it('on shows the member’s controls; a second member switches the first off; off hides them, no PATCH', async () => {
    await renderSettings()
    fireEvent.click(sw('Mila'))
    await waitFor(() => expect(toggles().length).toBe(2))
    expect([...toggles()].every((t) => t.getAttribute('data-policy-for') === 'Mila')).toBe(true)
    expect(sw('Mila').getAttribute('aria-checked')).toBe('true')
    expect(document.querySelector('[data-policy-editing]')).toBeNull()   // no note under the table
    // Mila's saved policy is what the control shows
    const prep = document.querySelector('[data-sec="sec-prep"] [data-policy-for]')
    expect(within(prep).getByRole('radio', { name: /Own/ }).getAttribute('aria-checked')).toBe('true')

    fireEvent.click(sw('Alex'))
    await waitFor(() => expect([...toggles()].every((t) => t.getAttribute('data-policy-for') === 'Alex')).toBe(true))
    expect(toggles()).toHaveLength(2)
    expect(sw('Mila').getAttribute('aria-checked')).toBe('false')
    expect(sw('Alex').getAttribute('aria-checked')).toBe('true')

    fireEvent.click(sw('Alex'))
    await waitFor(() => expect(toggles()).toHaveLength(0))
    expect(api.patch).not.toHaveBeenCalledWith('/settings/policies', expect.anything())
  })

  it('"Per device" on Display shows only while a member’s policies are edited', async () => {
    await renderSettings()
    const perDevice = () => document.querySelector('[data-sec="sec-appearance"] [data-scope-tag="Per device"]')
    expect(perDevice()).toBeNull()
    fireEvent.click(sw('Mila'))
    await waitFor(() => expect(perDevice()).toBeTruthy())
    fireEvent.click(sw('Mila'))
    await waitFor(() => expect(perDevice()).toBeNull())
  })

  it('a segment click still sets that member’s policy', async () => {
    await renderSettings()
    fireEvent.click(sw('Alex'))
    await waitFor(() => expect(toggles().length).toBe(2))
    const prep = document.querySelector('[data-sec="sec-prep"] [data-policy-for]')
    fireEvent.click(within(prep).getByRole('radio', { name: /Own/ }))
    await waitFor(() => expect(api.patch).toHaveBeenCalledWith('/settings/policies', { account_id: 'm2', section: 'prep', mode: 'own' }))
  })
})

describe('AccountsTable · Edit policies switch', () => {
  const data = { accounts: [{ ...OWNER, is_self: true }, MILA], max_accounts: 5 }
  const idx = { byId: Object.fromEntries(SECTIONS.map((s) => [s.id, s])), keySection: {} }
  it('no switch without onPolicyFor (one account, or a member view)', () => {
    render(<AccountsTable data={data} me={OWNER} onAdd={() => {}} onChanged={() => {}} push={() => {}} />)
    expect(screen.queryByRole('switch')).toBeNull()
  })
  it('reports the member id on, "" off, and counts sections off their default', () => {
    const onPolicyFor = vi.fn()
    const { rerender } = render(<AccountsTable data={data} me={OWNER} onAdd={() => {}} onChanged={() => {}} push={() => {}}
      policyFor="" onPolicyFor={onPolicyFor} policies={POLICIES} idx={idx} />)
    expect(screen.getByText('1 policy off default')).toBeTruthy()
    // the count sits beside the switch, in the same cell
    expect(sw('Mila').parentElement.textContent).toContain('1 policy off default')
    fireEvent.click(sw('Mila'))
    expect(onPolicyFor).toHaveBeenLastCalledWith('m1')
    rerender(<AccountsTable data={data} me={OWNER} onAdd={() => {}} onChanged={() => {}} push={() => {}}
      policyFor="m1" onPolicyFor={onPolicyFor} policies={POLICIES} idx={idx} />)
    fireEvent.click(sw('Mila'))
    expect(onPolicyFor).toHaveBeenLastCalledWith('')
    expect(nonDefaultPolicies('m2', POLICIES, idx)).toBe(0)
  })
})
