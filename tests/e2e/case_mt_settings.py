"""mt-settings — Settings › Accounts (owner) and Your account (member), MT-13; settings by role, MT-14.

SAFE FOR THE LIVE DB, by construction (the mt-switch pattern):
- SKIPs unless the install has exactly ONE account and the owner has no PIN when it starts;
- QA F2: it adds the member with no PIN anywhere (D3) and checks the plain Unlock, then sets
  an owner PIN (Settings › Accounts › Set PIN) for the PIN paths; that PIN is removed again
  in `finally`;
- it creates one member `ZZS Member <n>` with no copy-from, and deletes it in `finally`
  (through the UI when the flow gets there, else the API; the API takes its own
  pre-removal pg_dump first);
- any section policy it flips belongs to that member and goes with it; any install
  setting it edits is written back to the value it read first;
- ends asserting exactly one account and no owner PIN.
Take a manual backup before running it anyway.
"""
import json
import os
import random
import re
import time
import urllib.error
import urllib.request

from _suite import case
import _common as C
import h

OWNER_PIN = '7391'
SHOTS = '/tmp/v2t/shots/mt13'


def _api(method, path, body=None, account=None):
    data = None if body is None else json.dumps(body).encode()
    headers = {'X-API-Key': h.KEY, 'Content-Type': 'application/json'}
    if account:   # QA F1: a signed token from verify-pin (the member has no PIN)
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


def _wait_text(pg, text, ms=8000):
    try:
        pg.wait_for_function('(t) => (document.body.innerText || "").includes(t)', arg=text, timeout=ms)
        return True
    except Exception:
        return False


def _wait_text_ci(pg, text, ms=8000):
    try:
        pg.wait_for_function('(t) => (document.body.innerText || "").toLowerCase().includes(t.toLowerCase())', arg=text, timeout=ms)
        return True
    except Exception:
        return False


def _gone_text(pg, text, ms=8000):
    try:
        pg.wait_for_function('(t) => !(document.body.innerText || "").includes(t)', arg=text, timeout=ms)
        return True
    except Exception:
        return False


def _me(pg):
    return pg.evaluate("""async () => {
      const r = await fetch('/api/auth/me', { credentials: 'include' });
      const d = await r.json().catch(() => null);
      return { status: r.status, name: d && d.account ? d.account.name : null };
    }""")


def _nav(pg):
    return pg.evaluate("""() => [...document.querySelectorAll('.v2-anchor')].map((e) => e.textContent.trim())""")


def _active(pg):
    """The nav entry drawn as current (600 weight)."""
    return pg.evaluate("""() => { const a = [...document.querySelectorAll('.v2-anchor')].find((e) => getComputedStyle(e).fontWeight === '600');
      return a ? a.textContent.trim() : null }""")


def _scroll_to(pg, sec):
    pg.evaluate("""(s) => { const el = document.querySelector(`[data-sec="sec-${s}"]`); if (el) el.scrollIntoView({ block: 'start' }) }""", sec)
    pg.wait_for_timeout(300)


