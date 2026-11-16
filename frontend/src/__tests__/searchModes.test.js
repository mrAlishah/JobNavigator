// The dashboard's search-mode table (src/searchModes.js). Its equality with the backend's
// table is checked by backend/tests/test_search_modes.py; this covers the helpers.
import { describe, expect, it } from 'vitest'
import { MODE_OPTIONS, SEARCH_MODES, defaultSources, isExtensionMode, isTestable, modeBadge, needsQuery } from '../searchModes'

describe('searchModes', () => {
  it('offers every mode but the two built-in extension ones', () => {
    expect(MODE_OPTIONS.map(([k]) => k)).toEqual(SEARCH_MODES.filter((m) => !m.extension).map((m) => m.key))
    expect(MODE_OPTIONS.map(([k]) => k)).not.toContain('extension')
    expect(MODE_OPTIONS[0]).toEqual(['keyword', 'Keyword (JobSpy)'])
  })

  it('knows which modes are built in, testable or need a query', () => {
    expect(isExtensionMode('linkedin_extension')).toBe(true)
    expect(isExtensionMode('keyword')).toBe(false)
    expect(isTestable('trulyremote')).toBe(true)
    expect(isTestable('extension')).toBe(false)
    expect(isTestable('made_up')).toBe(false)
    expect(needsQuery('freehire')).toBe(true)
    expect(needsQuery('levels_fyi')).toBe(false)
  })

  it('treats an empty mode as keyword and shows an unknown one by its key', () => {
    expect(modeBadge('')).toEqual(['JOBSPY', 'sm-keyword'])
    expect(modeBadge('made_up')).toEqual(['MADE_UP', 'sm-extension'])
  })

  it('hands out a copy of the default sources', () => {
    const a = defaultSources('keyword')
    a.push('x')
    expect(defaultSources('keyword')).toEqual(['linkedin', 'indeed', 'zip_recruiter', 'google'])
    expect(defaultSources('levels_fyi')).toEqual([])
    expect(defaultSources('linkedin_personal')).toEqual(['recommended', 'top-applicant'])
  })
})
