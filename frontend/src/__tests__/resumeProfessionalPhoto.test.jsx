// The résumé editor with the merged Professional template: one "Professional Photo" in the picker, old résumés shown
// as using it on their old side, the photo side saved per résumé, and no save path that loses it.
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'

const LEFT = 'professional-top-left-image'
const RIGHT = 'professional-top-right-image'
const PHOTO = { id: 'professional-photo', name: 'Professional Photo', has_profile_image: true, docx: { style_overrides: true }, aliases: { [LEFT]: 'left', [RIGHT]: 'right' } }
const MODERN = { id: 'modern', name: 'Modern', has_profile_image: false, docx: {} }
const state = { doc: null }
const calls = []

vi.mock('../api', () => {
  const get = vi.fn(async (url, cfg) => {
    calls.push([url, cfg?.params])
    if (url === '/resumes/templates') return { data: [MODERN, PHOTO] }            // the backend hides the legacy ids
    if (url === '/resumes/r1') return { data: state.doc }
    if (url === '/resumes/r1/pdf') return { data: new ArrayBuffer(8) }
    if (url === '/resumes/r1/preview') return { data: '<html><body></body></html>' }
    return { data: url === '/settings' ? {} : [] }
  })
  return { default: { defaults: { baseURL: '/api' }, get, post: vi.fn(async () => ({ data: {} })), patch: vi.fn(async () => ({ data: {} })) } }
})

import api from '../api'
import ResumeEditor from '../screens/ResumeEditor'

const makeDoc = (template, json_data = {}) => ({ id: 'r1', name: 'CV', is_base: true, template, page_format: 'letter', json_data: { header: { name: 'Dana', contact_items: [] }, ...json_data } })
const mount = (template, json_data) => {
  state.doc = makeDoc(template, json_data)
  localStorage.setItem('jobnavigator_v2_resume_sections', JSON.stringify(['Settings']))
  return render(<MemoryRouter initialEntries={['/resumes/r1']}><Routes><Route path="/resumes/:id" element={<ResumeEditor />} /></Routes></MemoryRouter>)
}
const picker = async () => fireEvent.click(await screen.findByTitle('Résumé template'))
const sides = async () => within(await screen.findByRole('radiogroup', { name: /Photo position/ }))
const checked = async () => (await sides()).getAllByRole('radio').find((r) => r.getAttribute('aria-checked') === 'true').textContent
const lastPatch = async () => { await waitFor(() => expect(api.patch).toHaveBeenCalled(), { timeout: 3000 }); return api.patch.mock.calls.at(-1) }

