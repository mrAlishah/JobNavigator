"""mt-switch — the account picker, PIN pad and rail switcher (MT-12) on a real second account.

SAFE FOR THE LIVE DB, by construction:
- refuses to run (SKIP) unless the install has exactly ONE account when it starts;
- sets an owner PIN only when the owner has none (needed: an owner without a PIN cannot
  add an account) and removes it again afterwards;
- creates one member named `ZZE Member <n>` with no copy-from (an empty Persona and the
  two built-in extension searches, nothing else), and in `finally` DELETEs it through
  the API (typed-name confirm; the API takes its own pre-removal pg_dump first);
- ends by asserting the install is back to exactly one account and the owner's PIN
  state is what it was.
Take a manual backup before running it anyway (MT-PLAN rule for data-changing work).

Flow: a fresh browser lands on the picker (401 account_required, not the sign-in);
the member's tile opens its PIN pad; a wrong PIN shows the tries left; the right PIN
switches, reloads and toasts "Switched to …"; the rail shows the member; its menu lists
both accounts with ✓ on the current one; Ctrl+Shift+1 asks for the owner's PIN and
switches back.
"""
import json
import os
import random
import time
import urllib.error
import urllib.request

from _suite import case
import _common as C
import h

OWNER_PIN = '7391'
MEMBER_PIN = '2468'
SHOTS = '/tmp/v2t/shots/mt12'


