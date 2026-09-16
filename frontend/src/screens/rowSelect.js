// rowSelect.js — the list-row *selection* mechanics, pure and unit-tested.
//
// Two screens carry a bulk selection over a list of rows (the Feed, and the
// Applications list), and both answer the same three gestures:
//
//   plain click   → focus that row, drop the selection
//   ⌘/Ctrl click  → toggle that row, leave the rest alone, move the anchor
//   ⇧ click       → add everything between the anchor and this row, in LIST order
//
// "List order" is the order the rows are painted in, which on Applications is
// the flattened stage groups, not the raw array — the caller passes the ids it
// actually rendered, so a collapsed group simply is not part of a range.
//
// Nothing here touches React: a click answers a new `{ checked, anchor, focus }`
// and the screen decides what to do with it.

const IS_MAC = typeof navigator !== 'undefined'
  && /Mac|iPhone|iPad|iPod/i.test(navigator.platform || navigator.userAgent || '')
// The modifier's own name, for the "⇧ range · ⌘ pick" hint above a list.
export const PICK_KEY = IS_MAC ? '⌘' : 'Ctrl'

/** Read the two modifiers off a mouse event (⌘ on mac, Ctrl elsewhere — both are accepted). */
export const clickMods = (e) => ({ pick: !!(e && (e.metaKey || e.ctrlKey)), range: !!(e && e.shiftKey) })

/**
 * The selection after a click on `ids[index]`.
 *
 * @param checked  the current Set of selected ids
 * @param ids      the ids of the rows as rendered, in list order
 * @param index    which row was clicked
 * @param anchor   the last picked index, or null
 * @param pick     ⌘/Ctrl was held
 * @param range    ⇧ was held
 * @param focused  the index of the row open in the detail pane, or null
 * @returns {{checked: Set, anchor: number|null, focus: boolean}} — `focus` means
 *          the click was a plain one and the row should open in the detail pane.
 */
export function clickSelection({ checked, ids, index, anchor, pick, range, focused = null }) {
  let cur = checked instanceof Set ? checked : new Set(checked || [])
  const list = ids || []
  const id = list[index]
  // A modified click on a row the list does not have is not a click on "nothing
  // selected" — it is a no-op. Only a PLAIN click clears.
  if ((pick || range) && id === undefined) return { checked: cur, anchor, focus: false }
  // The open row is the implicit first pick: with nothing selected yet, a ⌘ or ⇧
  // click on another row selects both (and a range runs from the open row).
  if ((pick || range) && cur.size === 0 && focused != null && focused >= 0
      && focused < list.length && focused !== index) {
    cur = new Set([list[focused]])
    anchor = focused
  }
  if (pick) {
    const next = new Set(cur)
    if (next.has(id)) next.delete(id); else next.add(id)
    return { checked: next, anchor: index, focus: false }
  }
  // A range with no anchor yet has nothing to reach back to — fall through to a
  // plain click rather than selecting one lone row on a ⇧ the user meant as a range.
  if (range && anchor != null && anchor >= 0 && anchor < list.length) {
    const [a, b] = [anchor, index].sort((x, y) => x - y)
    const next = new Set(cur)
    for (let k = a; k <= b; k++) next.add(list[k])
    return { checked: next, anchor, focus: false }
  }
  return { checked: new Set(), anchor: index, focus: true }
}

/**
 * Drop ids that are no longer in the list — a background refresh must keep the
 * selection, but must not keep pointing at rows the server has stopped sending.
 * Returns the SAME Set when nothing was dropped, so it can be handed straight to
 * a setState updater without forcing a render.
 */
export function pruneSelection(checked, ids) {
  const cur = checked instanceof Set ? checked : new Set(checked || [])
  if (!cur.size) return cur
  const live = new Set(ids || [])
  const next = new Set([...cur].filter((id) => live.has(id)))
  return next.size === cur.size ? cur : next
}
