// Résumé Settings: the footer and the fork's profile-image setting are the shared `Switch` (ui.jsx), as upstream's
// "Page footer" is. The fork's rules stay: both read as ON when the key is absent, the image switch is disabled for a
// template without a picture, and the Settings section exists for base résumés too (upstream: tailored copies only).
import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { PERSONA_SECTIONS, SECTION_ORDER, SectionEditor, SettingsEditor, resumeSectionNames } from '../screens/ResumeSections'

const footer = () => screen.getByRole('switch', { name: /Page footer/ })
const image = () => screen.getByRole('switch', { name: /Profile image/ })

describe('résumé settings use the shared Switch', () => {
  it('shows exactly two switches and no checkbox, drawn by the shared component', () => {
    const { container } = render(<SettingsEditor data={{}} setField={vi.fn()} hasProfileImage />)
    expect(screen.getAllByRole('switch')).toHaveLength(2)
    expect(screen.queryAllByRole('checkbox')).toHaveLength(0)
    expect(container.querySelectorAll('.v2-swtrack')).toHaveLength(2)      // the track ui.jsx's Switch renders
  })

  it('keeps the fork defaults: both are on until the field says false', () => {
    const { rerender } = render(<SettingsEditor data={{}} setField={vi.fn()} hasProfileImage />)
    expect(footer().getAttribute('aria-checked')).toBe('true')
    expect(image().getAttribute('aria-checked')).toBe('true')
    rerender(<SettingsEditor data={{ footer_enabled: false, profile_image_enabled: false }} setField={vi.fn()} hasProfileImage />)
    expect(footer().getAttribute('aria-checked')).toBe('false')
    expect(image().getAttribute('aria-checked')).toBe('false')
    rerender(<SettingsEditor data={{ footer_enabled: true, profile_image_enabled: true }} setField={vi.fn()} hasProfileImage />)
    expect(footer().getAttribute('aria-checked')).toBe('true')
    expect(image().getAttribute('aria-checked')).toBe('true')
  })

  it('writes the same fields and values as before', () => {
    const setField = vi.fn()
    const { rerender } = render(<SettingsEditor data={{}} setField={setField} hasProfileImage />)
    fireEvent.click(footer())
    fireEvent.click(image())
    expect(setField.mock.calls).toEqual([['footer_enabled', false], ['profile_image_enabled', false]])
    setField.mockClear()
    rerender(<SettingsEditor data={{ footer_enabled: false, profile_image_enabled: false }} setField={setField} hasProfileImage />)
    fireEvent.click(footer())
    fireEvent.click(image())
    expect(setField.mock.calls).toEqual([['footer_enabled', true], ['profile_image_enabled', true]])
  })

  it('works from the keyboard: focusable, Enter and Space toggle', () => {
    const setField = vi.fn()
    render(<SettingsEditor data={{}} setField={setField} hasProfileImage />)
    for (const control of [footer(), image()]) expect(control.getAttribute('tabindex')).toBe('0')
    fireEvent.keyDown(footer(), { key: 'Enter' })
    fireEvent.keyDown(footer(), { key: ' ' })
    fireEvent.keyDown(image(), { key: 'Enter' })
    fireEvent.keyDown(image(), { key: ' ' })
    fireEvent.keyDown(image(), { key: 'a' })                  // any other key does nothing
    expect(setField.mock.calls).toEqual([['footer_enabled', false], ['footer_enabled', false],
      ['profile_image_enabled', false], ['profile_image_enabled', false]])
  })

  it('disables only the image switch for a template without a picture, mouse and keyboard', () => {
    const setField = vi.fn()
    render(<SettingsEditor data={{}} setField={setField} hasProfileImage={false} />)
    const control = image()
    expect(control.getAttribute('aria-disabled')).toBe('true')
    expect(control.getAttribute('tabindex')).toBe('-1')
    expect(control.getAttribute('title')).toBe('The selected template has no profile image')
    fireEvent.click(control)
    fireEvent.keyDown(control, { key: 'Enter' })
    fireEvent.keyDown(control, { key: ' ' })
    expect(setField).not.toHaveBeenCalled()
    expect(footer().getAttribute('aria-disabled')).toBeNull()
    fireEvent.click(footer())
    expect(setField).toHaveBeenCalledWith('footer_enabled', false)
  })

  it('leaves the image switch enabled when the template supports a picture or says nothing', () => {
    const { rerender } = render(<SettingsEditor data={{}} setField={vi.fn()} hasProfileImage />)
    expect(image().getAttribute('aria-disabled')).toBeNull()
    rerender(<SettingsEditor data={{}} setField={vi.fn()} />)
    expect(image().getAttribute('aria-disabled')).toBeNull()
  })

  it('explains the footer the way upstream does', () => {
    render(<SettingsEditor data={{}} setField={vi.fn()} hasProfileImage />)
    expect(footer().getAttribute('title')).toMatch(/foot of every page of this résumé's PDF and Word downloads/)
    expect(screen.getByText(/“name - job title - company” in small type/)).toBeTruthy()
  })

  it('is reached through the Settings section, which exists for every résumé, not only tailored copies', () => {
    render(<SectionEditor name="Settings" data={{}} setField={vi.fn()} hasProfileImage />)
    expect(screen.getAllByRole('switch')).toHaveLength(2)
    expect(SECTION_ORDER.at(-1)).toBe('Settings')
    expect(PERSONA_SECTIONS).not.toContain('Settings')
    for (const doc of [{ is_base: true, job_id: null }, { is_base: false, job_id: 'j1' }, { is_base: false, job_id: null }]) {
      expect(resumeSectionNames(doc)).toContain('Settings')
    }
  })
})
