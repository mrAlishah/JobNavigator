// Image / version mismatch warning (the rail's health line + one error toast).
//
// Compose bind-mounts ./backend into a prebuilt image, so `git pull` alone runs new
// backend code in an old image; /health then reports `image_outdated` with a
// message naming what differs. The frontend is a separate image: it knows its own
// release (__APP_VERSION__, major.minor) and /health returns the backend's, so a
// dashboard left on another release is caught here too. A matching install gets
// null and nothing on screen changes.
import { useEffect, useState } from 'react'

export const FIX = 'Run `docker compose pull && docker compose up -d`, or `docker compose up --build -d`.'
const SEEN_KEY = 'jobnavigator_version_warned'

export function versionWarning(health, appVersion) {
  if (!health || typeof health !== 'object') return null
  if (health.image_outdated) {
    return {
      kind: 'image',
      short: 'Backend image out of date',
      message: health.image_message || `The backend image does not match the code. ${FIX}`,
    }
  }
  const backend = health.version != null ? String(health.version) : ''
  const frontend = appVersion != null ? String(appVersion) : ''
  if (backend && frontend && backend !== frontend) {
    return {
      kind: 'version',
      short: 'Version mismatch',
      message: `The dashboard is v${frontend} but the backend is v${backend}. ${FIX}`,
    }
  }
  return null
}

// Reads /health once per page load. `push` (useToasts) raises the error toast,
// once per browser session per message so a reload does not stack it again; the
// rail line keeps the warning visible after the toast is dismissed.
export function useVersionWarning(appVersion, push) {
  const [warning, setWarning] = useState(null)
  useEffect(() => {
    let on = true
    fetch('/health')
      .then((r) => (r.ok ? r.json() : null))
      .then((h) => {
        if (!on) return
        const w = versionWarning(h, appVersion)
        setWarning(w)
        if (!w || !push) return
        let seen = null
        try { seen = sessionStorage.getItem(SEEN_KEY) } catch {}
        if (seen === w.message) return
        try { sessionStorage.setItem(SEEN_KEY, w.message) } catch {}
        push({ kind: 'error', msg: w.message })
      })
      .catch(() => { /* unreachable backend: the rail's "Backend unreachable" covers it */ })
    return () => { on = false }
  }, [appVersion, push])
  return warning
}
