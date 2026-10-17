// QA V-05: the Feed disables "Score N unscored jobs" / "Score this role" with the
// hint "Add a base résumé to score jobs" only when the account has nothing to
// score against — and never while that is still unknown.
import { describe, it, expect } from 'vitest'
import { NO_BASES_HINT, noScoreSource } from '../screens/feedActivity'

const base = { basesLoaded: true, bases: [], personaLoaded: true, personaHasContent: false }

describe('noScoreSource', () => {
  it('is true with no base résumé and an empty Persona', () => {
    expect(noScoreSource(base)).toBe(true)
    expect(NO_BASES_HINT).toBe('Add a base résumé to score jobs')
  })
  it('is false with a base résumé', () => {
    expect(noScoreSource({ ...base, bases: [{ id: 'r1', name: 'PM' }] })).toBe(false)
  })
  it('is false when the Persona has résumé content (it is a score source too)', () => {
    expect(noScoreSource({ ...base, personaHasContent: true })).toBe(false)
  })
  it('is false while either list is still loading or failed', () => {
    expect(noScoreSource({ ...base, basesLoaded: false })).toBe(false)
    expect(noScoreSource({ ...base, personaLoaded: false })).toBe(false)
    expect(noScoreSource({ ...base, bases: undefined })).toBe(false)
  })
})
