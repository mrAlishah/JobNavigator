// telemetry.js: only fixed values leave the browser, page paths lose their ids, events go to this
// install's backend (never to Umami), and only at `full` (never in the demo, never with Do Not Track).
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'

vi.mock('../api', () => ({ default: { get: vi.fn(), post: vi.fn() } }))
const api = (await import('../api')).default
const T = await import('../telemetry')

const posted = () => api.post.mock.calls.map(([path, body]) => ({ path, ...body }))

beforeEach(() => {
  vi.clearAllMocks()
  T.__resetTelemetry()
  api.post.mockResolvedValue({ data: { sent: true } })
})
afterEach(() => { vi.unstubAllGlobals() })

describe('normalizePath', () => {
  it('replaces ids and drops query and hash', () => {
    expect(T.normalizePath('/resumes/4f1c2a9e-1b2c-4d5e-8f90-123456789abc')).toBe('/resumes/:id')
    expect(T.normalizePath('/feed?job=abc123def#x')).toBe('/feed')
    expect(T.normalizePath('/cover-letters/12345')).toBe('/cover-letters/:id')
    expect(T.normalizePath('/settings')).toBe('/settings')
  })
})

describe('sanitize', () => {
  it('keeps only listed events, fields and values', () => {
    expect(T.sanitize('nope', { a: 1 })).toBeNull()
    expect(T.sanitize('job_opened', { link_kind: 'direct', title: 'Secret PM' })).toEqual({ link_kind: 'direct' })
    expect(T.sanitize('job_opened', { link_kind: 'https://x' })).toEqual({})
    expect(T.sanitize('search_run', { mode: 'Keyword', term: 'secret' })).toEqual({ mode: 'keyword' })
    expect(T.sanitize('letter_generated', { voice: 'My own voice for Acme' })).toEqual({})
    expect(T.sanitize('letter_generated', { voice: T.voiceId('my-acme-voice') })).toEqual({ voice: 'custom' })
    expect(T.isShortcut('s')).toBe(true)
    expect(T.isShortcut('q')).toBe(false)
  })

  it('buckets counts', () => {
    expect([0, 1, 3, 12, 50, 500].map(T.bucket)).toEqual(['0', '1', '2_5', '6_20', '21_100', '101_plus'])
  })
})

describe('track', () => {
  it('sends nothing until enabled', () => {
    T.track('job_saved')
    T.trackPage('/feed')
    expect(api.post).not.toHaveBeenCalled()
  })

  it('posts to the backend with a normalized path', () => {
    T.__enableForTests()
    window.history.pushState({}, '', '/resumes/4f1c2a9e-1b2c-4d5e-8f90-123456789abc?x=1')
    T.track('resume_exported', { format: 'pdf', template: 'classic', name: 'My CV' })
    T.trackPage('/resumes/4f1c2a9e-1b2c-4d5e-8f90-123456789abc?x=1')
    expect(posted()).toEqual([
      { path: '/telemetry/event', name: 'resume_exported', data: { format: 'pdf', template: 'classic' }, url: '/resumes/:id' },
      { path: '/telemetry/event', name: 'page_view', data: {}, url: '/resumes/:id' },
    ])
  })

  it('drops the same event within 3 s', () => {
    T.__enableForTests()
    T.track('job_opened', { link_kind: 'direct' })
    T.track('job_opened', { link_kind: 'direct' })
    T.track('job_opened', { link_kind: 'board' })
    expect(api.post).toHaveBeenCalledTimes(2)
  })

  it('stops after 300 events in one load', () => {
    T.__enableForTests()
    for (let i = 0; i < 400; i++) T.trackPage(`/p${i}`)
    expect(api.post).toHaveBeenCalledTimes(300)
  })
})

describe('initTelemetry', () => {
  it('turns on at full', async () => {
    api.get.mockResolvedValue({ data: { level: 'full' } })
    await T.initTelemetry()
    T.track('job_saved')
    expect(api.post).toHaveBeenCalledTimes(1)
  })

  it.each(['essentials', 'unset', 'off'])('stays off at %s', async (level) => {
    api.get.mockResolvedValue({ data: { level } })
    await T.initTelemetry()
    T.track('job_saved')
    expect(api.post).not.toHaveBeenCalled()
  })

  it('turns off again when the choice changes to Essentials', async () => {
    api.get.mockResolvedValueOnce({ data: { level: 'full' } }).mockResolvedValueOnce({ data: { level: 'essentials' } })
    await T.initTelemetry()
    await T.initTelemetry()
    T.track('job_saved')
    expect(api.post).not.toHaveBeenCalled()
  })

  it('stays off with Do Not Track', async () => {
    vi.stubGlobal('navigator', { ...navigator, doNotTrack: '1' })
    api.get.mockResolvedValue({ data: { level: 'full' } })
    await T.initTelemetry()
    T.track('job_saved')
    expect(api.post).not.toHaveBeenCalled()
  })

  it('survives the API failing', async () => {
    api.get.mockRejectedValue(new Error('offline'))
    expect(await T.initTelemetry()).toBeNull()
  })

  it('never loads a script from anywhere', async () => {
    api.get.mockResolvedValue({ data: { level: 'full' } })
    await T.initTelemetry()
    expect(document.head.querySelectorAll('script').length).toBe(0)
  })
})
