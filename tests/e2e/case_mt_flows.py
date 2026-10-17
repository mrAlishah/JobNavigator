"""mt-flows — two-account flows end to end (MT-21): Add dialog WITH copy-from, picker + PIN,
a member that sees only its own data, "Copy from…" (MT-20), the member's Telegram link
dialog and Gmail row (MT-17/19), Export my data, the owner's Stats "All accounts" toggle
(MT-20), then removal through the Remove dialog.

SAFE FOR THE LIVE DB, by construction (the mt-switch / mt-settings pattern):
- SKIPs unless the install has exactly ONE account and the owner has no PIN when it starts;
- the owner PIN it sets (Settings › Accounts › Set PIN, QA F2) is removed again in
  `finally`;
- it creates one member `ZZM Member <n>` copying persona, résumés, companies and searches
  from the owner (nothing else, never jobs), and removes it in `finally` (through the Remove
  dialog when the flow gets there, else the API; the API takes its own pre-removal pg_dump);
- nothing is sent to Telegram and Google is never opened;
- ends asserting exactly one account and no owner PIN.
Take a manual backup before running it anyway.
"""
import io
import json
import os
import random
import time
import urllib.error
import urllib.request
import zipfile

from _suite import case
import _common as C
import h

OWNER_PIN = '7391'
MEMBER_PIN = '2468'
SHOTS = '/tmp/v2t/shots/mt21'
EXT_MODES = ('extension', 'linkedin_extension')


