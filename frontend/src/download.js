// The file name from a Content-Disposition header: the RFC 5987 `filename*=UTF-8''…`
// first (it keeps a name like José or Дана), then the plain ASCII `filename=`, then the
// fallback. Shared by the résumé and cover-letter downloads, which the backend sends in
// the same two-name form.
// Blob.text() where the browser has it, FileReader where it does not (older engines, jsdom).
const blobText = (b) => (typeof b.text === 'function' ? b.text() : new Promise((resolve, reject) => {
  const r = new FileReader()
  r.onload = () => resolve(String(r.result || ''))
  r.onerror = () => reject(r.error)
  r.readAsText(b)
}))

// R30 D-07: why a download failed, from the server. A blob request gets its error body as a
// Blob too, so the JSON `detail` has to be read out of it; '' when there is none.
export async function downloadErrorReason(e) {
  const data = e?.response?.data
  let body = data
  try {
    if (typeof Blob !== 'undefined' && data instanceof Blob) body = JSON.parse(await blobText(data))
    else if (typeof data === 'string') body = JSON.parse(data)
  } catch { return '' }
  const d = body?.detail ?? body?.message
  return typeof d === 'string' ? d : ''
}

export function downloadName(disposition, fallback) {
  const cd = String(disposition || '')
  const star = /filename\*\s*=\s*UTF-8''([^;\s]+)/i.exec(cd)
  if (star) {
    try { return decodeURIComponent(star[1]) } catch { /* malformed: use the plain name */ }
  }
  const plain = /filename\s*=\s*(?:"([^"]*)"|([^;\s]+))/i.exec(cd)
  return (plain && (plain[1] || plain[2])) || fallback
}
