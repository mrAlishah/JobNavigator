// The boards an Apify search can pick. The backend owns the list
// (PRESETS in backend/scraper/sources/apify.py, served by GET /searches/apify-boards),
// so a new board needs no frontend change. One fetch per page load, shared by
// both UIs; a failed fetch is retried on the next mount.
import { useEffect, useState } from 'react'
import api from './api'

let cache = null
let pending = null

const load = () => {
  if (cache) return Promise.resolve(cache)
  pending = pending || api.get('/searches/apify-boards')
    .then(({ data }) => { cache = Array.isArray(data) ? data : []; return cache })
    .catch(() => { pending = null; return [] })
  return pending
}

/** [{ value, label, hint, actor }, ...] in backend order; [] until the fetch lands. */
export function useApifyBoards() {
  const [boards, setBoards] = useState(cache || [])
  useEffect(() => {
    if (!cache) load().then(setBoards)
  }, [])
  return boards
}

/** "apify_indeed" -> "Indeed"; an unknown key comes back as is. Board keys carry the
 *  `apify_` prefix so they never collide with the JobSpy boards ("indeed", "linkedin"). */
export const boardLabel = (boards, key) => (boards.find((b) => b.value === key) || {}).label || key

/** Job.source of an Apify job is its board key, `apify_<board>`: "apify_indeed" -> "Indeed (Apify)",
 *  a board this page has not heard of -> "<board> (Apify)", any other source -> null. */
export const apifySourceLabel = (boards, source, suffix = ' (Apify)') => {
  const s = String(source || '')
  if (!s.startsWith('apify_')) return null
  const b = boards.find((x) => x.value === s)
  return `${b ? b.label : s.slice(6)}${suffix}`
}

/** Estimated and capped cost of one Test run over `keys`: { est, max } in USD, from each
 *  board's preview_limit × price_per_1k and the spend cap the backend sends (test_max_usd). */
export const testCost = (boards, keys) => (keys || []).reduce((acc, k) => {
  const b = boards.find((x) => x.value === k)
  if (!b) return acc
  return { est: acc.est + (b.preview_limit || 0) * (b.price_per_1k || 0) / 1000, max: acc.max + (b.test_max_usd || 0) }
}, { est: 0, max: 0 })

/** Dollars, rounded up to whole cents (float noise dropped first: 0.1 + 0.18 is $0.28). */
export const usd = (n) => `$${(Math.ceil(Number((n * 100).toFixed(6))) / 100).toFixed(2)}`
