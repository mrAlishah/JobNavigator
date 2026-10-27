// Pure helpers for the Feed's Saved search menu and the Tracked companies group
// of the Company menu. They only shape lists; the filtering stays on the backend
// (`search_id`, and `company` expanded through the company aliases).

const lc = (s) => String(s || '').toLowerCase()

// One option per saved search, active or not. Same-name searches get their first four
// id characters appended so they can be told apart; the id itself is never changed.
export function searchOptions(searches) {
  const list = [...(searches || [])].sort((a, b) => lc(a.name).localeCompare(lc(b.name)))
  const seen = {}
  list.forEach((s) => { seen[lc(s.name)] = (seen[lc(s.name)] || 0) + 1 })
  return list.map((s) => ({
    id: String(s.id),
    label: seen[lc(s.name)] > 1 ? `${s.name} · ${String(s.id).slice(0, 4)}` : s.name,
    inactive: s.active === false,
  }))
}

// '' = no search picked; 'Search' = the list has not loaded yet; a deleted search
// (a stale ?search=) still gets a clearable label.
export function searchLabel(searches, id) {
  if (!searches) return 'Search'
  if (!id) return ''
  const s = searches.find((x) => String(x.id) === String(id))
  return s ? searchOptions(searches).find((o) => o.id === String(s.id)).label : 'Unavailable search'
}

// Set or clear ?search=<id> and leave every other query parameter alone.
export function withSearchParam(params, id) {
  const next = new URLSearchParams(params)
  if (id) next.set('search', id)
  else next.delete('search')
  return next
}

// Split the Company menu into the tracked companies (from /companies) and the
// job-derived facet rows. A tracked company's count is its facet rows summed over its
// name and aliases, because the backend matches jobs under all of them; 0 when it has
// no jobs, so it stays selectable. Facet rows named like a tracked company are dropped
// (no duplicate). Picked companies stay in the list whatever the query.
export function companyGroups(tracked, facet, picked, query) {
  const q = lc(query).trim()
  const byName = {}
  ;(facet || []).forEach((c) => { byName[lc(c.name)] = (byName[lc(c.name)] || 0) + (c.count || 0) })
  const isPicked = (name) => (picked || []).some((p) => lc(p) === lc(name))
  const shown = (name) => isPicked(name) || lc(name).includes(q)
  const trackedRows = (tracked || []).map((c) => ({
    name: c.name,
    count: [c.name, ...(c.aliases || [])].reduce((n, k) => n + (byName[lc(k)] || 0), 0),
    inactive: c.active === false,
  })).filter((c) => shown(c.name) && (c.count > 0 || isPicked(c.name)))
  const trackedNames = new Set((tracked || []).map((c) => lc(c.name)))
  const rest = (facet || []).filter((c) => !trackedNames.has(lc(c.name)) && shown(c.name))
    .sort((a, b) => isPicked(b.name) - isPicked(a.name))   // stable: keeps the backend's count order
  return { tracked: trackedRows, rest }
}
