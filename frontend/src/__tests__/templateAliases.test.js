// The editor's view of the legacy Professional ids: the backend's template list says which ids a template answers to
// (`aliases`: id -> default photo side), so the editor holds no copy of the mapping.
import { describe, it, expect } from 'vitest'
import { DEFAULT_IMAGE_SIDE, IMAGE_SIDES, dataWithSavedSide, effectiveImageSide, templateFor } from '../templateAliases'

const PHOTO = { id: 'professional-photo', name: 'Professional Photo', has_profile_image: true, aliases: { 'professional-top-left-image': 'left', 'professional-top-right-image': 'right' } }
const MODERN = { id: 'modern', name: 'Modern' }
const LIST = [MODERN, PHOTO]

describe('templateFor', () => {
  it('finds a template by its id, and the photo template by either legacy id', () => {
    expect(templateFor(LIST, 'modern')).toBe(MODERN)
    expect(templateFor(LIST, 'professional-photo')).toBe(PHOTO)
    expect(templateFor(LIST, 'professional-top-left-image')).toBe(PHOTO)
    expect(templateFor(LIST, 'professional-top-right-image')).toBe(PHOTO)
  })

  it('finds nothing for an unknown, empty or missing id, or before the list has loaded', () => {
    for (const id of ['nope', 'professional-top-up-image', '', null, undefined]) expect(templateFor(LIST, id)).toBeUndefined()
    expect(templateFor([], 'professional-top-left-image')).toBeUndefined()
    expect(templateFor(undefined, 'modern')).toBeUndefined()
  })

  it('does not mistake an object property for an alias', () => {
    expect(templateFor(LIST, 'constructor')).toBeUndefined()
    expect(templateFor(LIST, 'toString')).toBeUndefined()
  })
})

describe('effectiveImageSide', () => {
  it('uses the saved side first', () => {
    expect(effectiveImageSide(LIST, 'professional-top-left-image', { profile_image_position: 'right' })).toBe('right')
    expect(effectiveImageSide(LIST, 'professional-top-right-image', { profile_image_position: 'left' })).toBe('left')
    expect(effectiveImageSide(LIST, 'professional-photo', { profile_image_position: 'left' })).toBe('left')
  })

  it('then the legacy id\'s own side, then right', () => {
    expect(effectiveImageSide(LIST, 'professional-top-left-image', {})).toBe('left')
    expect(effectiveImageSide(LIST, 'professional-top-right-image', {})).toBe('right')
    expect(effectiveImageSide(LIST, 'professional-photo', {})).toBe('right')
    expect(effectiveImageSide(LIST, 'professional-photo', undefined)).toBe('right')
  })

  it('ignores a saved value that is not a side', () => {
    for (const junk of ['up', '', 'LEFT', null, 5, ['left']]) {
      expect(effectiveImageSide(LIST, 'professional-top-left-image', { profile_image_position: junk })).toBe('left')
      expect(effectiveImageSide(LIST, 'professional-photo', { profile_image_position: junk })).toBe('right')
    }
  })

  it('exports the two sides and the default', () => {
    expect(IMAGE_SIDES).toEqual(['left', 'right'])
    expect(DEFAULT_IMAGE_SIDE).toBe('right')
  })
})

describe('dataWithSavedSide (a legacy résumé being switched keeps its side)', () => {
  it('saves the legacy side in a copy when none is saved', () => {
    const data = { summary: 's' }
    const out = dataWithSavedSide(LIST, 'professional-top-left-image', data)
    expect(out).toEqual({ summary: 's', profile_image_position: 'left' })
    expect(data).toEqual({ summary: 's' })
    expect(dataWithSavedSide(LIST, 'professional-top-right-image', {}).profile_image_position).toBe('right')
  })

  it('returns the very same object when there is nothing to save', () => {
    const saved = { profile_image_position: 'right' }
    expect(dataWithSavedSide(LIST, 'professional-top-left-image', saved)).toBe(saved)       // an explicit choice stays
    const plain = { summary: 's' }
    expect(dataWithSavedSide(LIST, 'professional-photo', plain)).toBe(plain)
    expect(dataWithSavedSide(LIST, 'modern', plain)).toBe(plain)
    expect(dataWithSavedSide([], 'professional-top-left-image', plain)).toBe(plain)         // the list has not loaded: nothing is known
  })
})
