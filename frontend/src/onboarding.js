// Which of the two arrival overlays a browser profile gets: the first-run tour
// (WelcomeModal) or the upgrade announcement (NewUiModal). App.jsx renders them;
// the storage rules live here so they can be tested without mounting the app.

// Set once the onboarding overlay has been dismissed; its absence is what makes
// a browser profile a "first visit" (R4-T0-02).
export const WELCOMED_KEY = 'jobnavigator_welcomed'
// Two keys an older build wrote for the same fact (and the ones the Playwright
// harness seeds). Either one means the tour has already been seen, so neither an
// upgrade nor a test run replays it.
const LEGACY_WELCOMED = ['jobnavigator_v2_welcome_seen', 'jobnavigator_welcome_seen']
export const alreadyWelcomed = () => {
  try {
    if (localStorage.getItem(WELCOMED_KEY) === '1') return true
    return LEGACY_WELCOMED.some((k) => localStorage.getItem(k) === 'true')
  } catch { return true }   // storage blocked: never strand someone behind a modal that can't record its own dismissal
}

// The upgrade overlay (NewUiModal). Versioned rather than boolean, so the
// next release can raise its own by bumping the value; anything else — an older
// mark, no mark — means this release has not been announced to this profile yet.
export const NEWUI_KEY = 'jobnavigator_newui_seen'
// 3.1: raised again so returning users see the accounts (multi-tenancy) line once.
export const NEWUI_VERSION = '3.1'
// Keys only an EXISTING install can have written. A profile carrying any of them
// (or the welcome mark itself) used JobNavigator before the redesign, which is
// exactly the audience for "here is what moved" — a genuinely fresh profile has
// none of them and gets the first-run tour instead.
const RETURNING_KEYS = ['jobnavigator_api_key', 'jobnavigator_dark_mode', 'jobnavigator_v2_rail']
const isReturningUser = () => {
  try {
    if (alreadyWelcomed()) return true
    return RETURNING_KEYS.some((k) => localStorage.getItem(k) !== null)
  } catch { return false }
}
// Same guard as the tour: storage blocked → no overlay, since the dismissal
// could not be recorded and it would come back on every load.
export const newUiPending = () => {
  try { return isReturningUser() && localStorage.getItem(NEWUI_KEY) !== NEWUI_VERSION } catch { return false }
}

export const markNewUiSeen = () => {
  try { localStorage.setItem(NEWUI_KEY, NEWUI_VERSION) } catch { /* ignore */ }
}

// The tour was closed. `firstRun` = the profile had never been welcomed when the
// page loaded: that person was onboarded on the current UI, so the "here is what
// moved" announcement has nothing to tell them and is marked seen as well (V-01:
// otherwise the welcome mark itself made the next load look like an upgrade).
// A welcomed profile seeing the tour again after a sign-in (V-06) keeps its
// pending announcement.
export const markWelcomed = (firstRun) => {
  try { sessionStorage.removeItem('jn:welcome') } catch { /* ignore */ }
  try { localStorage.setItem(WELCOMED_KEY, '1') } catch { /* ignore */ }
  if (firstRun) markNewUiSeen()
}
