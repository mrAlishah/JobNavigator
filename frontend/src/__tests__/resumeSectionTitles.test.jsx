import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { SectionEditor } from '../screens/ResumeSections'

describe('résumé section header title', () => {
  it.each(['Summary', 'Experience', 'Skills', 'Education', 'Projects', 'Publications'])('%s has a Header title input that writes section_titles', (name) => {
    const data = { section_titles: { other: 'keep' } }
    const mutate = vi.fn((fn) => fn(data))
    render(<SectionEditor name={name} data={data} setField={vi.fn()} mutate={mutate} />)
    fireEvent.change(screen.getByLabelText('Header title'), { target: { value: 'My title' } })
    expect(data.section_titles).toEqual({ other: 'keep', [name.toLowerCase()]: 'My title' })
  })

  it('Header and Settings have no Header title input', () => {
    for (const name of ['Header', 'Settings']) {
      const { unmount } = render(<SectionEditor name={name} data={{ header: { contact_items: [] } }} setField={vi.fn()} mutate={vi.fn()} />)
      expect(screen.queryByLabelText('Header title')).toBeNull()
      unmount()
    }
  })
})
