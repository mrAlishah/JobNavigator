"""newui — the 2.0 upgrade announcement (App.jsx / NewUiModal.jsx) and the
old /classic bookmarks.

Facts, all keyed off localStorage, all checked in throwaway contexts so the
user's own storage is never involved:

  1. An EXISTING user (a profile carrying `jobnavigator_welcomed`, or any legacy
     key) sees "Welcome to the new JobNavigator" once. Closing it — by Escape,
     the scrim, the ✕ or "Got it", all one `onClose` — writes
     `jobnavigator_newui_seen = '3.1'` and the next load is clean.
  2. A GENUINELY FRESH profile sees the first-run tour instead; the upgrade modal
     is not marked seen while the tour is up, and closing the tour marks it seen
     (V-01), so the visit after the tour is clean.
  3. The classic dashboard was removed in 3.0: `/classic` and `/classic/<screen>`
     redirect to the same path without the prefix (query and hash kept), so old
     bookmarks land on the screen of the same name.

`run.sh` copies this file into the backend container and runs it there; the
frontend it drives is whatever the last `docker compose build frontend` produced,
so the orchestrator must BUILD the frontend before this case means anything.
"""
import re

from _suite import case
import _common as C
import h

NEWUI_TITLE = 'Welcome to the new JobNavigator'
TOUR_TITLE = 'Welcome to JobNavigator'
SEEN_KEY = 'jobnavigator_newui_seen'
TELEMETRY_TITLE = 'Allow telemetry?'   # TelemetryChoice.jsx, asked of the owner once
VERSION = '3.1'

# The tour's own title is a PREFIX of the upgrade modal's, so "is the tour up?"
# can never be asked with a plain substring test — it is true either way.
def _tour_only(pg):
    return C.body_has(pg, TOUR_TITLE) and not C.body_has(pg, NEWUI_TITLE)