@case('mt-settings')
def _mt_settings(c):
    os.makedirs(SHOTS, exist_ok=True)
    C.single_account_only(c)   # 2+ accounts: skip before touching anything
    st, data = _api('GET', '/accounts')
    if st != 200 or not isinstance(data, dict):
        c.skip(f'GET /api/accounts answered {st}')
    if len(data['accounts']) != 1:
        c.skip(f'needs an install with exactly one account (has {len(data["accounts"])})')
    owner = data['accounts'][0]
    owner_id, owner_name = owner['id'], owner['name']
    if owner['has_pin']:
        c.skip('the owner already has a PIN this case does not know')

    member_name = f'ZZS Member {random.randint(100, 999)}'
    member_id = None
    try:
        with h.browser() as b:
            opg = C.seeded_page(b)
            try:
                # ── QA F2 / D2: one account → the Accounts section first, own row + Add ──
                C.go(opg, '/settings', settle=1200)
                c.check('one account: Accounts is the first section', (_nav(opg) or [''])[0] == 'Accounts', _nav(opg))
                c.eq('T7-01: at the top the first nav entry is the highlighted one', _active(opg), 'Accounts')
                c.eq('one account: the table has the owner row only', opg.locator('[data-account-row]').count(), 1)
                c.check('… with Set PIN, API token and Edit',
                        all(opg.locator(f'[aria-label="{a}"]').count() == 1 for a in
                            ('Set your PIN', 'Create an API token for scripts', 'Rename or recolour your account')))
                c.eq('no Advanced "Add account…" row any more', opg.locator('[aria-label="Add an account to this install"]').count(), 0)
                opg.screenshot(path=f'{SHOTS}/owner-one-account.png')
                # ── D3: no owner PIN step; the member gets no PIN either ──
                opg.get_by_text('+ Add account', exact=True).click()
                c.check('the Add account form opens at once (no owner PIN step)', _wait_text(opg, 'Start from a copy of'))
                c.check('… no "Set your PIN first"', not C.body_has(opg, 'Set your PIN first'))

                opg.locator('[aria-label="Account name"]').fill(member_name)
                opg.locator('[aria-label="Copy from account"]').click()
                opg.get_by_text(f'{owner_name} (owner)', exact=True).click()
                c.check('picking a source lists the items to copy', _wait_text(opg, 'Base résumés'))
                opg.wait_for_timeout(300)
                opg.screenshot(path=f'{SHOTS}/add-account-modal.png')
                # this run copies nothing: back to an empty start
                opg.locator('[aria-label="Copy from account"]').click()
                opg.get_by_text('Nothing — start empty', exact=True).click()
                opg.get_by_text('Add account', exact=True).last.click()
                c.check('adding toasts', _wait_text(opg, f'Added {member_name}', 12000))
                st, d = _api('GET', '/accounts', account=owner_id)
                acc = [a for a in (d or {}).get('accounts', []) if a['name'] == member_name]
                c.check('the member exists', st == 200 and len(acc) == 1, f'{st}')
                member_id = acc[0]['id'] if acc else None
                c.eq('the owner browser still acts as the owner (no picker)', _me(opg), {'status': 200, 'name': owner_name})
                st, d = _api('GET', '/accounts', account=owner_id)
                c.check('both accounts have no PIN', st == 200 and not any(a['has_pin'] for a in d['accounts']), st)
                c.check('two accounts: the Accounts section comes first', (_nav(opg) or [''])[0] == 'Accounts', _nav(opg))
                _scroll_to(opg, 'accounts')
                # R6-07: the owner recolours the member from its row
                opg.locator(f'[aria-label="Rename or recolour {member_name}"]').click()
                opg.locator('[aria-label="Account colour"] [role="radio"]').nth(3).click()
                opg.get_by_text('Save', exact=True).last.click()
                c.check('owner Edit saves', _wait_text(opg, f'Saved {member_name}'))
                st, d = _api('GET', '/accounts', account=owner_id)
                c.eq('… the member colour changed', [a['color'] for a in d['accounts'] if a['id'] == member_id], ['#e0912b'])
                rows = opg.locator('[data-account-row]')
                c.eq('the table lists both accounts', rows.count(), 2)
                c.check('the owner row says "you"', 'you' in opg.locator(f'[data-account-row="{owner_name}"]').inner_text())
                c.check('the member row has Remove', opg.locator(f'[aria-label="Remove {member_name}"]').count() == 1)
                c.eq('the owner row has no Remove', opg.locator(f'[aria-label="Remove {owner_name}"]').count(), 0)
                opg.screenshot(path=f'{SHOTS}/owner-accounts.png')

                # the rail's "Manage accounts" lands on the section
                C.go(opg, '/feed', settle=1000)
                opg.locator('[data-account-switcher]').click()
                opg.wait_for_timeout(300)
                opg.get_by_text('Add account…', exact=True).click()
                c.check('rail "Add account…" opens the Add dialog in Settings', _wait_text(opg, 'Start from a copy of'))
                c.check('… on /settings', '/settings' in opg.url, opg.url)
                opg.keyboard.press('Escape')
                c.check('Escape closes it', _gone_text(opg, 'Start from a copy of'))

                # ── MT-13: the member's own Settings ──
                mpg = C.seeded_page(b)
                try:
                    C.go(mpg, '/feed', settle=1200)
                    c.check('a fresh browser gets the picker', _wait_text(mpg, 'job hunting?'))
                    mpg.locator(f'[aria-label^="{member_name},"]').first.click()
                    c.check('the member (no PIN) opens in one click', _wait_text(mpg, f'Switched to {member_name}', 12000))
                    C.go(mpg, '/settings', settle=1500)
                    nav = _nav(mpg)
                    c.check('member: "Your account" comes first', (nav or [''])[0] == 'Your account', nav)
                    c.check('member: no Accounts table', 'Accounts' not in nav)
                    c.check('member: the footer names the owner', _wait_text(mpg, f'{owner_name} (owner)'))
                    c.check('member: adding people is the owner’s', C.body_has(mpg, 'Adding or removing people is'))
                    mpg.locator('[aria-label="Your colour"] [role="radio"]').nth(4).click()
                    c.check('member: picking a colour saves', _wait_text(mpg, 'Colour saved'))
                    st, d = _api('GET', '/accounts', account=owner_id)
                    col = [a['color'] for a in d['accounts'] if a['id'] == member_id]
                    c.eq('… to the account', col, ['#8a5cd6'])
                    st, _ = _api('GET', f'/accounts/{member_id}/export', account=member_id)
                    c.eq('member: Export my data answers', st, 200)
                    mpg.screenshot(path=f'{SHOTS}/member-your-account.png')
                    _mt14(c, opg, mpg, owner_id, owner_name, member_id, member_name)
                    errs, perrs = C.console_errors(mpg)
                    c.check('member: no page errors', not perrs, '; '.join(perrs[:2]))
                finally:
                    mpg.context.close()

                # ── MT-13: remove through the UI ──
                C.go(opg, '/settings#accounts', settle=1200)
                opg.locator(f'[aria-label="Remove {member_name}"]').click()
                c.check('Remove asks for the typed name', _wait_text(opg, f'Type “{member_name}” to confirm'))
                c.check('… itemises the counts', C.body_has(opg, 'applications') or C.body_has(opg, 'application'))
                c.check('… mentions the backup', C.body_has(opg, 'A full database backup is taken first'))
                opg.locator('[aria-label="Type the account name to confirm"]').fill(member_name.lower())
                c.eq('a wrong-case name keeps Remove disabled',
                     opg.get_by_text(f'Remove {member_name}', exact=True).last.get_attribute('aria-disabled'), 'true')
                opg.locator('[aria-label="Type the account name to confirm"]').fill(member_name)
                opg.get_by_text(f'Remove {member_name}', exact=True).last.click()
                c.check('removing toasts', _wait_text(opg, f'Removed {member_name}', 60000))
                st, d = _api('GET', '/accounts', account=owner_id)
                if st == 200 and len(d['accounts']) == 1:
                    member_id = None
                c.check('back to one account: Accounts keeps the owner row and Add',
                        _wait_text(opg, 'Just you') and opg.locator('[data-account-row]').count() == 1)
                errs, perrs = C.console_errors(opg)
                c.check('owner: no page errors', not perrs, '; '.join(perrs[:2]))
            finally:
                opg.context.close()
    finally:
        if member_id:
            for _ in range(24):
                st, d = _api('DELETE', f'/accounts/{member_id}', {'confirm_name': member_name}, account=owner_id)
                if not (st == 409 and isinstance(d, dict) and d.get('detail') == 'account_busy'):
                    break
                time.sleep(5)
            c.check('the member is removed (cleanup)', st == 200, f'{st} {d}')
        st, d = _api('GET', '/accounts')
        if st == 200 and len(d['accounts']) == 1 and d['accounts'][0]['has_pin']:
            st, d = _api('PATCH', f'/accounts/{owner_id}', {'pin': None, 'current_pin': OWNER_PIN})
            c.check("the owner's PIN is removed again", st == 200 and not d['account']['has_pin'], f'{st} {d}')
        st, d = _api('GET', '/accounts')
        n = len(d['accounts']) if st == 200 and isinstance(d, dict) else None
        c.eq('the install is back to exactly one account', n, 1)
        if n == 1:
            c.eq('… with no owner PIN', d['accounts'][0]['has_pin'], False)


