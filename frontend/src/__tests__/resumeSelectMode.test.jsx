import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'

const TEMPLATES = [
  { id: 'professional-photo', name: 'Professional Photo', has_profile_image: true, docx: { style_overrides: true }, aliases: { 'professional-top-left-image': 'left', 'professional-top-right-image': 'right' } },   // the backend hides the legacy ids; the résumé below still stores one
  { id: 'modern', name: 'Modern', docx: {} },
]
const doc = { id: 'r1', name: 'CV', is_base: true, template: 'professional-top-left-image', page_format: 'letter', json_data: { header: { name: 'Dana', contact_items: [] } } }
const calls = []

vi.mock('../api', () => {
  const get = vi.fn(async (url, cfg) => {
    calls.push([url, cfg?.params])
    if (url === '/resumes/templates') return { data: TEMPLATES }
    if (url === '/resumes/r1') return { data: doc }
    if (url === '/resumes/r1/pdf') return { data: new ArrayBuffer(8) }
    if (url === '/resumes/r1/preview') return { data: '<html><body><div data-section="experience">x</div></body></html>' }
    return { data: url === '/settings' ? {} : [] }
  })
  return { default: { defaults: { baseURL: '/api' }, get, post: vi.fn(async () => ({ data: {} })), patch: vi.fn(async () => ({ data: {} })) } }
})

import ResumeEditor from '../screens/ResumeEditor'

const mount = () => render(<MemoryRouter initialEntries={['/resumes/r1']}><Routes><Route path="/resumes/:id" element={<ResumeEditor />} /></Routes></MemoryRouter>)
const pick = (frame, area) => act(() => { window.dispatchEvent(new MessageEvent('message', { data: { type: 'resume-select', area }, source: frame.contentWindow })) })

describe('click-to-select style preview', () => {
  beforeEach(() => { calls.length = 0; URL.createObjectURL = vi.fn(() => 'blob:x'); URL.revokeObjectURL = vi.fn() })

  it('swaps the PDF for a clickable preview and opens the selected area\'s properties', async () => {
    mount()
    fireEvent.click(await screen.findByRole('button', { name: 'Select & style' }))
    const frame = await screen.findByTitle('Select preview', {}, { timeout: 4000 })
    expect(frame.getAttribute('sandbox')).toBe('allow-scripts')
    expect(frame.getAttribute('srcdoc')).toContain('data-section="experience"')
    expect(calls.find(([u]) => u === '/resumes/r1/preview')[1]).toMatchObject({ template: 'professional-top-left-image', select: true })

    pick(frame, 'experience')
    expect(await screen.findByLabelText('Experience size (pt)')).toBeTruthy()
    expect(screen.queryByLabelText('Summary size (pt)')).toBeNull()

    pick(frame, 'experience.bullets')
    expect(await screen.findByText('Experience › Bullets')).toBeTruthy()
    expect(await screen.findByLabelText('Part size (pt)')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'Select whole section' }))
    expect(await screen.findByLabelText('Experience size (pt)')).toBeTruthy()
    expect(screen.queryByLabelText('Part size (pt)')).toBeNull()

    pick(frame, 'page')
    expect(await screen.findByLabelText('Margin top (in)')).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Close' }))
    expect(screen.queryByLabelText('Margin top (in)')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Back to PDF' }))
    expect(screen.queryByTitle('Select preview')).toBeNull()
  })

  it('ignores messages from other windows and unknown areas', async () => {
    mount()
    fireEvent.click(await screen.findByRole('button', { name: 'Select & style' }))
    const frame = await screen.findByTitle('Select preview', {}, { timeout: 4000 })
    pick({ contentWindow: window }, 'experience')
    pick(frame, 'settings')
    pick(frame, 'nope.x')
    expect(screen.queryByLabelText('Experience size (pt)')).toBeNull()
  })
})
