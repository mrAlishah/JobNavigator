// Settings › Models (owner). R30 L-01: switching an override off clears its API key too, since
// the key belongs to the override's provider and is hidden once the row closes. L-03: an empty
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

const OWNER = { id: 'o1', name: 'Viktor', color: '#2f6fed', role: 'owner', has_pin: false }
const BASE = {
  cover_letter_voice_presets: [], autofill_field_patterns: {}, autofill_option_synonyms: {},
  llm_provider: 'lmstudio', llm_model: 'qwen', lmstudio_base_url: '', ollama_base_url: '',
  email_llm_provider: 'ollama', email_llm_model: 'llama3',
}

const mockApi = (settings) => {
  api.get.mockImplementation((path) => {
    const data = {
      '/auth/me': { account: OWNER, accounts_count: 1, owner_has_pin: false },
      '/accounts': { accounts: [{ ...OWNER, is_self: true }], max_accounts: 5, self_id: 'o1' },
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
  it('switching an override off clears its provider, model, effort and key', async () => {
    mockApi({ ...BASE, autofill_llm_provider: 'openai', autofill_llm_model: 'gpt-5', autofill_llm_api_key: '••••••' })
    await renderSettings('Autofill — override the Primary model')
    fireEvent.click(screen.getByLabelText('Autofill — override the Primary model'))
    await waitFor(() => expect(api.patch).toHaveBeenCalledTimes(4))
    expect(patchedKeys()).toEqual(['autofill_llm_provider', 'autofill_llm_model', 'autofill_llm_effort', 'autofill_llm_api_key'])
    expect(api.patch).toHaveBeenLastCalledWith('/settings', { autofill_llm_api_key: '' })
  })

  it('an override with no key sends no key PATCH', async () => {
    mockApi({ ...BASE, autofill_llm_provider: 'openai', autofill_llm_model: 'gpt-5', autofill_llm_api_key: '' })
    await renderSettings('Autofill — override the Primary model')
    fireEvent.click(screen.getByLabelText('Autofill — override the Primary model'))
    await waitFor(() => expect(api.patch).toHaveBeenCalledTimes(3))
    expect(patchedKeys()).not.toContain('autofill_llm_api_key')
  })

  it('an empty local server address shows the address in use as its placeholder', async () => {
    mockApi({ ...BASE, _effective: { lmstudio_base_url: 'http://host.docker.internal:1234/v1', ollama_base_url: 'http://localhost:11434' } })
    await renderSettings('LM Studio address')
    expect(screen.getByLabelText('LM Studio address').getAttribute('placeholder')).toBe('http://host.docker.internal:1234/v1')
    expect(screen.getByLabelText('Ollama address').getAttribute('placeholder')).toBe('http://localhost:11434')
    expect(screen.getByLabelText('LM Studio address').value).toBe('')
  })
})