def _api(method, path, body=None, account=None):
    """h.api plus an optional account (with two accounts the API needs one): a signed
    X-Account-Token from verify-pin (the owner's PIN opens both accounts here)."""
    data = None if body is None else json.dumps(body).encode()
    headers = {'X-API-Key': h.KEY, 'Content-Type': 'application/json'}
    if account:
        headers['X-Account-Token'] = C.account_token(account, OWNER_PIN)
    req = urllib.request.Request(h.BASE + '/api' + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            txt, st = r.read().decode('utf-8', 'replace'), r.status
    except urllib.error.HTTPError as e:
        txt, st = e.read().decode('utf-8', 'replace'), e.code
    try:
        return st, (json.loads(txt) if txt else None)
    except ValueError:
        return st, txt


def _switcher(pg):
    return pg.evaluate("""() => {
      const el = document.querySelector('[data-account-switcher]');
      return el ? el.getAttribute('aria-label') : null;
    }""")


def _me(pg):
    return pg.evaluate("""async () => {
      const r = await fetch('/api/auth/me', { credentials: 'include' });
      const d = await r.json().catch(() => null);
      return { status: r.status, name: d && d.account ? d.account.name : null };
    }""")


def _wait_text(pg, text, ms=8000):
    try:
        pg.wait_for_function('(t) => (document.body.innerText || "").includes(t)', arg=text, timeout=ms)
        return True
    except Exception:
        return False


@case('mt-switch')
def _mt_switch(c):
    os.makedirs(SHOTS, exist_ok=True)
    C.single_account_only(c)   # 2+ accounts: skip before touching anything
    st, data = _api('GET', '/accounts')
    if st != 200 or not isinstance(data, dict):
        c.skip(f'GET /api/accounts answered {st}')
    if len(data['accounts']) != 1:
        c.skip(f'needs an install with exactly one account (has {len(data["accounts"])}) — refusing to touch it')
    owner = data['accounts'][0]
    owner_id, owner_name = owner['id'], owner['name']
    had_pin = bool(owner['has_pin'])
    if had_pin:
        c.skip('the owner already has a PIN this case does not know — set none, or run against a throwaway DB')

    member_name = f'ZZE Member {random.randint(100, 999)}'
    member_id = None
    pin_set = False
    try:
        st, d = _api('PATCH', f'/accounts/{owner_id}', {'pin': OWNER_PIN})
        pin_set = c.check('owner PIN set for the run', st == 200 and d['account']['has_pin'], st)
        st, d = _api('POST', '/accounts', {'name': member_name, 'pin': MEMBER_PIN})
        c.check('second account created', st == 201, f'{st} {d}')
        if st != 201:
            return
        member_id = d['account']['id']

        st, d = _api('GET', '/auth/me')
        c.eq('the API without a token now needs an account', (st, (d or {}).get('detail')), (401, 'account_required'))
        # QA F1 (R2-01): a bare X-Account id selects nothing, even with the install key
        req = urllib.request.Request(h.BASE + '/api/auth/me', method='GET',
                                     headers={'X-API-Key': h.KEY, 'X-Account': str(owner_id)})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                bare = r.status
        except urllib.error.HTTPError as e:
            bare = e.code
        c.eq('a bare X-Account header is ignored', bare, 401)
        st, d = _api('GET', '/auth/me', account=member_id)
        c.eq('a token from verify-pin acts as that account', (st, (d or {}).get('account', {}).get('id')), (200, member_id))

        with h.browser() as b:
            pg = C.seeded_page(b)
            try:
                C.go(pg, '/feed', settle=1200)
                c.check('a fresh browser gets the account picker', _wait_text(pg, 'job hunting?'))
                c.check('… and not the install-password sign-in', not C.body_has(pg, 'Enter your dashboard API key'))
                # QA F7 / T7-03: the screen waits behind the picker instead of failing
                c.check("… with no \"Couldn't load\" behind it (T7-03)", not C.body_has(pg, "Couldn't load"))
                # QA F7 / R6-05: the picker is a named dialog and holds the focus
                c.check('the picker dialog is named (R6-05)', pg.locator('[role="dialog"][aria-labelledby="jn-picker-title"]').count() == 1)
                pg.keyboard.press('Tab'); pg.keyboard.press('Tab'); pg.keyboard.press('Tab')
                c.check('Tab stays inside the picker (R6-05)', pg.evaluate("() => !!document.activeElement.closest('[role=\"dialog\"]')"))
                tiles = pg.locator('[role="listitem"]').count()
                c.eq('one tile per account', tiles, 2)
                pg.screenshot(path=f'{SHOTS}/picker.png')

                pg.locator(f'[aria-label^="{member_name},"]').first.click()
                c.check("the member's tile opens its PIN pad", _wait_text(pg, f"Enter {member_name}'s PIN"))
                c.eq('four PIN fields', pg.locator('[data-testid="pin-step"] input').count(), 4)
                pg.screenshot(path=f'{SHOTS}/pinpad.png')
                pg.keyboard.type('0000', delay=60)
                c.check('a wrong PIN says how many tries are left', _wait_text(pg, 'tries left'))
                pg.wait_for_timeout(400)
                pg.screenshot(path=f'{SHOTS}/pinpad-wrong.png')

                pg.keyboard.type(MEMBER_PIN, delay=60)
                c.check('the right PIN switches and the screen toasts', _wait_text(pg, f'Switched to {member_name}', 12000))
                pg.wait_for_timeout(600)
                c.eq('this browser now acts as the member', _me(pg), {'status': 200, 'name': member_name})
                lbl = _switcher(pg) or ''
                c.check('the rail footer shows the member', lbl.startswith(f'{member_name}, Member'), lbl)
                c.check('still on the feed after the reload', pg.url.rstrip('/').endswith('/feed'), pg.url)

                pg.locator('[data-account-switcher]').click()
                pg.wait_for_timeout(400)
                menu = pg.locator('[role="menu"][aria-label="Accounts"]')
                c.check('the switcher menu opens', menu.count() == 1)
                text = menu.inner_text() if menu.count() else ''
                c.check('it lists both accounts and the actions', all(t in text for t in (owner_name, member_name, 'Manage accounts')), text[:200])
                c.check('a member gets no "Add account…"', 'Add account' not in text)
                c.check('the current account carries ✓', menu.locator('[aria-label$=", current"]').count() == 1)
                pg.screenshot(path=f'{SHOTS}/rail-menu.png')
                pg.keyboard.press('Escape')
                pg.wait_for_timeout(300)

                pg.keyboard.press('Control+Shift+Digit1')
                c.check("Ctrl+Shift+1 asks for the owner's PIN", _wait_text(pg, f"Enter {owner_name}'s PIN"))
                pg.keyboard.type(OWNER_PIN, delay=60)
                c.check('the owner PIN switches back', _wait_text(pg, f'Switched to {owner_name}', 12000))
                pg.wait_for_timeout(600)
                c.eq('this browser acts as the owner again', _me(pg), {'status': 200, 'name': owner_name})
                lbl = _switcher(pg) or ''
                c.check('the rail footer shows the owner', lbl.startswith(f'{owner_name}, Owner'), lbl)
                pg.locator('[data-account-switcher]').click()
                pg.wait_for_timeout(400)
                c.check('the owner gets "Add account…"', 'Add account' in pg.locator('[role="menu"][aria-label="Accounts"]').inner_text())
                pg.screenshot(path=f'{SHOTS}/rail-menu-owner.png')
                pg.keyboard.press('Escape')
                # the same switcher on win98 (square palette avatars), open and collapsed rail
                for rail in ('expanded', 'collapsed'):
                    pg.evaluate("(r) => { localStorage.setItem('jobnavigator_theme', 'win98'); localStorage.setItem('jobnavigator_v2_rail', r) }", rail)
                    C.go(pg, '/feed', settle=1200)
                    pg.locator('[data-account-switcher]').click()
                    pg.wait_for_timeout(400)
                    radius = pg.evaluate("() => getComputedStyle(document.querySelector('[data-account-switcher] .v2-avatar')).borderRadius")
                    c.eq(f'win98 avatar is square ({rail} rail)', radius, '0px')
                    pg.screenshot(path=f'{SHOTS}/rail-menu-win98-{rail}.png')
                    pg.keyboard.press('Escape')
                pg.evaluate("() => { localStorage.removeItem('jobnavigator_theme'); localStorage.setItem('jobnavigator_v2_rail', 'expanded') }")

                # QA F7 / R6-01: a second tab of the same browser, then a switch in the first
                pg2 = pg.context.new_page()
                C.go(pg2, '/persona', settle=1500)
                c.check('tab 2 shows the owner', (_switcher(pg2) or '').startswith(f'{owner_name}, Owner'), _switcher(pg2))
                C.go(pg, '/feed', settle=1200)
                pg.keyboard.press('Control+Shift+Digit2')
                c.check("tab 1: Ctrl+Shift+2 asks for the member's PIN", _wait_text(pg, f"Enter {member_name}'s PIN"))
                pg.keyboard.type(MEMBER_PIN, delay=60)
                c.check('tab 1 switches to the member', _wait_text(pg, f'Switched to {member_name}', 12000))
                c.check('tab 2 gets the blocking "switched in another tab" notice',
                        _wait_text(pg2, f'This tab was switched to {member_name}', 8000))
                c.check('… as a dialog with a Reload button', pg2.locator('[data-account-changed] [role="dialog"] [role="button"]:has-text("Reload")').count() == 1)
                c.eq('tab 1 has no such notice', C.body_has(pg, 'This tab was switched'), False)
                pg2.screenshot(path=f'{SHOTS}/cross-tab-notice.png')
                # the server refuses a stale write too (what the keepalive flush would send)
                stale = pg2.evaluate("""async (owner) => {
                  const r = await fetch('/api/persona', { method: 'PATCH', credentials: 'include',
                    headers: { 'Content-Type': 'application/json', 'X-Expected-Account': owner },
                    body: JSON.stringify({ contact: { name: 'stale tab' } }) });
                  return { status: r.status, detail: (await r.json().catch(() => ({}))).detail };
                }""", owner_id)
                c.eq('a write naming the old account is 409 account_changed', stale, {'status': 409, 'detail': 'account_changed'})
                pg2.locator('[data-account-changed] [role="button"]:has-text("Reload")').click()
                pg2.wait_for_timeout(1500)
                c.check('after Reload tab 2 is the member', (_switcher(pg2) or '').startswith(f'{member_name}, Member'), _switcher(pg2))
                c.check('… and the notice is gone', not C.body_has(pg2, 'This tab was switched'))
                pg2.close()
                errs, perrs = C.console_errors(pg)
                c.check('no page errors through the flow', not perrs, '; '.join(perrs[:2]))
            finally:
                pg.context.close()
    finally:
        if member_id:
            for _ in range(24):   # a scheduler job running for the member → 409 account_busy: wait it out
                st, d = _api('DELETE', f'/accounts/{member_id}', {'confirm_name': member_name}, account=owner_id)
                if not (st == 409 and isinstance(d, dict) and d.get('detail') == 'account_busy'):
                    break
                time.sleep(5)
            c.check('the second account is removed again', st == 200, f'{st} {d}')
            if st == 200:
                c.note(f'pre-removal backup: {d.get("backup")}')
        if pin_set and not had_pin:
            st, d = _api('PATCH', f'/accounts/{owner_id}', {'pin': None, 'current_pin': OWNER_PIN})
            c.check("the owner's PIN is removed again", st == 200 and not d['account']['has_pin'], f'{st} {d}')
        st, d = _api('GET', '/accounts')
        n = len(d['accounts']) if st == 200 and isinstance(d, dict) else None
        c.eq('the install is back to exactly one account', n, 1)
        if n == 1:
            c.eq("the owner's PIN state is as it was", d['accounts'][0]['has_pin'], had_pin)

    # one account again: no switcher anywhere
    with h.browser() as b:
        pg = C.seeded_page(b)
        try:
            C.go(pg, '/feed', settle=1200)
            c.check('with one account the rail has no switcher', _switcher(pg) is None)
            c.check('… and no picker', not C.body_has(pg, 'job hunting?'))
        finally:
            pg.context.close()
