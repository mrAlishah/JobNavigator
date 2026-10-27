// Feed scope filters: the Saved search menu and the Tracked companies group.
import { describe, it, expect } from 'vitest'
import { companyGroups, searchLabel, searchOptions, withSearchParam } from '../screens/feedScopes'

const S = (id, name, active = true) => ({ id, name, active })

describe('searchOptions / searchLabel', () => {
  it('lists every search by name, active and inactive, keeping the id', () => {
    const o = searchOptions([S('b-2222', 'Zeta'), S('a-1111', 'alpha', false)])
    expect(o.map((x) => x.id)).toEqual(['a-1111', 'b-2222'])
    expect(o.map((x) => x.label)).toEqual(['alpha', 'Zeta'])
    expect(o[0].inactive).toBe(true)
  })
  it('tells searches with the same name apart without touching their ids', () => {
    const o = searchOptions([S('aaaa1111', 'PM'), S('bbbb2222', 'PM'), S('cccc3333', 'Dev')])
    expect(o.filter((x) => x.label.startsWith('PM')).map((x) => x.label)).toEqual(['PM · aaaa', 'PM · bbbb'])
    expect(o.find((x) => x.label === 'Dev').id).toBe('cccc3333')
  })
  it('labels the pick, and a missing search safely', () => {
    const list = [S('a1', 'Alpha')]
    expect(searchLabel(list, 'a1')).toBe('Alpha')
    expect(searchLabel(list, 'gone')).toBe('Unavailable search')
    expect(searchLabel(list, '')).toBe('')
    expect(searchLabel(null, 'a1')).toBe('Search')   // list not loaded yet
  })
})

describe('withSearchParam', () => {
  it('sets and clears only ?search, keeping the other params', () => {
    const p = new URLSearchParams('company=Acme&job=7')
    expect(withSearchParam(p, 's1').toString()).toBe('company=Acme&job=7&search=s1')
    expect(withSearchParam(new URLSearchParams('search=s1&job=7'), '').toString()).toBe('job=7')
  })
})

describe('companyGroups', () => {
  const facet = [{ name: 'Amazon', count: 3 }, { name: 'AWS', count: 2 }, { name: 'Other Co', count: 5 }]
  const tracked = [
    { name: 'amazon', aliases: ['AWS', 'Audible'], active: true },
    { name: 'Idle Inc', aliases: [], active: false },
  ]
  it('pins tracked companies, merging aliases into their count and de-duplicating by name', () => {
    const g = companyGroups(tracked, facet, [], '')
    expect(g.tracked).toEqual([
      { name: 'amazon', count: 5, inactive: false },
      { name: 'Idle Inc', count: 0, inactive: true },
    ])
    expect(g.rest.map((c) => c.name)).toEqual(['AWS', 'Other Co'])
  })
  it('keeps picked companies visible whatever the query, and filters the rest', () => {
    const g = companyGroups(tracked, facet, ['Idle Inc'], 'oth')
    expect(g.tracked.map((c) => c.name)).toEqual(['Idle Inc'])
    expect(g.rest.map((c) => c.name)).toEqual(['Other Co'])
  })
  it('works with no tracked companies (not loaded / none)', () => {
    expect(companyGroups(null, facet, [], '').rest).toHaveLength(3)
  })
})