def _api(method, path, body=None, account=None):
    data = None if body is None else json.dumps(body).encode()
    headers = {'X-API-Key': h.KEY, 'Content-Type': 'application/json'}
    if account:   # QA F1: a signed token from verify-pin (the owner's PIN opens both accounts)
        headers['X-Account-Token'] = C.account_token(account, OWNER_PIN)
    req = urllib.request.Request(h.BASE + '/api' + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
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


def _scroll_to(pg, sec):
    pg.evaluate("""(s) => { const el = document.querySelector(`[data-sec="sec-${s}"]`); if (el) el.scrollIntoView({ block: 'start' }) }""", sec)
    pg.wait_for_timeout(300)


def _list(path, account):
    st, d = _api('GET', path, account=account)
    return d if st == 200 and isinstance(d, list) else None


def _counts(pg):
    r, _ = C.rail_counts_within(pg, 6000)
    return (r or {}).get('counts') or {}


@case('mt-flows')
def _mt_flows(c):
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

    # what the owner has now (the yardstick for every copy check)
    o_res = _list('/resumes', owner_id) or []
    o_base = [r for r in o_res if r.get('is_base')]
    o_tailored = [r for r in o_res if not r.get('is_base')]
    o_cos = _list('/companies', owner_id) or []
    o_searches = [s for s in (_list('/searches', owner_id) or []) if s.get('search_mode') not in EXT_MODES]
    st, o_persona = _api('GET', '/persona', account=owner_id)
    c.check('the owner has résumés and companies to copy', bool(o_base) and bool(o_cos), f'{len(o_base)} base, {len(o_cos)} companies')
    foreign_resume = (o_tailored or o_res or [{}])[0].get('id')

    member_name = f'ZZM Member {random.randint(100, 999)}'
    member_id = None
    try:
        with h.browser() as b:
            opg = C.seeded_page(b)
            mpg = None
            try:
                # ── create through Settings › Accounts › Add, with copy-from ──
                # QA F2: the owner PIN is optional (D3); this case keeps testing the PIN paths,
                # so it sets one from the always-present Accounts section (D2) first
                C.go(opg, '/settings', settle=1200)
                opg.locator('[aria-label="Set your PIN"]').click()
                c.check('the owner sets a PIN from Settings › Accounts', _wait_text(opg, 'New PIN'))
                opg.keyboard.type(OWNER_PIN, delay=60)
                c.check('… and repeats it', _wait_text(opg, 'Same PIN again'))
                opg.keyboard.type(OWNER_PIN, delay=60)
                c.check('… PIN set', _wait_text(opg, 'PIN set'))
                opg.get_by_text('+ Add account', exact=True).click()
                c.check('the Add account form opens', _wait_text(opg, 'Start from a copy of'))
                opg.locator('[aria-label="Account name"]').fill(member_name)
                opg.locator('[aria-label="PIN for the new account"]').fill(MEMBER_PIN)
                opg.locator('[aria-label="Copy from account"]').click()
                opg.get_by_text(f'{owner_name} (owner)', exact=True).click()
                c.check('picking a source lists the items to copy', _wait_text(opg, 'Base résumés'))
                for label in ('Settings', 'Cover-letter voices', 'Global excludes'):   # copy exactly the four the spec names
                    opg.locator(f'[role="checkbox"][aria-label="{label}"]').click()
                for label, want in (('Persona', 'true'), ('Base résumés', 'true'), ('Companies', 'true'), ('Searches', 'true'),
                                    ('Settings', 'false'), ('Cover-letter voices', 'false'), ('Global excludes', 'false')):
                    got = opg.locator(f'[role="checkbox"][aria-label="{label}"]').get_attribute('aria-checked')
                    if got != want:
                        c.check(f'item "{label}" ticked as intended', False, got)
                opg.screenshot(path=f'{SHOTS}/add-with-copy.png')
                opg.get_by_text('Add account', exact=True).last.click()
                c.check('adding toasts', _wait_text(opg, f'Added {member_name}', 120000))
                st, d = _api('GET', '/accounts', account=owner_id)
                acc = [a for a in (d or {}).get('accounts', []) if a['name'] == member_name]
                c.check('the member exists with a PIN', st == 200 and len(acc) == 1 and acc[0]['has_pin'], f'{st}')
                if not acc:
                    return
                member_id = acc[0]['id']

                # ── the copies exist and are due ──
                m_base = [r for r in (_list('/resumes', member_id) or []) if r.get('is_base')]
                c.eq('base résumés copied (same count)', len(m_base), len(o_base))
                c.check('… under new ids', not ({r['id'] for r in m_base} & {r['id'] for r in o_base}))
                m_cos = _list('/companies', member_id) or []
                c.eq('companies copied (same names)', sorted(x['name'] for x in m_cos), sorted(x['name'] for x in o_cos))
                c.check('copied companies are due (never scraped)', all(not x.get('last_scraped_at') for x in m_cos),
                        sum(1 for x in m_cos if x.get('last_scraped_at')))
                m_searches = _list('/searches', member_id) or []
                m_user = [s for s in m_searches if s.get('search_mode') not in EXT_MODES]
                c.eq('searches copied (same names)', sorted(s['name'] for s in m_user), sorted(s['name'] for s in o_searches))
                c.check('copied searches are due (never run)', all(not s.get('last_run_at') for s in m_user))
                c.check('the member has its own two built-in extension searches',
                        len([s for s in m_searches if s.get('search_mode') in EXT_MODES]) == 2)
                st, m_persona = _api('GET', '/persona', account=member_id)
                same = [k for k in ('contact', 'work_auth', 'preferences', 'qa_bank') if k in (o_persona or {})
                        and (o_persona or {}).get(k) == (m_persona or {}).get(k)]
                c.check('the Persona was copied', st == 200 and bool(same), same)
                c.eq('no jobs came along', len(_list('/applications', member_id) or []), 0)
                c.check('the table lists both accounts',
                        _wait_text(opg, member_name) and opg.locator('[data-account-row]').count() == 2)

                # ── the member's browser: picker + PIN ──
                mpg = C.seeded_page(b)
                C.go(mpg, '/feed', settle=1200)
                c.check('a fresh browser gets the account picker', _wait_text(mpg, 'job hunting?'))
                mpg.locator(f'[aria-label^="{member_name},"]').first.click()
                c.check("the member's tile opens its PIN pad", _wait_text(mpg, f"Enter {member_name}'s PIN"))
                mpg.keyboard.type(MEMBER_PIN, delay=60)
                c.check('the right PIN switches', _wait_text(mpg, f'Switched to {member_name}', 20000))
                mpg.wait_for_timeout(800)
                c.eq('this browser acts as the member', _me(mpg), {'status': 200, 'name': member_name})

                # ── the member sees only its own data ──
                C.go(opg, '/feed', settle=1500)
                owner_counts = _counts(opg)
                C.go(mpg, '/feed', settle=1500)
                member_counts = _counts(mpg)
                c.check('the rail counts differ between the accounts', owner_counts != member_counts, f'{owner_counts} vs {member_counts}')
                c.check('… the owner has applications, the member none',
                        owner_counts.get('/applications') not in ('0', '', None) and member_counts.get('/applications') in ('0', ''),
                        f"{owner_counts.get('/applications')} vs {member_counts.get('/applications')}")
                c.check('… the member has no feed rows', member_counts.get('/feed') in ('0', ''), member_counts.get('/feed'))
                c.eq('… the member shelf holds exactly the copied base résumés', member_counts.get('/resumes'), str(len(m_base)))
                c.check('… tailored copies stayed behind', len(_list('/resumes', member_id) or []) == len(o_base),
                        f'member {len(_list("/resumes", member_id) or [])} vs owner {len(o_res)} total')
                mpg.screenshot(path=f'{SHOTS}/member-feed.png')
                if foreign_resume:
                    C.go(mpg, f'/resumes/{foreign_resume}', settle=800)
                    c.check("an owner résumé's URL gives the not-found state", _wait_text(mpg, 'no longer exists'))
                    c.check('… and lands on the shelf', mpg.url.rstrip('/').endswith('/resumes'), mpg.url)
                    st, _ = _api('GET', f'/resumes/{foreign_resume}', account=member_id)
                    c.eq('… the API says 404 too', st, 404)

                # ── member "Copy from…" one company from the owner (MT-20) ──
                victim = next((x for x in m_cos if x.get('name')), None)
                st, _ = _api('DELETE', f'/companies/{victim["id"]}', account=member_id)
                c.check('(setup) the member drops one copied company', st in (200, 204), st)
                C.go(mpg, '/companies', settle=1500)
                mpg.get_by_role('button', name='Copy from…').click()
                c.check('the Copy a company dialog opens', _wait_text(mpg, 'Copy a company from…'))
                c.check('… listing the owner by name', _wait_text_ci(mpg, owner_name))
                row = mpg.locator(f'[title="Copy {victim["name"]}"]').first
                row.scroll_into_view_if_needed()
                row.click()
                c.check('one click copies it and closes the dialog', _gone_text(mpg, 'Copy a company from…', 20000))
                m_cos2 = _list('/companies', member_id) or []
                new = [x for x in m_cos2 if x['name'] == victim['name']]
                c.eq('the member has it again, once', len(new), 1)
                c.check('… due at once, résumé choices left to the member', bool(new) and not new[0].get('last_scraped_at')
                        and not (new[0].get('selected_resume_ids') or []), new and new[0].get('selected_resume_ids'))
                c.eq('… and nothing else changed', len(m_cos2), len(m_cos))

                # ── member Settings: Telegram link dialog, Gmail row ──
                C.go(mpg, '/settings', settle=1500)
                row = mpg.get_by_text('Link Telegram', exact=True).first
                row.scroll_into_view_if_needed()
                c.check('Telegram shows "Not linked"', _wait_text(mpg, 'Not linked'))
                mpg.get_by_text('Link…', exact=True).first.click()
                c.check('"Link Telegram" opens a dialog', _wait_text(mpg, 'within 10 minutes'))
                code = mpg.evaluate("""() => { const e = [...document.querySelectorAll('input, textarea')]
                  .map((i) => i.value).find((v) => /^\\/start \\d{6}$/.test(v)); return e || null }""")
                c.check('… showing "/start <6-digit code>"', bool(code), code)
                mpg.screenshot(path=f'{SHOTS}/member-telegram-link.png')
                mpg.get_by_text('Done', exact=True).last.click()
                mpg.wait_for_timeout(500)
                st, g = _api('GET', '/gmail/status', account=member_id)
                gmail_line = 'Not connected' if (g or {}).get('configured') else 'Not set up on this install'
                _scroll_to(mpg, 'emailclass')
                c.check(f'the Gmail row says "{gmail_line}"', _wait_text(mpg, gmail_line))
                c.eq('… and the API agrees: not connected', (g or {}).get('status'), 'none')
                mpg.screenshot(path=f'{SHOTS}/member-gmail.png')

                # ── member: Your account › Export my data ──
                _scroll_to(mpg, 'account')
                with mpg.expect_download(timeout=120000) as dl:
                    mpg.get_by_text('Download zip', exact=True).click()
                path = dl.value.path()
                name = dl.value.suggested_filename
                c.check('Export my data downloads a zip', name.endswith('.zip'), name)
                try:
                    with zipfile.ZipFile(path) as z:
                        names = z.namelist()
                    c.check('… with a manifest and the account data', any('manifest' in n for n in names) and any('resumes' in n for n in names), names[:6])
                except Exception as e:   # noqa: BLE001
                    c.check('… the file is a readable zip', False, str(e))

                errs, perrs = C.console_errors(mpg)
                c.check('member: no page errors', not perrs, '; '.join(perrs[:2]))

                # ── switch back to the owner (Ctrl+Shift+1 + owner PIN) ──
                mpg.keyboard.press('Control+Shift+Digit1')
                c.check("Ctrl+Shift+1 asks for the owner's PIN", _wait_text(mpg, f"Enter {owner_name}'s PIN"))
                mpg.keyboard.type(OWNER_PIN, delay=60)
                c.check('the owner PIN switches back', _wait_text(mpg, f'Switched to {owner_name}', 20000))
                mpg.wait_for_timeout(800)
                c.eq('this browser acts as the owner again', _me(mpg), {'status': 200, 'name': owner_name})

                # ── owner Stats: All accounts → a by-account line ──
                C.go(mpg, '/stats', settle=2500)
                sw = mpg.locator('[aria-label="Sum LLM costs across all accounts"]')
                c.eq('the owner sees the "All accounts" switch', sw.count(), 1)
                if sw.count():
                    with mpg.expect_response(lambda r: 'llm-costs' in r.url and 'all_accounts=1' in r.url, timeout=20000) as resp:
                        sw.click()
                    body = resp.value.json()
                    c.check('the all-accounts query answers per account', isinstance(body.get('by_account'), list), body.get('by_account'))
                    if body.get('by_account'):
                        c.check('a "By account:" line shows', _wait_text(mpg, 'By account:'))
                    else:
                        c.note('no LLM calls in the default period: the by-account line has nothing to show')
                    mpg.screenshot(path=f'{SHOTS}/owner-stats-all.png')

                # ── remove through the Remove dialog ──
                C.go(mpg, '/settings#accounts', settle=1500)
                mpg.locator(f'[aria-label="Remove {member_name}"]').click()
                c.check('Remove asks for the typed name', _wait_text(mpg, f'Type “{member_name}” to confirm'))
                mpg.locator('[aria-label="Type the account name to confirm"]').fill(member_name)
                mpg.get_by_text(f'Remove {member_name}', exact=True).last.click()
                c.check('removing toasts', _wait_text(mpg, f'Removed {member_name}', 180000))
                st, d = _api('GET', '/accounts', account=owner_id)
                if st == 200 and len(d['accounts']) == 1:
                    member_id = None
                c.check('back to one account', member_id is None)
                errs, perrs = C.console_errors(mpg)
                c.check('owner: no page errors', not perrs, '; '.join(perrs[:2]))
            finally:
                if mpg:
                    mpg.context.close()
                opg.context.close()
    finally:
        if member_id:
            for _ in range(36):   # a scheduler run for the member → 409 account_busy: wait it out
                st, d = _api('DELETE', f'/accounts/{member_id}', {'confirm_name': member_name}, account=owner_id)
                if not (st == 409 and isinstance(d, dict) and d.get('detail') == 'account_busy'):
                    break
                time.sleep(5)
            c.check('the member is removed (cleanup)', st == 200, f'{st} {d}')
            if st == 200:
                c.note(f'pre-removal backup: {d.get("backup")}')
        st, d = _api('GET', '/accounts')
        if st == 200 and len(d['accounts']) == 1 and d['accounts'][0]['has_pin']:
            st, d = _api('PATCH', f'/accounts/{owner_id}', {'pin': None, 'current_pin': OWNER_PIN})
            c.check("the owner's PIN is removed again", st == 200 and not d['account']['has_pin'], f'{st} {d}')
        st, d = _api('GET', '/accounts')
        n = len(d['accounts']) if st == 200 and isinstance(d, dict) else None
        c.eq('the install is back to exactly one account', n, 1)
        if n == 1:
            c.eq('… with no owner PIN', d['accounts'][0]['has_pin'], False)