describe('Professional Photo in the résumé editor', () => {
  beforeEach(() => { calls.length = 0; api.patch.mockClear(); localStorage.clear(); URL.createObjectURL = vi.fn(() => 'blob:x'); URL.revokeObjectURL = vi.fn() })

  it('offers only "Professional Photo": the legacy names are not selectable templates', async () => {
    mount('modern')
    await picker()
    const options = within(await screen.findByRole('listbox', { name: 'Résumé template' })).getAllByRole('option')
    expect(options.map((o) => o.textContent)).toEqual(['Modern', 'Professional Photo'])
    expect(screen.queryByText(/Top-Left|Top-Right|professional-top/)).toBeNull()
  })

  it.each([[LEFT], [RIGHT], ['professional-photo']])('shows a résumé stored as %s as using Professional Photo', async (id) => {
    mount(id)
    const trigger = await screen.findByTitle('Résumé template')
    expect(trigger.textContent).toContain('Professional Photo')
    expect(trigger.textContent).not.toContain('professional-top')
    await picker()
    const selected = (await screen.findAllByRole('option')).filter((o) => o.getAttribute('aria-selected') === 'true')
    expect(selected.map((o) => o.textContent)).toEqual(['Professional Photo'])
  })

  it.each([[LEFT, 'Left'], [RIGHT, 'Right'], ['professional-photo', 'Right']])('selects the photo side of a résumé stored as %s: %s', async (id, side) => {
    mount(id)
    expect(await checked()).toBe(side)
  })

  it('a saved side beats the legacy default in the control', async () => {
    mount(LEFT, { profile_image_position: 'right' })
    expect(await checked()).toBe('Right')
  })

  it('keeps Select & style for a résumé that stores a legacy id', async () => {
    mount(LEFT)
    expect(await screen.findByRole('button', { name: 'Select & style' })).toBeTruthy()
  })

  it('asks the backend for the id the résumé stores, never a rewritten one, and saves nothing on load', async () => {
    mount(LEFT)
    await screen.findByRole('radiogroup', { name: /Photo position/ })
    await waitFor(() => expect(calls.find(([u]) => u === '/resumes/r1/pdf')).toBeTruthy(), { timeout: 3000 })
    expect(calls.find(([u]) => u === '/resumes/r1/pdf')[1]).toMatchObject({ template: LEFT })
    await new Promise((resolve) => setTimeout(resolve, 800))
    expect(api.patch).not.toHaveBeenCalled()                                         // loading an old résumé rewrites nothing
  })

  it('saves a picked side in json_data and leaves the template id alone', async () => {
    mount(LEFT)
    fireEvent.click(within(await (async () => (await screen.findByRole('radiogroup', { name: /Photo position/ })))()).getByRole('radio', { name: 'Right' }))
    const [url, body] = await lastPatch()
    expect(url).toBe('/resumes/r1')
    expect(body).toEqual({ json_data: expect.objectContaining({ profile_image_position: 'right' }) })
    expect(body).not.toHaveProperty('template')
    expect(await checked()).toBe('Right')
  })

  it.each([[LEFT, 'left'], [RIGHT, 'right']])('switching a %s résumé to Professional Photo saves its side with the template, in one patch', async (id, side) => {
    mount(id)
    await picker()
    fireEvent.click(await screen.findByRole('option', { name: 'Professional Photo' }))
    const [, body] = await lastPatch()
    expect(body).toEqual({ template: 'professional-photo', json_data: expect.objectContaining({ profile_image_position: side }) })
    expect(api.patch).toHaveBeenCalledTimes(1)
    expect(await checked()).toBe(side === 'left' ? 'Left' : 'Right')
  })

  it('switching a legacy résumé to another template keeps the side in its data too', async () => {
    mount(RIGHT)
    await picker()
    fireEvent.click(await screen.findByRole('option', { name: 'Modern' }))
    const [, body] = await lastPatch()
    expect(body).toEqual({ template: 'modern', json_data: expect.objectContaining({ profile_image_position: 'right' }) })
  })

  it('switching a résumé that already has a side, or one that is not legacy, adds nothing', async () => {
    mount(LEFT, { profile_image_position: 'right' })
    await picker()
    fireEvent.click(await screen.findByRole('option', { name: 'Modern' }))
    expect(await lastPatch()).toEqual(['/resumes/r1', { template: 'modern' }])
  })

  it('a side picked right after a template switch is saved together with it (one pending patch, not two)', async () => {
    mount(LEFT)
    await picker()
    fireEvent.click(await screen.findByRole('option', { name: 'Professional Photo' }))
    fireEvent.click((await sides()).getByRole('radio', { name: 'Right' }))
    const [, body] = await lastPatch()
    expect(body).toEqual({ template: 'professional-photo', json_data: expect.objectContaining({ profile_image_position: 'right' }) })
    expect(api.patch).toHaveBeenCalledTimes(1)
  })

  it('an edit made right after a template switch does not cancel the switch', async () => {
    mount('modern')
    await picker()
    fireEvent.click(await screen.findByRole('option', { name: 'Professional Photo' }))
    fireEvent.click((await sides()).getByRole('radio', { name: 'Left' }))
    const [, body] = await lastPatch()
    expect(body.template).toBe('professional-photo')
    expect(body.json_data.profile_image_position).toBe('left')
  })

  it('keeps the position control but disables it while the picture is hidden', async () => {
    mount(LEFT, { profile_image_enabled: false })
    const group = await sides()
    for (const radio of group.getAllByRole('radio')) expect(radio.getAttribute('aria-disabled')).toBe('true')
  })

  it('does not offer the position control for a template without a picture', async () => {
    mount('modern')
    await screen.findByRole('switch', { name: /Profile image/ })
    expect(screen.queryByRole('radiogroup', { name: /Photo position/ })).toBeNull()
  })
})
