// The telemetry choice: asked of the owner once (Yes preselected), never of a member, never when
// JN_TELEMETRY decides; Settings shows the env value when it is locked.
import { describe, it, expect, beforeEach, vi } from 'vitest'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'

vi.mock('../api', () => ({ default: { get: vi.fn(), patch: vi.fn() } }))
const api = (await import('../api')).default
const { TelemetryAsk, TelemetrySettingRow } = await import('../TelemetryChoice')

const state = (over) => ({ level: 'unset', locked: false, asked: false, owner: true, umami: null, ...over })

beforeEach(() => {
  vi.clearAllMocks()
  api.patch.mockResolvedValue({ data: { updated: ['telemetry_level'] } })
})

describe('TelemetryAsk', () => {
  it('asks the owner once, with Yes preselected', async () => {
    api.get.mockResolvedValue({ data: state() })
    render(<TelemetryAsk />)
    expect(await screen.findByText('Allow anonymous telemetry?')).toBeTruthy()
    expect(screen.getByRole('radio', { name: /^Yes/ }).getAttribute('aria-checked')).toBe('true')
    fireEvent.click(screen.getByText('Save'))
    await waitFor(() => expect(api.patch).toHaveBeenCalledWith('/settings', { telemetry_level: 'full' }))
    await waitFor(() => expect(screen.queryByText('Allow anonymous telemetry?')).toBeNull())
  })

  it('saves Essentials when picked', async () => {
    api.get.mockResolvedValue({ data: state() })
    render(<TelemetryAsk />)
    fireEvent.click(await screen.findByRole('radio', { name: /^Essentials/ }))
    fireEvent.click(screen.getByText('Save'))
    await waitFor(() => expect(api.patch).toHaveBeenCalledWith('/settings', { telemetry_level: 'essentials' }))
  })

  it.each([
    ['a member', state({ owner: false })],
    ['an install already asked', state({ asked: true, level: 'full' })],
    ['an install where JN_TELEMETRY decides', state({ locked: true, level: 'off' })],
  ])('does not ask %s', async (_n, data) => {
    api.get.mockResolvedValue({ data })
    render(<TelemetryAsk />)
    await waitFor(() => expect(api.get).toHaveBeenCalled())
    expect(screen.queryByText('Allow anonymous telemetry?')).toBeNull()
  })

  it('shows, once opened, what the picked level sends, what is never sent and how to turn it off', async () => {
    api.get.mockResolvedValue({ data: state() })
    render(<TelemetryAsk />)
    const toggle = await screen.findByLabelText('What is sent?')
    expect(screen.queryByText(/How often scrapes and AI calls succeed/)).toBeNull()   // collapsed by default
    fireEvent.click(toggle)
    expect(screen.getByText(/Which screens are opened/)).toBeTruthy()
    expect(screen.getByText(/How often scrapes and AI calls succeed/)).toBeTruthy()
    expect(screen.getByText(/^Job titles, search terms/)).toBeTruthy()
    expect(screen.getByText(/JN_TELEMETRY=off/)).toBeTruthy()
    fireEvent.click(screen.getByRole('radio', { name: /^Essentials/ }))
    expect(screen.queryByText(/Which screens are opened/)).toBeNull()
    expect(screen.getByText(/How often scrapes and AI calls succeed/)).toBeTruthy()
  })

  it('closes only through Save: Escape and a click outside keep it open', async () => {
    api.get.mockResolvedValue({ data: state() })
    render(<TelemetryAsk />)
    const dialog = await screen.findByRole('dialog')
    fireEvent.keyDown(document, { key: 'Escape' })
    fireEvent.keyDown(window, { key: 'Escape' })
    fireEvent.click(dialog.parentElement)
    expect(screen.getByText('Allow anonymous telemetry?')).toBeTruthy()
    expect(api.patch).not.toHaveBeenCalled()
  })

  it('says why in the passive voice', async () => {
    api.get.mockResolvedValue({ data: state() })
    render(<TelemetryAsk />)
    expect(await screen.findByText(/^This data is collected to improve JobNavigator/)).toBeTruthy()
  })
})

describe('TelemetrySettingRow', () => {
  it('names the env value when JN_TELEMETRY locks it', async () => {
    api.get.mockResolvedValue({ data: state({ locked: true, level: 'essentials', asked: true }) })
    render(<TelemetrySettingRow />)
    expect(await screen.findByText('Set by JN_TELEMETRY=essentials in .env.')).toBeTruthy()
  })
})
