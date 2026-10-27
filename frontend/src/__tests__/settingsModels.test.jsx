// Settings › Models (owner). 3.1: one API key per provider — every slot's key box shows and saves
// the key of the slot's current provider, and switching an override off keeps it. L-03: an empty
// LM Studio / Ollama address shows the address in use (GET /settings `_effective`) as its placeholder.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'

vi.mock('../api', () => ({
  default: { get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  authFailure: () => null,
}))
globalThis.__APP_VERSION__ = 'test'
const api = (await import('../api')).default
const { default: Settings } = await import('../screens/Settings')

const OWNER = { id: 'o1', name: 'Jordan', color: '#2f6fed', role: 'owner', has_pin: false }
const BASE = {
  cover_letter_voice_presets: [], autofill_field_patterns: {}, autofill_option_synonyms: {},
  llm_provider: 'lmstudio', llm_model: 'qwen', lmstudio_base_url: '', ollama_base_url: '',
  email_llm_provider: 'ollama', email_llm_model: 'llama3',
}

const MEMBER = { id: 'm1', name: 'Mila', color: '#e8590c', role: 'member', has_pin: false }
const mockApi = (settings, accounts = 1) => {
  api.get.mockImplementation((path) => {
    const others = accounts > 1 ? [MEMBER] : []
    const data = {
      '/auth/me': { account: OWNER, accounts_count: accounts, owner_has_pin: false },
      '/accounts': { accounts: [{ ...OWNER, is_self: true }, ...others], max_accounts: 5, self_id: 'o1' },
      '/resumes': [],
      '/settings': settings,
    }[path]
    return Promise.resolve({ data: data ?? {} })
  })
  api.post.mockResolvedValue({ data: {} })
  api.patch.mockResolvedValue({ data: {} })
}

const renderSettings = async (label) => {
  render(<MemoryRouter initialEntries={['/settings']}><Settings /></MemoryRouter>)
  await waitFor(() => expect(screen.getByLabelText(label)).toBeTruthy())
}
const patchedKeys = () => api.patch.mock.calls.map(([, body]) => Object.keys(body)[0])

beforeEach(() => { vi.clearAllMocks() })
afterEach(() => cleanup())

describe('Settings › Models', () => {
  it('switching an override off clears its provider, model and effort, and keeps the provider key', async () => {
    mockApi({ ...BASE, autofill_llm_provider: 'openai', autofill_llm_model: 'gpt-5', openai_api_key: '••••••' })
    await renderSettings('Autofill — override the Primary model')
    fireEvent.click(screen.getByLabelText('Autofill — override the Primary model'))
    await waitFor(() => expect(api.patch).toHaveBeenCalledTimes(3))
    expect(patchedKeys()).toEqual(['autofill_llm_provider', 'autofill_llm_model', 'autofill_llm_effort'])
  })

  it("an override's key box saves the key of its provider", async () => {
    mockApi({ ...BASE, autofill_llm_provider: 'openrouter', autofill_llm_model: 'openai/gpt-5' })
    await renderSettings('Autofill — API key')
    const box = screen.getByLabelText('Autofill — API key')
    fireEvent.change(box, { target: { value: 'sk-or' } })
    fireEvent.blur(box)
    await waitFor(() => expect(api.patch).toHaveBeenCalledWith('/settings', { openrouter_api_key: 'sk-or' }))
  })

  it("the Primary's key box follows the Primary's provider", async () => {
    mockApi({ ...BASE, llm_provider: 'openai', llm_model: 'gpt-5', openai_api_key: 'sk-openai', anthropic_api_key: 'sk-ant' })
    await renderSettings('API key')
    expect(screen.getByLabelText('API key').value).not.toBe('')
    const box = screen.getByLabelText('API key')
    fireEvent.focus(box)                      // reveal the stored key
    expect(box.value).toBe('sk-openai')
  })

  it('with several accounts the owner sees the local server addresses even when no slot of its own uses them (R30 L-10)', async () => {
    mockApi({ ...BASE, llm_provider: 'claude_code', email_llm_provider: '' }, 2)
    await renderSettings('LM Studio address')
    expect(screen.getByLabelText('Ollama address')).toBeTruthy()
  })

  it('a provider without a key shows no key box for the Primary', async () => {
    mockApi(BASE)                             // the Primary is LM Studio
    await renderSettings('LM Studio address')
    expect(screen.queryByLabelText('API key')).toBeNull()
  })

  it('an empty local server address shows the address in use as its placeholder', async () => {
    mockApi({ ...BASE, _effective: { lmstudio_base_url: 'http://host.docker.internal:1234/v1', ollama_base_url: 'http://localhost:11434' } })
    await renderSettings('LM Studio address')
    expect(screen.getByLabelText('LM Studio address').getAttribute('placeholder')).toBe('http://host.docker.internal:1234/v1')
    expect(screen.getByLabelText('Ollama address').getAttribute('placeholder')).toBe('http://localhost:11434')
    expect(screen.getByLabelText('LM Studio address').value).toBe('')
  })
})
