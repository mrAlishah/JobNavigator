// Link kinds (backend scraper/_shared/link_kinds.py) and the still-open marker (still_open.py).
import { describe, it, expect } from 'vitest'
import { LINK_KINDS, closedTag, linkKindParam, linkTag, openLink } from '../linkKinds'

describe('link kinds', () => {
  it('offers Direct and Board link; Easy Apply counts as Direct', () => {
    expect(LINK_KINDS.map((k) => [k.key, k.label])).toEqual([['direct', 'Direct'], ['board', 'Board link']])
    expect(linkKindParam(['direct'])).toBe('direct,easy_apply')
    expect(linkKindParam(['board'])).toBe('board')
    expect(linkKindParam([])).toBe('')
  })

  it('tags only board links', () => {
    expect(linkTag({ link_kind: 'direct' })).toBeNull()
    expect(linkTag({ link_kind: 'easy_apply' })).toBeNull()
    expect(linkTag({ link_kind: null })).toBeNull()
    expect(linkTag({ link_kind: 'board' })).toEqual({ text: 'Board link', title: "No link to the employer's own site yet" })
  })

  it('opens the employer link when there is one', () => {
    expect(openLink({ url: 'https://www.linkedin.com/jobs/view/1', link: 'https://boards.greenhouse.io/a/jobs/2' }))
      .toBe('https://boards.greenhouse.io/a/jobs/2')
    expect(openLink({ url: 'https://www.linkedin.com/jobs/view/1', link: null })).toBe('https://www.linkedin.com/jobs/view/1')
    expect(openLink(null)).toBe('')
  })
})

describe('still open', () => {
  it('marks only a closed posting, with why and when', () => {
    expect(closedTag({ open_state: 'open' })).toBeNull()
    expect(closedTag({ open_state: 'unknown' })).toBeNull()
    expect(closedTag(null)).toBeNull()
    const t = closedTag({ open_state: 'closed', open_reason: 'the page returns 404', open_checked_at: new Date().toISOString() })
    expect(t.text).toBe('Closed posting')
    expect(t.title).toMatch(/^Looks closed: the page returns 404 · checked .* · click to check again$/)
  })
})
