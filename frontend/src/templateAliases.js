// The editor's view of résumé templates that were merged into one. The backend's template list gives the photo
// template an `aliases` map (legacy id -> default photo side); a résumé that still stores a legacy id is shown as
// using that template, on its old side, and nothing in the editor keeps a copy of the mapping.
export const IMAGE_SIDES = ['left', 'right']
export const DEFAULT_IMAGE_SIDE = 'right'

const has = (object, key) => Object.prototype.hasOwnProperty.call(object, key)
const aliasSide = (templates, id) => {
  const owner = (templates || []).find((t) => t.aliases && has(t.aliases, id))
  return owner ? owner.aliases[id] : undefined
}
const savedSide = (data) => (IMAGE_SIDES.includes(data?.profile_image_position) ? data.profile_image_position : undefined)

// The template an id stands for: the template with that id, or the one that answers to it as a legacy id.
export const templateFor = (templates, id) => (templates || []).find((t) => t.id === id) || (templates || []).find((t) => t.aliases && has(t.aliases, id))

// Side of the photo: the résumé's saved side, else the legacy id's own side, else right (as the backend resolves it).
export const effectiveImageSide = (templates, id, data) => savedSide(data) || aliasSide(templates, id) || DEFAULT_IMAGE_SIDE

// `data` with the legacy side saved in a copy when a résumé that stores a legacy id has no side saved yet (the
// same object when there is nothing to save), for the patch that switches its template.
export const dataWithSavedSide = (templates, id, data) => {
  const side = aliasSide(templates, id)
  return side && !savedSide(data) ? { ...data, profile_image_position: side } : data
}
