import React, { useState } from 'react'
import { Link } from 'react-router-dom'
import { Sparkles, X } from 'lucide-react'

// Bump the key when there's a new feature to announce — the banner reappears
// once per user until they dismiss that version.
const WHATS_NEW_KEY = 'jn:whatsnew:v1.1.0'

export default function WhatsNewBanner() {
  const [dismissed, setDismissed] = useState(() => {
    try { return localStorage.getItem(WHATS_NEW_KEY) === '1' } catch { return false }
  })
  if (dismissed) return null

  const close = () => {
    try { localStorage.setItem(WHATS_NEW_KEY, '1') } catch {}
    setDismissed(true)
  }

  return (
    <div className="flex items-start gap-3 px-4 py-3 bg-gradient-to-r from-blue-600 to-indigo-600 text-white text-sm">
      <Sparkles size={18} className="mt-0.5 flex-shrink-0" />
      <div className="flex-1">
        <span className="font-semibold">New — Application Autofill.</span>{' '}
        Focus any free-text question on a job application and click the Navigator
        button to generate a persona-grounded answer, right in the page. Turn it on
        with the <span className="font-medium">Application Autofill</span> toggle in
        the extension popup, and fill out your{' '}
        <Link to="/persona" className="underline font-medium">Persona</Link> so
        answers have something to ground on.
      </div>
      <button
        onClick={close}
        aria-label="Dismiss"
        className="flex-shrink-0 text-white/80 hover:text-white transition-colors"
      >
        <X size={18} />
      </button>
    </div>
  )
}
