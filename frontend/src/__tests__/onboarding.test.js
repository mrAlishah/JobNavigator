// onboarding.js — which arrival overlay a profile gets. V-01: a brand-new
// profile that closes the first-run tour must not get the 2.0 upgrade overlay
// ("Welcome to the new JobNavigator") on its next load.
import { describe, it, expect, beforeEach } from 'vitest'
import {
  alreadyWelcomed, newUiPending, markWelcomed, markNewUiSeen,
  WELCOMED_KEY, NEWUI_KEY, NEWUI_VERSION,
} from '../onboarding'

beforeEach(() => { localStorage.clear(); sessionStorage.clear() })

// what App.jsx does on a load + tour close
const closeTourOnThisLoad = () => markWelcomed(!alreadyWelcomed())

describe('first-run tour vs upgrade overlay', () => {
  it('a fresh profile gets the tour and no upgrade overlay', () => {
    expect(alreadyWelcomed()).toBe(false)
    expect(newUiPending()).toBe(false)
  })

  it('V-01: closing the first-run tour marks the upgrade seen, so the next load is clean', () => {
    closeTourOnThisLoad()
    expect(localStorage.getItem(WELCOMED_KEY)).toBe('1')
    expect(localStorage.getItem(NEWUI_KEY)).toBe(NEWUI_VERSION)
    // next load
    expect(alreadyWelcomed()).toBe(true)
    expect(newUiPending()).toBe(false)
  })

  it('V-01: a keyed install (sign-in wrote the API key before the tour) is still a first run', () => {
    localStorage.setItem('jobnavigator_api_key', 'k')
    sessionStorage.setItem('jn:welcome', '1')
    expect(newUiPending()).toBe(true)       // pending, but the tour wins while it is up
    closeTourOnThisLoad()
    expect(sessionStorage.getItem('jn:welcome')).toBe(null)
    expect(newUiPending()).toBe(false)
  })

  it('an upgrader (already welcomed) who sees the tour again after a sign-in keeps the announcement', () => {
    localStorage.setItem(WELCOMED_KEY, '1')
    closeTourOnThisLoad()
    expect(localStorage.getItem(NEWUI_KEY)).toBe(null)
    expect(newUiPending()).toBe(true)
  })

  it('a legacy welcome mark makes the profile an upgrader', () => {
    localStorage.setItem('jobnavigator_welcome_seen', 'true')
    expect(alreadyWelcomed()).toBe(true)
    expect(newUiPending()).toBe(true)
    markNewUiSeen()
    expect(newUiPending()).toBe(false)
  })

  it('an older seen-mark leaves this release pending', () => {
    localStorage.setItem(WELCOMED_KEY, '1')
    localStorage.setItem(NEWUI_KEY, '1.0.0')
    expect(newUiPending()).toBe(true)
  })
})