@case('newui')
def _newui(c):
    with h.browser() as b:
        # ── 1. existing user: shown once ────────────────────────────────────
        # raw_page seeds the API key too, but `jobnavigator_welcomed` is what
        # settles it: the tour is suppressed, so the upgrade modal is the one
        # overlay left that can appear.
        pg = C.raw_page(b, ls={'jobnavigator_welcomed': '1'})
        try:
            C.go(pg, '/feed', settle=900)
            c.check('an existing profile has no seen-mark yet', C.ls_get(pg, SEEN_KEY) is None)
            shown = C.body_has(pg, NEWUI_TITLE)
            c.check('existing user sees the new-UI modal', shown)
            c.check('the first-run tour does not double up', not _tour_only(pg))
            if shown:
                bullets = pg.evaluate("""() => {
                  const t = (document.querySelector('[role="dialog"]')?.innerText || '');
                  return [/up to five people share one install/, /Everything you know, rebuilt/,
                          /extension, redesigned/, /Pick a look in Settings/, /Persona import from a r/]
                         .filter((re) => re.test(t)).length;
                }""")
                c.check('all five bullets render, accounts first', bullets == 5, bullets)
                notes = pg.evaluate(
                    """() => { const a = [...document.querySelectorAll('[role="dialog"] a')]
                        .find((x) => /Read the release notes/.test(x.textContent || ''));
                      return a ? { href: a.getAttribute('href'), target: a.getAttribute('target') } : null; }""")
                c.check('release-notes link points at CHANGELOG.md on GitHub in a new tab',
                        bool(notes) and notes['href'].endswith('/blob/main/CHANGELOG.md')
                        and notes['target'] == '_blank', notes)

                # Escape is a REAL dismissal, not a peek: it runs the panel's
                # `onClose`, which is the same handler "Got it" runs, so it writes
                # the mark too. That is the contract on purpose — an announcement
                # nobody asked for must not come back because it was waved away
                # with the keyboard instead of the button. The listener is
                # capture-phase, so it wins over the Feed, which owns Escape itself.
                pg.keyboard.press('Escape')
                pg.wait_for_timeout(450)
                c.check('Escape closes it', not C.body_has(pg, NEWUI_TITLE))
                c.eq('Escape marks it seen', C.ls_get(pg, SEEN_KEY), VERSION)

                C.clear_log(pg)
                C.go(pg, '/feed', settle=900)
                c.check('a load after Escape does not replay it', not C.body_has(pg, NEWUI_TITLE))

                # Clear the mark and come back, to exercise the button on a panel
                # raised the same way the real upgrade raises it.
                pg.evaluate('(k) => { try { localStorage.removeItem(k) } catch (e) {} }', SEEN_KEY)
                C.go(pg, '/feed', settle=900)
                c.check('clearing the mark raises it again', C.body_has(pg, NEWUI_TITLE))

                got = pg.locator('[role="dialog"]').first.get_by_text('Got it', exact=True)
                got.first.click()
                pg.wait_for_timeout(450)
                c.check('"Got it" closes it', not C.body_has(pg, NEWUI_TITLE))
                c.eq('"Got it" writes the versioned seen-mark', C.ls_get(pg, SEEN_KEY), VERSION)

                C.clear_log(pg)
                C.go(pg, '/feed', settle=900)
                c.check('a second load does not replay it', not C.body_has(pg, NEWUI_TITLE))
                errs, perrs = C.console_errors(pg)
                c.check('no console errors around the overlay', not errs and not perrs,
                        '; '.join((errs + perrs)[:2]))
        finally:
            pg.context.close()

        # ── 2. fresh profile: the tour wins, and the upgrade stays owed ──────
        pg = C.raw_page(b)                      # nothing seeded but the API key
        try:
            C.go(pg, '/feed', settle=900)
            c.check('a fresh profile gets the first-run tour', _tour_only(pg))
            c.check('the upgrade modal is not marked seen behind it',
                    C.ls_get(pg, SEEN_KEY) is None, C.ls_get(pg, SEEN_KEY))
            pg.locator('[aria-label="Close"]').first.click()
            pg.wait_for_timeout(450)
            c.eq('dismissing the tour writes jobnavigator_welcomed',
                 C.ls_get(pg, 'jobnavigator_welcomed'), '1')
            # V-01: a first-run tour marks the upgrade announcement seen too
            c.eq('dismissing the first-run tour marks the upgrade seen',
                 C.ls_get(pg, SEEN_KEY), VERSION)

            # the visit AFTER the tour: a brand-new user is never "upgraded"
            C.clear_log(pg)
            C.go(pg, '/feed', settle=900)
            c.check('no upgrade modal on the next visit', not C.body_has(pg, NEWUI_TITLE))
            c.check('and no tour either', not _tour_only(pg))
        finally:
            pg.context.close()

        # ── 2b. the owner's telemetry question comes right after either overlay ──
        # GET /api/telemetry is answered "not asked yet" (the install's real choice is never read
        # or written: the dialog is closed with Escape, which saves nothing).
        ask = {'level': 'unset', 'asked': False, 'owner': True, 'locked': False}
        for label, seed, close in (
                ('after the upgrade notice', {'jobnavigator_welcomed': '1'}, NEWUI_TITLE),
                ('after the first-run tour', {}, TOUR_TITLE)):
            pg = C.raw_page(b, ls=seed, telemetry=ask)
            try:
                C.go(pg, '/feed', settle=1200)
                c.check(f'telemetry waits {label}', C.body_has(pg, close) and not C.body_has(pg, TELEMETRY_TITLE))
                pg.keyboard.press('Escape')
                pg.wait_for_timeout(1200)
                c.check(f'telemetry is asked {label}', C.body_has(pg, TELEMETRY_TITLE))
                c.check('Yes is preselected', pg.get_by_role('radio', name=re.compile('^Yes')).first
                        .get_attribute('aria-checked') == 'true')
                pg.keyboard.press('Escape')
                pg.wait_for_timeout(450)
                c.check('Escape closes it without a choice', not C.body_has(pg, TELEMETRY_TITLE))
            finally:
                pg.context.close()

        # ── 3. old /classic bookmarks ────────────────────────────────────────
        # newui_seen is pre-set so the modal is out of the way. Every v1 path had
        # a twin at the root, so the prefix is simply dropped.
        pg = C.raw_page(b, ls={'jobnavigator_welcomed': '1', SEEN_KEY: VERSION})
        try:
            for old, want in (('/classic', '/feed'),
                              ('/classic/applications', '/applications'),
                              ('/classic/settings?x=1#y', '/settings?x=1#y'),
                              ('/classic/cover-letters', '/cover-letters')):
                C.go(pg, old, settle=900)
                got = pg.evaluate('() => location.pathname + location.search + location.hash')
                # /feed opens its first job and appends ?job=<id> when the feed has rows;
                # only the path matters for the redirect.
                if want == '/feed':
                    got = got.split('?', 1)[0]
                c.eq(f'{old} lands on {want}', got, want)
            c.check('no classic shell is left behind', pg.locator('aside.bg-slate-900').count() == 0)
        finally:
            pg.context.close()
