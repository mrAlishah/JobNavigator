// versionCheck.js — the image/version mismatch warning. /health reports
// `image_outdated` when the backend image is older than its mounted code, and its
// `version` lets the dashboard notice it is on another release than the backend.
// A matching install must produce nothing: no warning, no toast.
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { renderHook, waitFor } from '@testing-library/react'
import { versionWarning, useVersionWarning, FIX } from '../versionCheck'

const MATCH = { status: 'ok', service: 'JobNavigator', version: '3.0', image_outdated: false, image_message: null, image_details: [] }
const OUTDATED = {
  ...MATCH,
  image_outdated: true,
  image_message: 'The backend image is older than the code (python-docx missing). ' + FIX,
  image_details: ['python-docx missing'],
}

describe('versionWarning', () => {
  it('is null for a matching install', () => {
    expect(versionWarning(MATCH, '3.0')).toBeNull()
  })

  it('is null without a /health answer', () => {
    expect(versionWarning(null, '3.0')).toBeNull()
    expect(versionWarning(undefined, '3.0')).toBeNull()
  })

  it('shows the backend message when the image is outdated', () => {
    const w = versionWarning(OUTDATED, '3.0')
    expect(w.kind).toBe('image')
    expect(w.short).toBe('Backend image out of date')
    expect(w.message).toBe(OUTDATED.image_message)
  })

  it('flags a dashboard on another release than the backend', () => {
    const w = versionWarning({ ...MATCH, version: '3.1' }, '3.0')
    expect(w.kind).toBe('version')
    expect(w.short).toBe('Version mismatch')
    expect(w.message).toContain('v3.0')
    expect(w.message).toContain('v3.1')
    expect(w.message).toContain('docker compose pull')
  })

  it('an older backend without the image fields still compares versions', () => {
    expect(versionWarning({ status: 'ok', version: '2.2.0' }, '3.0').kind).toBe('version')
    expect(versionWarning({ status: 'ok', version: '3.0' }, '3.0')).toBeNull()
  })

  it('the image warning wins over a version mismatch', () => {
    expect(versionWarning({ ...OUTDATED, version: '3.1' }, '3.0').kind).toBe('image')
  })
})

describe('useVersionWarning', () => {
  const answer = (body) => vi.fn(() => Promise.resolve({ ok: true, json: () => Promise.resolve(body) }))
  beforeEach(() => { sessionStorage.clear() })
  afterEach(() => { vi.unstubAllGlobals() })

  it('raises one error toast for an outdated image', async () => {
    vi.stubGlobal('fetch', answer(OUTDATED))
    const push = vi.fn()
    const { result } = renderHook(() => useVersionWarning('3.0', push))
    await waitFor(() => expect(result.current?.kind).toBe('image'))
    expect(push).toHaveBeenCalledTimes(1)
    expect(push).toHaveBeenCalledWith({ kind: 'error', msg: OUTDATED.image_message })
    expect(fetch).toHaveBeenCalledWith('/health')
  })

  it('raises the toast for a version mismatch', async () => {
    vi.stubGlobal('fetch', answer({ ...MATCH, version: '3.1' }))
    const push = vi.fn()
    const { result } = renderHook(() => useVersionWarning('3.0', push))
    await waitFor(() => expect(result.current?.kind).toBe('version'))
    expect(push.mock.calls[0][0].msg).toContain('v3.1')
  })

  it('a matching install shows nothing', async () => {
    const f = answer(MATCH)
    vi.stubGlobal('fetch', f)
    const push = vi.fn()
    const { result } = renderHook(() => useVersionWarning('3.0', push))
    await waitFor(() => expect(f).toHaveBeenCalled())
    await new Promise((r) => setTimeout(r, 0))
    expect(result.current).toBeNull()
    expect(push).not.toHaveBeenCalled()
  })

  it('the toast is not raised again in the same session', async () => {
    vi.stubGlobal('fetch', answer(OUTDATED))
    const push = vi.fn()
    const first = renderHook(() => useVersionWarning('3.0', push))
    await waitFor(() => expect(first.result.current).not.toBeNull())
    first.unmount()
    const second = renderHook(() => useVersionWarning('3.0', push))
    await waitFor(() => expect(second.result.current).not.toBeNull())
    expect(push).toHaveBeenCalledTimes(1)
  })

  it('an unreachable backend raises nothing', async () => {
    vi.stubGlobal('fetch', vi.fn(() => Promise.reject(new TypeError('Failed to fetch'))))
    const push = vi.fn()
    const { result } = renderHook(() => useVersionWarning('3.0', push))
    await new Promise((r) => setTimeout(r, 0))
    expect(result.current).toBeNull()
    expect(push).not.toHaveBeenCalled()
  })
})