def _sec_has(pg, sec, selector):
    return pg.evaluate("""([s, sel]) => {
      const head = document.querySelector(`[data-sec="sec-${s}"]`);
      return head ? head.parentElement.querySelectorAll(sel).length : -1;
    }""", [sec, selector])


def _tag(pg, sec):
    return pg.evaluate("""(s) => { const h = document.querySelector(`[data-sec="sec-${s}"] [data-scope-tag]`);
      return h ? h.getAttribute('data-scope-tag') : null }""", sec)


def _fetch(pg, method, path, body=None):
    return pg.evaluate("""async ([m, p, b]) => {
      const r = await fetch('/api' + p, { method: m, credentials: 'include',
        headers: { 'Content-Type': 'application/json' }, body: b ? JSON.stringify(b) : undefined });
      return r.status;
    }""", [method, path, body])


def _stub_scheduler_rows(pg):
    """Make Stats' scheduler table hold the owner-only `db_backup` row. With the scheduler running
    GET /api/scheduler/jobs already has it and nothing is stubbed; when it returns no such row
    (scheduler disabled) the route is answered with the real rows plus a stub one. True if stubbed."""
    rows = pg.evaluate("""async () => {
      const r = await fetch('/api/scheduler/jobs', { credentials: 'include' });
      return r.ok ? await r.json() : [];
    }""")
    if any(j.get('id') == 'db_backup' for j in rows):
        return False
    stub = {'id': 'db_backup', 'name': 'Back up database (pg_dump)', 'schedule': '0 3 * * *', 'next_run': None,
            'pending': False, 'trigger_url': '/db/backup', 'running': None, 'owner_only': True}

    def fulfil(route):
        route.fulfill(status=200, content_type='application/json', body=json.dumps(list(rows) + [stub]))

    pg.route(re.compile(r'/api/scheduler/jobs(\?|$)'), fulfil)
    return True


