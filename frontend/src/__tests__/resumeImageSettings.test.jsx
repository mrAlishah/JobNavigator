import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { SectionEditor } from '../screens/ResumeSections'

describe('Professional image setting', () => {
  it('lives in Settings and is disabled for templates without an image', () => {
    const props = { data: { header: { name: 'A', contact_items: [] } }, setField: vi.fn(), mutate: vi.fn() }
    const { unmount } = render(<SectionEditor name="Header" {...props} />)
    expect(screen.queryByRole('checkbox', { name: /Show profile image/ })).toBeNull()
    unmount()
    render(<SectionEditor name="Settings" {...props} hasProfileImage={false} />)
    expect(screen.getByRole('checkbox', { name: /Show profile image/ }).getAttribute('aria-disabled')).toBe('true')
  })

  it('can toggle image visibility for Professional templates', () => {
    const setField = vi.fn()
    render(<SectionEditor name="Settings" data={{}} setField={setField} hasProfileImage />)
    fireEvent.click(screen.getByRole('checkbox', { name: /Show profile image/ }))
    expect(setField).toHaveBeenCalledWith('profile_image_enabled', false)
  })
})
