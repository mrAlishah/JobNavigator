// rowSelect.js — the pure part of a list's bulk selection (the Feed's gestures,
// shared with the Applications list). The two invariants these tests protect:
// a ⇧-range walks the ids the screen actually PAINTED (so a collapsed group is
// not in it), and a refresh may drop ids that are gone but must never widen the
// selection or churn the Set identity for nothing.
import { describe, it, expect } from 'vitest'
import { PICK_KEY, clickMods, clickSelection, pruneSelection } from '../screens/rowSelect'

const ids = ['a', 'b', 'c', 'd', 'e']
const click = (over = {}) => clickSelection({ checked: new Set(), ids, anchor: null, pick: false, range: false, ...over })
const out = (r) => [...r.checked]

describe('clickSelection', () => {
  it('a plain click focuses the row and drops the selection', () => {
    const r = click({ checked: new Set(['a', 'b']), index: 3 })
    expect(out(r)).toEqual([])
    expect(r.focus).toBe(true)
    expect(r.anchor).toBe(3)
  })

  it('⌘/Ctrl adds a row without focusing it, and moves the anchor', () => {
    const r = click({ index: 1, pick: true })
    expect(out(r)).toEqual(['b'])
    expect(r.focus).toBe(false)
    expect(r.anchor).toBe(1)
  })

  it('⌘/Ctrl on an already-picked row takes it back out', () => {
    const r = click({ checked: new Set(['a', 'b']), index: 1, pick: true })
    expect(out(r)).toEqual(['a'])
  })

  it('⇧ selects the whole run between the anchor and the row, in list order', () => {
    const r = click({ checked: new Set(['a']), index: 3, anchor: 1, range: true })
    expect(out(r).sort()).toEqual(['a', 'b', 'c', 'd'])
    expect(r.focus).toBe(false)
  })

  it('⇧ works backwards, and leaves the anchor where it was', () => {
    const r = click({ index: 0, anchor: 2, range: true })
    expect(out(r).sort()).toEqual(['a', 'b', 'c'])
    expect(r.anchor).toBe(2)
  })

  it('⇧ ranges over the ids it is given — a list that hides rows cannot be reached through', () => {
    // 'c' is in a collapsed group, so the painted order is a,b,d,e and a range
    // from a to d covers three rows, not four.
    const r = clickSelection({ checked: new Set(), ids: ['a', 'b', 'd', 'e'], index: 2, anchor: 0, range: true, pick: false })
    expect(out(r).sort()).toEqual(['a', 'b', 'd'])
  })

  it('⇧ with no anchor yet behaves as a plain click, not a one-row range', () => {
    const r = click({ checked: new Set(['a']), index: 2, anchor: null, range: true })
    expect(out(r)).toEqual([])
    expect(r.focus).toBe(true)
  })

  it('⌘ wins over ⇧ when both are held', () => {
    const r = click({ index: 4, anchor: 0, pick: true, range: true })
    expect(out(r)).toEqual(['e'])
  })

  it('a click past the end of the list selects nothing and still focuses', () => {
    const r = click({ checked: new Set(['a']), index: 9, pick: true })
    expect(out(r)).toEqual(['a'])       // nothing to toggle
    const plain = click({ index: 9 })
    expect(plain.focus).toBe(true)
  })
})

describe('clickMods', () => {
  it('reads ⌘ and Ctrl as the same pick modifier', () => {
    expect(clickMods({ metaKey: true })).toEqual({ pick: true, range: false })
    expect(clickMods({ ctrlKey: true })).toEqual({ pick: true, range: false })
    expect(clickMods({ shiftKey: true })).toEqual({ pick: false, range: true })
    expect(clickMods(undefined)).toEqual({ pick: false, range: false })
  })
})

describe('pruneSelection', () => {
  it('drops ids the list no longer has', () => {
    expect([...pruneSelection(new Set(['a', 'x']), ids)]).toEqual(['a'])
  })

  it('returns the same Set when nothing was dropped, so a refresh costs no render', () => {
    const cur = new Set(['a', 'b'])
    expect(pruneSelection(cur, ids)).toBe(cur)
    const empty = new Set()
    expect(pruneSelection(empty, [])).toBe(empty)
  })
})

describe('PICK_KEY', () => {
  it('names one of the two modifiers', () => {
    expect(['⌘', 'Ctrl']).toContain(PICK_KEY)
  })
})


describe('the open row joins a first modified click', () => {
  const ids = ['a', 'b', 'c', 'd']
  it('⌘-click with the detail open selects the open row too', () => {
    const r = clickSelection({ checked: new Set(), ids, index: 2, anchor: null, pick: true, range: false, focused: 0 })
    expect([...r.checked].sort()).toEqual(['a', 'c'])
    expect(r.anchor).toBe(2)
  })
  it('⇧-click with the detail open ranges from the open row', () => {
    const r = clickSelection({ checked: new Set(), ids, index: 3, anchor: null, pick: false, range: true, focused: 1 })
    expect([...r.checked].sort()).toEqual(['b', 'c', 'd'])
  })
  it('⌘-click on the open row itself just picks it', () => {
    const r = clickSelection({ checked: new Set(), ids, index: 1, anchor: null, pick: true, range: false, focused: 1 })
    expect([...r.checked]).toEqual(['b'])
  })
  it('an existing selection is left alone', () => {
    const r = clickSelection({ checked: new Set(['d']), ids, index: 2, anchor: 3, pick: true, range: false, focused: 0 })
    expect([...r.checked].sort()).toEqual(['c', 'd'])
  })
})