def _mt14(c, opg, mpg, owner_id, owner_name, member_id, member_name):
    """MT-14: scope tags, the member's locked view, the owner's Inherit/Own, the owner-PIN unlock."""
    st, s = _api('GET', '/settings', account=owner_id)
    tracer_before = (s or {}).get('tracer_links_base_url') or ''
    prep_before = (s or {}).get('prep_include', '')
    try:
        # ── member, locked ──
        C.go(mpg, '/settings', settle=1500)
        labels = mpg.evaluate("() => [...document.querySelectorAll('label, span')].map((e) => e.textContent.trim()).filter((t) => /^(You|AI · yours|Yours|Shared · )/.test(t))")
        c.check('member nav groups: You · AI · yours · Yours · Shared · owner',
                all(any(l.startswith(g) for l in labels) for g in ('You', 'AI · yours', 'Yours', f'Shared · {owner_name}')), labels[:8])
        c.eq('Advanced is tagged Owner only', _tag(mpg, 'advanced'), 'Owner only')
        c.eq('Display has no Per device tag for a member', _tag(mpg, 'appearance'), None)
        c.eq('Interview prep is inherited', _tag(mpg, 'prep'), f'Inherited from {owner_name}')
        c.eq('Global exclude is the member’s', _tag(mpg, 'exclude'), 'Yours')
        c.check('Advanced rows are locked', _sec_has(mpg, 'advanced', '[data-locked="true"]') >= 2)
        c.eq('… with a lock card', _sec_has(mpg, 'advanced', '[data-lock-card]'), 1)
        c.eq('Global exclude has no lock card', _sec_has(mpg, 'exclude', '[data-lock-card]'), 0)
        c.check('the header pill says locked', C.body_has(mpg, f'Owner sections locked · Unlock as {owner_name}'))
        # QA F7 / R6-06: locked rows stay readable by a screen reader (no `inert`), their
        # controls are out of the tab order, and the nav says "(locked)"
        a11y = mpg.evaluate("""() => {
          const head = document.querySelector('[data-sec="sec-advanced"]');
          const rows = head ? [...head.parentElement.querySelectorAll('[data-locked="true"]')] : [];
          const ctrls = rows.flatMap((r) => [...r.querySelectorAll('input, button, [tabindex]')]);
          return { rows: rows.length, inert: rows.filter((r) => r.hasAttribute('inert')).length,
                   ariaDisabled: rows.filter((r) => r.getAttribute('aria-disabled') === 'true').length,
                   tabbable: ctrls.filter((c) => c.tabIndex >= 0).length,
                   nav: (document.querySelector('[aria-label="Jump to Advanced"]') || {}).textContent || '' };
        }""")
        c.check('R6-06: locked rows are aria-disabled, not inert', a11y['rows'] >= 2 and a11y['inert'] == 0 and a11y['ariaDisabled'] == a11y['rows'], a11y)
        c.eq('R6-06: … their controls are out of the tab order', a11y['tabbable'], 0)
        c.check('R6-06: the nav entry says (locked)', a11y['nav'].endswith('(locked)'), a11y['nav'])
        # QA F7 / T7-02: no Run now on owner-only schedules for a locked member
        # P0-01: GET /scheduler/jobs is empty while the scheduler is off (JN_SCHEDULER_DISABLED=1 on
        # throwaway stacks), which would leave both checks vacuous or failing; stub a db_backup row then
        stubbed = _stub_scheduler_rows(mpg)
        C.go(mpg, '/stats', settle=1500)
        note = ' (stubbed scheduler row)' if stubbed else ''
        c.eq('T7-02: member sees no "Run Back up database" button' + note,
             mpg.locator('[aria-label="Run Back up database (pg_dump) now"]').count(), 0)
        c.check('T7-02: … the row says Owner only' + note, mpg.locator('[title^="Only the owner can run Back up database"]').count() == 1)
        C.go(mpg, '/settings', settle=1500)
        c.eq('a member cannot write an owner-only setting', _fetch(mpg, 'PATCH', '/settings', {'tracer_links_base_url': 'https://zzs.example'}), 403)
        c.eq('… nor an inherited one', _fetch(mpg, 'PATCH', '/settings', {'prep_include': 'posting'}), 403)
        _scroll_to(mpg, 'advanced')
        mpg.screenshot(path=f'{SHOTS}/member-lock-card.png')

        # ── owner flips Interview prep to Own for the member ──
        C.go(opg, '/settings', settle=1500)
        c.check('owner: Accounts has no scope tag; Advanced says Owner only', _tag(opg, 'advanced') == 'Owner only', _tag(opg, 'advanced'))
        # the Inherit / Own controls are hidden until the member's "Edit policies" switch is on
        c.eq('owner: no header picker', opg.locator('[data-member-policies]').count(), 0)
        c.eq('owner: no Inherit / Own control before the switch', opg.locator('[data-policy-for]').count(), 0)
        opg.locator(f'[aria-label="Edit {member_name}’s policies"]').click()
        opg.wait_for_timeout(300)
        c.check("owner: the switch shows the toggles, naming the member's policy (T7-06)", C.body_has(opg, f'{member_name}’s policy:'))
        _scroll_to(opg, 'prep')
        opg.locator('[data-sec="sec-prep"] [role="radio"]', has_text='Own').click()
        c.check('owner: flipping toasts in the header', _wait_text(opg, f'{member_name} keeps their own Interview prep'))
        st, pol = _api('GET', '/settings/policies', account=owner_id)
        c.eq('the policy is stored', (pol or {}).get(member_id, {}).get('prep'), 'own')
        opg.screenshot(path=f'{SHOTS}/owner-policy.png')

        # ── member edits it now ──
        C.go(mpg, '/settings', settle=1500)
        c.eq('member: Interview prep is theirs now', _tag(mpg, 'prep'), 'Yours')
        c.eq('… and not locked', _sec_has(mpg, 'prep', '[data-locked="true"]'), 0)
        _scroll_to(mpg, 'prep')
        mpg.locator('[aria-label="Include by default"]').click()
        mpg.get_by_text('Posting only', exact=True).click()
        c.check('member: the edit saves', _wait_text(mpg, 'Saved'))
        st, s = _api('GET', '/settings', account=member_id)
        c.eq('… to the member’s own value', (s or {}).get('prep_include'), 'posting')
        st, s = _api('GET', '/settings', account=owner_id)
        c.eq('… the owner’s value is untouched', (s or {}).get('prep_include'), prep_before)

        # ── QA F5: 'own' never shows the owner's values; 'inherit' shows "Set by <owner>" ──
        owner_li = (s or {}).get('linkedin_email') or ''
        st, ms = _api('GET', '/settings', account=member_id)
        c.eq('F5: member on own LinkedIn reads no owner email', (ms or {}).get('linkedin_email'), '')
        c.check('F5: … nor the owner’s address anywhere', not owner_li or owner_li not in str(ms))
        st, _ = _api('PATCH', '/settings/policies', {'account_id': member_id, 'section': 'linkedin', 'mode': 'inherit'}, account=owner_id)
        c.eq('F5: owner flips the member’s LinkedIn to inherit', st, 200)
        st, ms = _api('GET', '/settings', account=member_id)
        c.check('F5: inherit still never serves the owner’s address', not owner_li or owner_li not in str(ms))
        if owner_li:
            c.check('F5: … it is flagged set_by_owner', ((ms or {}).get('_meta') or {}).get('linkedin_email', {}).get('set_by_owner') is True)
            C.go(mpg, '/settings', settle=1500)
            _scroll_to(mpg, 'linkedin')
            c.check('F5: the member’s LinkedIn row reads “Set by <owner>”',
                    mpg.evaluate("""(t) => { const h = document.querySelector('[data-sec="sec-linkedin"]');
                      return !!h && h.parentElement.textContent.includes(t) }""", f'Set by {owner_name}'))
            c.check('F5: … and its Refresh cookie row is locked (owner-only refresh)',
                    mpg.evaluate("""() => { const h = document.querySelector('[data-sec="sec-linkedin"]');
                      return !!h && [...h.parentElement.querySelectorAll('[data-locked="true"]')]
                        .some((x) => x.textContent.includes('Session cookie')) }"""))
            mpg.screenshot(path=f'{SHOTS}/member-linkedin-set-by-owner.png')
        st, li = _api('GET', '/linkedin/session', account=member_id)
        c.check('F5: the member’s LinkedIn session is the owner’s (shared)', (li or {}).get('shared') is True, li)
        st, _ = _api('POST', '/linkedin/session/refresh', account=member_id)
        c.eq('F5: … and only the owner refreshes it', st, 403)
        _api('PATCH', '/settings/policies', {'account_id': member_id, 'section': 'linkedin', 'mode': 'own'}, account=owner_id)

        # ── D3: the owner has no PIN → the lock card is a plain Unlock (a privacy latch) ──
        _scroll_to(mpg, 'advanced')
        c.eq('no owner PIN: the lock card has no PIN pad', _sec_has(mpg, 'advanced', '[data-lock-card] input'), 0)
        c.check('… says it is only a privacy latch', C.body_has(mpg, 'only a privacy latch'))
        mpg.screenshot(path=f'{SHOTS}/member-lock-card-plain.png')
        mpg.locator('[data-lock-card="Advanced"] [aria-label^="Unlock the shared settings"]').click()
        c.check('the plain Unlock unlocks', _wait_text_ci(mpg, f'Unlocked as {owner_name} · until'))
        c.eq('… the lock cards are gone', _sec_has(mpg, 'advanced', '[data-lock-card]'), 0)
        mpg.get_by_text('Lock', exact=True).click()
        c.check('Lock relocks (plain)', _wait_text(mpg, f'Owner sections locked · Unlock as {owner_name}'))

        # ── the owner sets a PIN from Settings › Accounts; the member's card becomes a PIN pad ──
        C.go(opg, '/settings', settle=1500)
        opg.locator('[aria-label="Set your PIN"]').click()
        c.check('owner: Set PIN dialog', _wait_text(opg, 'New PIN'))
        opg.keyboard.type(OWNER_PIN, delay=60)
        c.check('… repeat', _wait_text(opg, 'Same PIN again'))
        opg.keyboard.type(OWNER_PIN, delay=60)
        c.check('… PIN set', _wait_text(opg, 'PIN set'))
        C.go(mpg, '/settings', settle=1500)

        # ── unlock with the owner PIN, edit an owner section, lock again ──
        _scroll_to(mpg, 'advanced')
        mpg.locator('[data-lock-card="Advanced"] input').first.click()
        mpg.keyboard.type('0000', delay=60)
        c.check('a wrong owner PIN is refused', _wait_text(mpg, 'Wrong PIN'))
        mpg.wait_for_timeout(300)
        mpg.locator('[data-lock-card="Advanced"] input').first.click()
        mpg.keyboard.type(OWNER_PIN, delay=60)
        # the Tag paints caps in some themes (innerText follows text-transform): match the attribute-free text case-insensitively
        c.check('the owner PIN unlocks', _wait_text_ci(mpg, f'Unlocked as {owner_name} · until'))
        me = mpg.evaluate("async () => (await (await fetch('/api/auth/me', { credentials: 'include' })).json()).owner_unlocked_until")
        c.check('… the server knows the unlock', bool(me), me)
        c.eq('the lock cards are gone', _sec_has(mpg, 'advanced', '[data-lock-card]'), 0)
        c.eq('… and the rows are live', _sec_has(mpg, 'tracer', '[data-locked="true"]'), 0)
        _scroll_to(mpg, 'tracer')
        box = mpg.locator('[aria-label="Base URL"]')
        box.click()
        box.fill('https://zzs-unlock.example')
        with mpg.expect_response(lambda r: r.request.method == 'PATCH' and r.url.endswith('/api/settings')) as resp:
            box.blur()
        c.eq('… the PATCH goes through as the owner', resp.value.status, 200)
        c.check('an owner section edit saves while unlocked', _wait_text(mpg, 'Saved'))
        st, s = _api('GET', '/settings', account=owner_id)
        c.eq('… as the install value', (s or {}).get('tracer_links_base_url'), 'https://zzs-unlock.example')
        mpg.screenshot(path=f'{SHOTS}/member-unlocked.png')
        mpg.get_by_text('Lock', exact=True).click()
        c.check('Lock relocks', _wait_text(mpg, f'Owner sections locked · Unlock as {owner_name}'))
        c.check('… and the rows lock again', _sec_has(mpg, 'tracer', '[data-locked="true"]') >= 1)
        c.eq('… also on the server', _fetch(mpg, 'PATCH', '/settings', {'tracer_links_base_url': 'https://x.example'}), 403)
    finally:
        st, _ = _api('PATCH', '/settings', {'tracer_links_base_url': tracer_before}, account=owner_id)
        st2, s = _api('GET', '/settings', account=owner_id)
        c.eq('the tracer base URL is restored', (s or {}).get('tracer_links_base_url'), tracer_before)
        c.eq('the owner’s prep_include is unchanged', (s or {}).get('prep_include'), prep_before)
