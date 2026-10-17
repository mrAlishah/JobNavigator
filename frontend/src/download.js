// The file name from a Content-Disposition header: the RFC 5987 `filename*=UTF-8''…`
// first (it keeps a name like José or Дана), then the plain ASCII `filename=`, then the
// fallback. Shared by the résumé and cover-letter downloads, which the backend sends in
// the same two-name form.
export function downloadName(disposition, fallback) {
  const cd = String(disposition || '')
  const star = /filename\*\s*=\s*UTF-8''([^;\s]+)/i.exec(cd)
  if (star) {
    try { return decodeURIComponent(star[1]) } catch { /* malformed: use the plain name */ }
  }
  const plain = /filename\s*=\s*(?:"([^"]*)"|([^;\s]+))/i.exec(cd)
  return (plain && (plain[1] || plain[2])) || fallback
}
