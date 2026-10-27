// Whether a job reaches the employer's own site (backend scraper/_shared/link_kinds.py), and
// whether its posting is still open (still_open.py): the feed's Link menu, the header tags,
// and the link Open uses. Easy Apply counts as Direct: you apply right there.
import { ago } from './time'

export const LINK_KINDS = [
  { key: 'direct', label: 'Direct', kinds: ['direct', 'easy_apply'] },
  { key: 'board', label: 'Board link', kinds: ['board'], title: "No link to the employer's own site yet" },
]

// The API's link_kind filter for the menu's picks.
export const linkKindParam = (picked) =>
  LINK_KINDS.filter((k) => (picked || []).includes(k.key)).flatMap((k) => k.kinds).join(',')

// { text, title } for a job with only a board link, else null.
export const linkTag = (job) => {
  const k = LINK_KINDS.find((x) => x.kinds.includes(job?.link_kind))
  return k && k.title ? { text: k.label, title: k.title } : null
}

// { text, title } when the last check found the posting closed, else null (open, unknown, unchecked).
export const closedTag = (job) => (job?.open_state === 'closed'
  ? { text: 'Closed posting', title: `Looks closed: ${job.open_reason || 'the posting is gone'} · checked ${ago(job.open_checked_at)} · click to check again` }
  : null)

// The employer's link when the job has one (`link`), else its own link.
export const openLink = (job) => (job && (job.link || job.url)) || ''
