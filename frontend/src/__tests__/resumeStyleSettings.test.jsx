import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { SectionEditor, StyleSettings, STYLE_AREAS, styleAreaLabel } from '../screens/ResumeSections'

// mutate(fn) spy that applies fn to a copy of `data` and returns the result
const harness = (data = {}) => {
  const mutate = vi.fn()
  const result = () => { const d = JSON.parse(JSON.stringify(data)); mutate.mock.lastCall[0](d); return d }
  return { mutate, result }
}

describe('résumé style settings', () => {
  it('keeps only the two checkboxes in the Settings section', () => {
    render(<SectionEditor name="Settings" data={{}} setField={vi.fn()} mutate={vi.fn()} hasProfileImage />)
    expect(screen.getAllByRole('checkbox')).toHaveLength(2)
    expect(screen.getByRole('checkbox', { name: /Show profile image/ })).toBeTruthy()
    expect(screen.getByRole('checkbox', { name: /Show footer/ })).toBeTruthy()
    expect(screen.queryByLabelText('Margin top (in)')).toBeNull()
    expect(screen.queryByLabelText('Experience size (pt)')).toBeNull()
    expect(screen.queryByLabelText('Image width (px)')).toBeNull()
  })

  describe('scoped to a selected area', () => {
    const scoped = (only, data = {}) => { const h = harness(data); render(<StyleSettings data={data} mutate={h.mutate} hasProfileImage only={only} />); return h }

    it('shows only the selected section', () => {
      scoped('experience')
      expect(screen.getByLabelText('Experience size (pt)')).toBeTruthy()
      expect(screen.queryByLabelText('Summary size (pt)')).toBeNull()
      expect(screen.queryByLabelText('Margin top (in)')).toBeNull()
    })

    it('shows margins for the page and the size for the image', () => {
      const { unmount } = render(<StyleSettings data={{}} mutate={vi.fn()} only="page" />)
      expect(screen.getByLabelText('Margin top (in)')).toBeTruthy()
      expect(screen.queryByLabelText('Image width (px)')).toBeNull()
      unmount()
      render(<StyleSettings data={{}} mutate={vi.fn()} only="image" />)
      expect(screen.getByLabelText('Image width (px)')).toBeTruthy()
    })

    it('writes a section\'s margin and padding next to its text settings', () => {
      const { result } = scoped('experience', { style: { sections: { experience: { size: 11 } } } })
      fireEvent.change(screen.getByLabelText('Section margin top (pt)'), { target: { value: '4' } })
      expect(result().style.sections.experience).toEqual({ size: 11, margin_top: 4 })
      fireEvent.change(screen.getByLabelText('Section padding left (pt)'), { target: { value: '6' } })
      expect(result().style.sections.experience).toEqual({ size: 11, padding_left: 6 })
    })

    it('disables the image size without a profile image', () => {
      render(<StyleSettings data={{}} mutate={vi.fn()} hasProfileImage={false} only="image" />)
      expect(screen.getByLabelText('Image width (px)').disabled).toBe(true)
    })

    it('resets only the selected area', () => {
      const data = { style: { page: { top: 1 }, sections: { experience: { size: 11 }, summary: { size: 9 } } } }
      const { result } = scoped('experience', data)
      fireEvent.click(screen.getByRole('button', { name: /Reset/ }))
      expect(result().style).toEqual({ page: { top: 1 }, sections: { summary: { size: 9 } } })
    })
  })

  it('offers Manrope, the font that ships with the templates, for sections and parts', () => {
    const { unmount } = render(<StyleSettings data={{}} mutate={vi.fn()} only="experience" />)
    fireEvent.click(screen.getByLabelText('Experience font'))
    expect(screen.getByRole('option', { name: 'Manrope' })).toBeTruthy()
    unmount()
    render(<StyleSettings data={{}} mutate={vi.fn()} only="experience.title" />)
    fireEvent.click(screen.getByLabelText('Part font'))
    expect(screen.getByRole('option', { name: 'Manrope' })).toBeTruthy()
  })

  describe('part properties', () => {
    const KEY = 'experience.bullets'
    const part = (key, data = {}) => { const h = harness(data); const view = render(<StyleSettings data={data} mutate={h.mutate} only={key} />); return { ...h, unmount: view.unmount, parts: () => h.result().style.parts[key] } }
    const pickOption = (label, option) => { fireEvent.click(screen.getByLabelText(label)); fireEvent.click(screen.getByRole('option', { name: option })) }

    it('knows every area and labels parts with a breadcrumb', () => {
      expect(STYLE_AREAS).toEqual(expect.arrayContaining(['page', 'image', 'header', 'header.name', 'experience.bullets', 'skills.heading', 'publications.description']))
      expect(styleAreaLabel('experience.bullets')).toBe('Experience › Bullets')
      expect(styleAreaLabel('experience')).toBe('Experience section')
      expect(STYLE_AREAS).toEqual(expect.arrayContaining(['experience.entry', 'education.entry', 'projects.entry', 'publications.entry']))
      expect(styleAreaLabel('education.entry')).toBe('Education › Entry')
    })

    it('writes text properties under style.parts', () => {
      const { parts } = part(KEY)
      fireEvent.change(screen.getByLabelText('Part size (pt)'), { target: { value: '11' } })
      fireEvent.change(screen.getByLabelText('Part line height'), { target: { value: '1.3' } })
      fireEvent.change(screen.getByLabelText('Part letter spacing (pt)'), { target: { value: '0.5' } })
      fireEvent.change(screen.getByLabelText('Part color'), { target: { value: '#112233' } })
      expect(parts()).toMatchObject({ color: '#112233' })
      pickOption('Part font', 'Georgia'); expect(parts().font).toBe('Georgia')
      pickOption('Part bold', 'Bold'); expect(parts().bold).toBe(true)
      pickOption('Part italic', 'Normal'); expect(parts().italic).toBe(false)
      pickOption('Part align', 'Justify'); expect(parts().align).toBe('justify')
      pickOption('Part case', 'UPPERCASE'); expect(parts().case).toBe('upper')
    })

    it('adds to the part without touching its other values', () => {
      const { parts } = part(KEY, { style: { parts: { [KEY]: { size: 11 } } } })
      fireEvent.change(screen.getByLabelText('Part margin top (pt)'), { target: { value: '4' } })
      expect(parts()).toEqual({ size: 11, margin_top: 4 })
      fireEvent.change(screen.getByLabelText('Part padding left (pt)'), { target: { value: '6' } })
      expect(parts()).toEqual({ size: 11, padding_left: 6 })      // the spy re-applies to the unchanged data; the app re-renders in between
    })

    it('clearing a value removes it, and an emptied part leaves no style key', () => {
      const { result } = part(KEY, { style: { parts: { [KEY]: { size: 11 } } } })
      fireEvent.change(screen.getByLabelText('Part size (pt)'), { target: { value: '' } })
      expect(result().style).toBeUndefined()
    })

    it('shows bullet options only for bullets, rule options only for headings', () => {
      const { unmount } = part(KEY)
      expect(screen.getByLabelText('Part bullet')).toBeTruthy()
      expect(screen.getByLabelText('Part indent (pt)')).toBeTruthy()
      expect(screen.queryByLabelText('Part rule width (pt)')).toBeNull()
      unmount()
      const { unmount: u2 } = part('experience.heading')
      expect(screen.getByLabelText('Part rule width (pt)')).toBeTruthy()
      expect(screen.getByLabelText('Part rule color')).toBeTruthy()
      expect(screen.queryByLabelText('Part bullet')).toBeNull()
      u2()
      part('experience.title')
      expect(screen.queryByLabelText('Part bullet')).toBeNull()
      expect(screen.queryByLabelText('Part rule width (pt)')).toBeNull()
    })

    it('writes the bullet character and resets only this part', () => {
      const data = { style: { parts: { [KEY]: { size: 9 }, 'experience.title': { size: 12 } } } }
      const { parts, result } = part(KEY, data)
      pickOption('Part bullet', '–'); expect(parts().bullet).toBe('–')
      fireEvent.click(screen.getByRole('button', { name: /Reset style/ }))
      expect(result().style.parts).toEqual({ 'experience.title': { size: 12 } })
    })
  })
})
