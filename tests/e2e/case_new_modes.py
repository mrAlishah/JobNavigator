"""new modes — the search modes added in PR #26 (CaribbeanJobs.com), PR #28 (Apify) and
PR #32 (TrulyRemoteWork.com).

Nothing here spends money or scrapes by default. Every row is a ZZE scratch search,
created inactive (or left to the Apify paused default), tracked so `flows-sweep`
catches a leftover, and deleted at the end of its case. The Apify key of this
install is not touched; the Apify Test is opened and CANCELLED, and the browser
aborts any POST /searches/{id}/test it might send anyway (a guard, asserted unused).

  modes-apify                 API: paused default, JobSpy boards refused (400),
                              results_wanted clamped to MAX_RESULTS on create and
                              PATCH, the boards list with prices. UI: the badge, the
                              Paused pill, the editor's cap and paused note, the
                              Test confirm with the estimated cost, Cancel runs nothing.
  modes-caribbeanjobs         API create (inactive); UI badge, editor notes (mode
                              note, Cloudflare block, terms notice). Test not clicked.
  modes-settings-apify        Settings › Apify: key row (no key set here), Check key,
                              the section's scope control matches its peers.
  modes-caribbeanjobs-live    JN_E2E_LIVE=1 only: a real Test from the UI; the modal
                              shows either the readable Cloudflare error or rows.
  modes-trulyremote           API create with no term or URL (inactive); UI olive badge,
                              row summary, editor note and the per-run cap label. Test
                              not clicked.
  modes-trulyremote-live      JN_E2E_LIVE=1 only: a real Test (title include "engineer")
                              from the UI; the modal shows kept rows and its params.
"""
import re
import time

from _suite import case
import _common as C
import h

APIFY_NAME = 'ZZE Apify (e2e)'
CJ_NAME = 'ZZE CaribbeanJobs (e2e)'
CJ_LIVE_NAME = 'ZZE CaribbeanJobs Live (e2e)'
TR_NAME = 'ZZE TrulyRemoteWork (e2e)'
TR_LIVE_NAME = 'ZZE TrulyRemoteWork Live (e2e)'

APIFY_BODY = {
    'name': APIFY_NAME,
    # `active` deliberately left out: the backend must start an Apify search paused
    'search_mode': 'apify',
    'search_term': 'zze e2e placeholder',
    'location': '',
    'country': 'usa',
    'sources': ['apify_linkedin', 'apify_indeed'],
    'hours_old': 24,
    'results_wanted': 999,                 # over MAX_RESULTS: clamped on create
    'auto_scoring_depth': 'off',
    'run_interval_minutes': 0,
}

CJ_BODY = {
    'name': CJ_NAME,
    'active': False,
    'search_mode': 'caribbeanjobs',
    'search_term': 'zze e2e placeholder',
    'results_wanted': 5,
    'auto_scoring_depth': 'off',
    'run_interval_minutes': 0,
}

TR_BODY = {
    'name': TR_NAME,
    'active': False,
    'search_mode': 'trulyremote',          # no term, no URL: the whole board
    'results_wanted': 7,
    'auto_scoring_depth': 'off',
    'run_interval_minutes': 0,
}

# frontend/src/sourceBlocks.js (MODE_BLOCKS / MODE_TERMS) and Searches.jsx noteFor()
TR_NOTE = 'full corpus of worldwide-remote roles'
TR_RW_LABEL = 'New jobs per run · 1–500'
TR_RW_SUB = 'The whole board is read; this caps how many new jobs’ pages one run opens'
CJ_TERMS = 'Terms: caribbeanjobs.com challenges automated visitors'
CJ_BLOCK = 'caribbeanjobs.com answers many networks with a Cloudflare challenge'
CJ_NOTE = 'Test reads at most 20 of those pages'
APIFY_NOTE_PAUSED = 'A new Apify search starts paused'
APIFY_RW_LABEL = 'Results wanted · 1–200 per board'
# backend/scraper/sources/caribbeanjobs.py CLOUDFLARE_BLOCKED
CJ_CLOUDFLARE = 'CaribbeanJobs is behind a Cloudflare challenge'


def _searches():
    st, d = h.get('/searches')
    return d if st == 200 and isinstance(d, list) else []


def _mk(body):
    st, d = h.post('/searches', body)
    if st < 300 and isinstance(d, dict) and d.get('id'):
        C.track('searches', d['id'])
        # a paid search must never sit on the scheduler, whatever the response says
        if d.get('search_mode') == 'apify' and d.get('active'):
            h.patch(f"/searches/{d['id']}", {'active': False})
    return st, d


def _drop(sid):
    st, _ = h.delete(f'/searches/{sid}')
    C.untrack('searches', sid)
    return st


def _usd(n):
    """frontend/src/apifyBoards.js usd(): dollars rounded UP to whole cents."""
    import math
    return f'${math.ceil(round(n * 100, 6)) / 100:.2f}'


# ── page probes ─────────────────────────────────────────────────────────────
BADGE_JS = """(name) => {
  const heads = [...document.querySelectorAll('main *')].filter((el) =>
    el.children.length === 0 && (el.textContent || '').trim() === name);
  for (const hd of heads) {
    const row = hd.parentElement;
    const b = row && [...row.querySelectorAll('span')].find((s) => /(^| )sm-/.test(s.className || ''));
    if (!b) continue;
    const cs = getComputedStyle(b);
    return { text: (b.textContent || '').trim(), cls: b.className, bg: cs.backgroundColor, fg: cs.color };
  }
  return null;
}"""

# the keyword (JobSpy) badge colours, from a probe element — no real row is read
KEYWORD_BADGE_JS = """() => {
  const root = document.querySelector('.jn-v2') || document.body;
  const s = document.createElement('span'); s.className = 'sm-keyword'; s.textContent = 'JOBSPY';
  root.appendChild(s);
  const cs = getComputedStyle(s); const o = { bg: cs.backgroundColor, fg: cs.color };
  s.remove(); return o;
}"""

# the colours a badge class renders with, from a probe element
PROBE_BADGE_JS = """(cls) => {
  const root = document.querySelector('.jn-v2') || document.body;
  const s = document.createElement('span'); s.className = cls; s.textContent = 'PROBE';
  root.appendChild(s);
  const cs = getComputedStyle(s); const o = { bg: cs.backgroundColor, fg: cs.color };
  s.remove(); return o;
}"""

DIALOG_JS = """() => { const d = document.querySelector('[role="dialog"]');
  return d ? (d.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 600) : null; }"""


def _has_ci(pg, text):
    """body_has, ignoring case: a Label renders uppercase, and innerText reports it so."""
    return pg.evaluate('(t) => (document.body.innerText || "").toLowerCase().includes(t.toLowerCase())', text)


def _badge(pg, name):
    return pg.evaluate(BADGE_JS, name)


def _open_edit(pg, name, others):
    """⋯ → Edit search on the row named `name`; False when the row is not uniquely found."""
    hit = C.row_index(pg, '[title="More actions"]', name, others)
    if not hit:
        return False
    pg.locator('[title="More actions"]').nth(hit['i']).click()
    pg.wait_for_timeout(350)
    item = pg.locator('[role="menu"] >> text="Edit search"')
    if item.count() == 0:
        return False
    item.first.click()
    pg.wait_for_timeout(600)
    return True


def _no_console_errors(c, pg, where):
    errs, perrs = C.console_errors(pg)
    c.check(f'no console errors on {where}', not errs, '; '.join(errs[:2]))
    c.check(f'no pageerrors on {where}', not perrs, '; '.join(perrs[:2]))


# ── Apify ───────────────────────────────────────────────────────────────────
@case('modes-apify')
def _apify(c):
    st, boards = h.get('/searches/apify-boards')
    c.check('GET /api/searches/apify-boards lists boards', st == 200 and isinstance(boards, list) and boards,
            f'{st} {str(boards)[:120]}')
    boards = boards if isinstance(boards, list) else []
    c.check('every board key carries the apify_ prefix',
            boards and all(str(b.get('value', '')).startswith('apify_') for b in boards),
            [b.get('value') for b in boards])
    c.check('every board names a price, a Test spend cap and the 20-job preview limit',
            boards and all(isinstance(b.get('price_per_1k'), (int, float)) and b['price_per_1k'] > 0
                           and isinstance(b.get('test_max_usd'), (int, float)) and b['test_max_usd'] > 0
                           and b.get('preview_limit') == 20 for b in boards),
            [(b.get('value'), b.get('price_per_1k'), b.get('test_max_usd'), b.get('preview_limit')) for b in boards])
    c.check('every board states the 200-result cap', boards and all(b.get('max_results') == 200 for b in boards))
    keys = {b.get('value') for b in boards}
    if not {'apify_linkedin', 'apify_indeed'} <= keys:
        c.check('the two boards this case uses exist', False, sorted(keys))
        return

    # JobSpy boards in apify mode: refused, nothing stored
    bad = dict(APIFY_BODY, name='ZZE Apify JobSpy boards (e2e)', sources=['indeed'], active=False)
    st, d = _mk(bad)
    c.eq('JobSpy boards in apify mode → 400', st, 400)
    c.check('…naming the board it refused', 'indeed' in str((d or {}).get('detail', '') if isinstance(d, dict) else d), d)
    if isinstance(d, dict) and d.get('id'):
        _drop(d['id'])
    st, d = _mk(dict(bad, name='ZZE Apify no boards (e2e)', sources=[]))
    c.eq('no board at all in apify mode → 400', st, 400)
    if isinstance(d, dict) and d.get('id'):
        _drop(d['id'])

    st, made = _mk(APIFY_BODY)
    c.check('POST an apify search with apify_* boards → 2xx', C.api_ok(st) and isinstance(made, dict), f'{st} {str(made)[:160]}')
    if not (isinstance(made, dict) and made.get('id')):
        return
    sid = made['id']
    try:
        c.eq('a new apify search starts paused (active omitted)', made.get('active'), False)
        c.eq('results_wanted 999 is clamped to 200 on create', made.get('results_wanted'), 200)
        c.eq('boards round-trip', made.get('sources'), APIFY_BODY['sources'])
        st, upd = h.patch(f'/searches/{sid}', {'results_wanted': 500})
        c.check('PATCH results_wanted 500', st == 200, st)
        c.eq('…clamped to 200 on PATCH too', (upd or {}).get('results_wanted'), 200)
        c.eq('…and the PATCH leaves it paused', (upd or {}).get('active'), False)
        st, upd = h.patch(f'/searches/{sid}', {'sources': ['apify_linkedin', 'indeed']})
        c.eq('PATCH a JobSpy board into an apify search → 400', st, 400)
        listed = next((s for s in _searches() if s.get('id') == sid), {})
        c.eq('…and the stored boards are unchanged', listed.get('sources'), APIFY_BODY['sources'])

        # the cost the confirm must name — the same arithmetic as apifyBoards.js testCost()
        pick = [b for b in boards if b['value'] in APIFY_BODY['sources']]
        est = sum(b['preview_limit'] * b['price_per_1k'] / 1000 for b in pick)
        cap = sum(b['test_max_usd'] for b in pick)
        want_est, want_cap = _usd(est), _usd(cap)

        requests = []
        with h.browser() as b:
            pg = C.mpage(b)
            try:
                pg.on('request', lambda r: requests.append((r.method, r.url)))
                # guard: even a broken Cancel can never start a paid run from here
                pg.route(re.compile(r'/api/searches/[^/]+/test(\?|$)'), lambda r: r.abort())
                C.go(pg, '/searches')
                others = [s['name'] for s in _searches() if s.get('id') != sid]

                bd = _badge(pg, APIFY_NAME)
                c.check('the apify row renders its APIFY badge', bool(bd) and bd['text'] == 'APIFY' and 'sm-apify' in bd['cls'], bd)
                kw = pg.evaluate(KEYWORD_BADGE_JS)
                c.check('…in colours of its own, not the keyword badge',
                        bool(bd) and (bd['bg'], bd['fg']) != (kw['bg'], kw['fg']), f'apify={bd} keyword={kw}')

                pill = C.row_index(pg, '[aria-pressed][title*="schedule"]', APIFY_NAME, others)
                c.check('the row has a status pill', pill is not None)
                if pill:
                    t = pg.locator('[aria-pressed][title*="schedule"]').nth(pill['i']).inner_text().strip()
                    c.eq('…reading Paused', t, 'Paused')

                # Test → confirm with the estimated cost → Cancel
                tsel = '[title^="Preview run"]'
                hit = C.row_index(pg, tsel, APIFY_NAME, others)
                c.check('the apify row offers Test', hit is not None, C.row_index_debug(pg, tsel, APIFY_NAME, others))
                if hit:
                    pg.locator(tsel).nth(hit['i']).click()
                    pg.wait_for_timeout(450)
                    dlg = pg.evaluate(DIALOG_JS)
                    c.check('Test raises a confirm dialog first', dlg is not None, dlg)
                    dlg = dlg or ''
                    c.check('…naming the search', APIFY_NAME in dlg, dlg)
                    c.check(f'…with the estimated cost {want_est}', f'Estimated cost {want_est}' in dlg, dlg)
                    c.check(f'…and the spend cap {want_cap}', f'{want_cap} at most' in dlg, dlg)
                    c.check('…naming both boards', 'LinkedIn' in dlg and 'Indeed' in dlg, dlg)
                    c.check('…offering Cancel and Run paid test', 'Cancel' in dlg and 'Run paid test' in dlg, dlg)
                    cancel = pg.locator('[role="dialog"] >> text="Cancel"')
                    if cancel.count():
                        cancel.first.click()
                    else:
                        pg.keyboard.press('Escape')
                    pg.wait_for_timeout(700)
                    c.check('Cancel closes the dialog', pg.locator('[role="dialog"]').count() == 0)
                    posted = [u for m, u in requests if m == 'POST' and re.search(r'/api/searches/[^/]+/test', u)]
                    c.check('no POST /searches/{id}/test was sent', not posted, posted)
                    _, act = h.get('/monitor/active')
                    mine = [a for a in (act or []) if a.get('scope_key') == sid]
                    c.check('nothing runs under its scope key on /monitor/active', not mine, mine)
                    c.check('the row shows no Test spinner', C.row_index(pg, tsel, APIFY_NAME, others) is not None
                            and 'Test' in pg.locator(tsel).nth(hit['i']).inner_text())

                # the editor: Results wanted cap and the paused note
                c.check('⋯ → Edit search opens the apify editor', _open_edit(pg, APIFY_NAME, others))
                c.check(f'the editor labels the cap “{APIFY_RW_LABEL}”', _has_ci(pg, APIFY_RW_LABEL))
                rw = pg.locator(f'input[aria-label="{APIFY_RW_LABEL}"]')
                c.eq('…holding the clamped 200', rw.first.input_value() if rw.count() else None, '200')
                c.eq('…with max=200 on the input', rw.first.get_attribute('max') if rw.count() else None, '200')
                c.check('the mode note says a new Apify search starts paused', C.body_has(pg, APIFY_NOTE_PAUSED))
                c.check('the editor lists the boards with their hints', C.body_has(pg, 'Each board is a separate Apify run'))
                cancel = pg.locator('main >> text="Cancel"')
                if cancel.count():
                    cancel.first.click()
                    pg.wait_for_timeout(300)
                _no_console_errors(c, pg, '/searches (apify)')
            finally:
                pg.unroute_all(behavior='ignoreErrors')
                pg.context.close()
        after = next((s for s in _searches() if s.get('id') == sid), {})
        c.eq('the UI pass left it paused', after.get('active'), False)
        c.eq('…and unchanged (Cancel saved nothing)', after.get('results_wanted'), 200)
    finally:
        st = _drop(sid)
        c.check('scratch apify search deleted', st in (200, 204, 404), st)
        c.check('…and gone from the list', not any(s.get('id') == sid for s in _searches()))


# ── CaribbeanJobs ───────────────────────────────────────────────────────────
@case('modes-caribbeanjobs')
def _caribbeanjobs(c):
    st, made = _mk(CJ_BODY)
    c.check('POST a caribbeanjobs search (inactive) → 2xx', C.api_ok(st) and isinstance(made, dict), f'{st} {str(made)[:160]}')
    if not (isinstance(made, dict) and made.get('id')):
        return
    sid = made['id']
    try:
        c.eq('mode stored', made.get('search_mode'), 'caribbeanjobs')
        c.eq('active:false honoured', made.get('active'), False)
        c.eq('term stored', made.get('search_term'), CJ_BODY['search_term'])

        requests = []
        with h.browser() as b:
            pg = C.mpage(b)
            try:
                pg.on('request', lambda r: requests.append((r.method, r.url)))
                C.go(pg, '/searches')
                others = [s['name'] for s in _searches() if s.get('id') != sid]
                bd = _badge(pg, CJ_NAME)
                c.check('the row renders its CARIBBEANJOBS.COM badge',
                        bool(bd) and bd['text'] == 'CARIBBEANJOBS.COM' and 'sm-caribbeanjobs' in bd['cls'], bd)
                kw = pg.evaluate(KEYWORD_BADGE_JS)
                c.check('…in colours of its own, not the keyword badge',
                        bool(bd) and (bd['bg'], bd['fg']) != (kw['bg'], kw['fg']), f'cj={bd} keyword={kw}')
                c.check('the row offers Test (not clicked: it would reach caribbeanjobs.com)',
                        C.row_index(pg, '[title^="Preview run"]', CJ_NAME, others) is not None)

                c.check('⋯ → Edit search opens the caribbeanjobs editor', _open_edit(pg, CJ_NAME, others))
                c.check('the mode note says Test reads at most 20 detail pages', C.body_has(pg, CJ_NOTE))
                c.check('the Cloudflare block is stated in the editor', C.body_has(pg, CJ_BLOCK))
                c.check('the terms notice is shown in the editor', C.body_has(pg, CJ_TERMS))
                c.check('…pointing at LEGAL_DISCLAIMER.md', C.body_has(pg, 'LEGAL_DISCLAIMER.md'))
                c.check('the URL field names caribbeanjobs.com', _has_ci(pg, 'caribbeanjobs.com URL'))
                cancel = pg.locator('main >> text="Cancel"')
                if cancel.count():
                    cancel.first.click()
                    pg.wait_for_timeout(300)
                posted = [u for m, u in requests if m in ('POST', 'PATCH') and '/api/searches' in u]
                c.check('opening and cancelling the editor wrote nothing', not posted, posted)
                _no_console_errors(c, pg, '/searches (caribbeanjobs)')
            finally:
                pg.context.close()
    finally:
        st = _drop(sid)
        c.check('scratch caribbeanjobs search deleted', st in (200, 204, 404), st)
        c.check('…and gone from the list', not any(s.get('id') == sid for s in _searches()))


# ── TrulyRemoteWork ─────────────────────────────────────────────────────────
@case('modes-trulyremote')
def _trulyremote(c):
    st, made = _mk(TR_BODY)
    c.check('POST a trulyremote search with no term or URL (inactive) → 2xx',
            C.api_ok(st) and isinstance(made, dict), f'{st} {str(made)[:160]}')
    if not (isinstance(made, dict) and made.get('id')):
        return
    sid = made['id']
    try:
        c.eq('mode stored', made.get('search_mode'), 'trulyremote')
        c.eq('active:false honoured', made.get('active'), False)

        requests = []
        with h.browser() as b:
            pg = C.mpage(b)
            try:
                pg.on('request', lambda r: requests.append((r.method, r.url)))
                C.go(pg, '/searches')
                others = [s['name'] for s in _searches() if s.get('id') != sid]
                bd = _badge(pg, TR_NAME)
                c.check('the row renders its TRULYREMOTEWORK.COM badge',
                        bool(bd) and bd['text'] == 'TRULYREMOTEWORK.COM' and 'sm-trulyremote' in bd['cls'], bd)
                jr = pg.evaluate(PROBE_BADGE_JS, 'sm-jobright')
                c.check('…in olive, not Jobright’s teal',
                        bool(bd) and (bd['bg'], bd['fg']) != (jr['bg'], jr['fg']), f'tr={bd} jobright={jr}')
                c.check('the row summary reads "Worldwide remote · max 7 new"', C.body_has(pg, 'Worldwide remote · max 7 new'))
                c.check('the row offers Test with no term or URL (not clicked: it would reach the board)',
                        C.row_index(pg, '[title^="Preview run"]', TR_NAME, others) is not None)

                c.check('⋯ → Edit search opens the trulyremote editor', _open_edit(pg, TR_NAME, others))
                c.check('the mode note explains the whole-board read', C.body_has(pg, TR_NOTE))
                c.check('…and the 20-page Test cap', C.body_has(pg, 'Test reads at most 20 of those pages'))
                c.check('the cap is labelled per run', _has_ci(pg, TR_RW_LABEL))
                c.check('…with its sub-text', C.body_has(pg, TR_RW_SUB))
                c.check('no terms notice for this board', not C.body_has(pg, 'Terms:'))
                cancel = pg.locator('main >> text="Cancel"')
                if cancel.count():
                    cancel.first.click()
                    pg.wait_for_timeout(300)
                posted = [u for m, u in requests if m in ('POST', 'PATCH') and '/api/searches' in u]
                c.check('opening and cancelling the editor wrote nothing', not posted, posted)
                _no_console_errors(c, pg, '/searches (trulyremote)')
            finally:
                pg.context.close()
    finally:
        st = _drop(sid)
        c.check('scratch trulyremote search deleted', st in (200, 204, 404), st)
        c.check('…and gone from the list', not any(s.get('id') == sid for s in _searches()))


# ── Settings › Apify ────────────────────────────────────────────────────────
SECTION_JS = """(id) => {
  const hd = document.querySelector(`[data-sec="sec-${id}"]`);
  if (!hd) return null;
  const wrap = hd.parentElement;
  const tag = hd.querySelector('[data-scope-tag]');
  const pol = hd.querySelector('[data-policy-for]');
  const on = pol && pol.querySelector('[aria-checked="true"], [aria-pressed="true"], [aria-selected="true"]');
  return {
    title: (hd.innerText || '').split('\\n')[0].trim(),
    text: (wrap.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 700),
    scope: tag ? 'tag:' + tag.getAttribute('data-scope-tag') : pol ? 'policy' : 'none',
    policyOn: on ? (on.innerText || '').trim() : null,
  };
}"""


@case('modes-settings-apify')
def _settings_apify(c):
    st, s = h.get('/settings')
    key_set = bool((s or {}).get('apify_api_key')) if isinstance(s, dict) else None
    c.note(f'apify_api_key set on this install: {key_set}')
    st, secs = h.get('/settings/sections')
    sec = next((x for x in (secs or []) if isinstance(x, dict) and x.get('id') == 'apify'), None)
    c.check('the apify section is registered', sec is not None, str(secs)[:160])
    if sec:
        c.eq('…as a policy section', sec.get('scope'), 'policy')
        c.eq('…own by default', sec.get('default'), 'own')
        c.eq('…holding apify_api_key', sec.get('keys'), ['apify_api_key'])
    with h.browser() as b:
        pg = C.mpage(b)
        try:
            pg.route(re.compile(r'/api/llm/models'),
                     lambda r: r.fulfill(status=200, content_type='application/json', body='[]'))
            requests = []
            pg.on('request', lambda r: requests.append((r.method, r.url)))
            C.go(pg, '/settings', settle=1200)
            # owner with members: the Inherit / Own controls show only once a member's
            # "Edit policies" switch is on (it only shows them, it writes nothing)
            edit = pg.locator('[role="switch"][aria-label$="’s policies"]')
            if edit.count():
                edit.first.click()
                pg.wait_for_timeout(300)
            ap = pg.evaluate(SECTION_JS, 'apify')
            c.check('Settings renders an Apify section', bool(ap) and ap['title'] == 'Apify', ap)
            if not ap:
                return
            c.check('…with the Apify API key row', 'Apify API key' in ap['text'], ap['text'][:200])
            c.check('…and the Key check row with its button', 'Key check' in ap['text'] and 'Check key' in ap['text'],
                    ap['text'][:300])
            inp = pg.locator('[data-sec="sec-apify"] >> xpath=.. >> input').first
            if key_set is False:
                c.eq('no key set: the field is empty', inp.input_value() if inp.count() else None, '')
                c.eq('…showing the apify_api_… placeholder', inp.get_attribute('placeholder') if inp.count() else None,
                     'apify_api_…')
            # the scope control matches the other per-account credential sections (same policy, own by default)
            peers = {pid: pg.evaluate(SECTION_JS, pid) for pid in ('jobright', 'linkedin')}
            peer_scopes = {k: (v or {}).get('scope') for k, v in peers.items()}
            c.check('the Apify head carries the same scope control as Jobright.ai and LinkedIn',
                    all(v == ap['scope'] for v in peer_scopes.values()), f"apify={ap['scope']} peers={peer_scopes}")
            if ap['scope'] == 'policy':
                c.note(f"policy toggle shows {ap['policyOn']!r} for the selected member")
            c.note(f"scope control: {ap['scope']}")
            writes = [(m, u) for m, u in requests if m in ('POST', 'PATCH', 'PUT', 'DELETE') and '/api/settings' in u]
            c.check('looking at the section wrote no setting', not writes, writes)
            _no_console_errors(c, pg, '/settings')
        finally:
            pg.unroute_all(behavior='ignoreErrors')
            pg.context.close()


# ── CaribbeanJobs Test — JN_E2E_LIVE only ───────────────────────────────────
@case('modes-caribbeanjobs-live')
def _caribbeanjobs_live(c):
    C.live_only(c, 'the CaribbeanJobs Test reads caribbeanjobs.com (up to 20 detail pages)')
    st, made = _mk(dict(CJ_BODY, name=CJ_LIVE_NAME, search_term='engineer'))
    if not (isinstance(made, dict) and made.get('id')):
        c.check('created the scratch search', False, st)
        return
    sid = made['id']
    try:
        with h.browser() as b:
            pg = C.mpage(b)
            try:
                C.go(pg, '/searches')
                others = [s['name'] for s in _searches() if s.get('id') != sid]
                tsel = '[title^="Preview run"]'
                hit = C.row_index(pg, tsel, CJ_LIVE_NAME, others)
                if not c.check('the row offers Test', hit is not None):
                    return
                pg.locator(tsel).nth(hit['i']).click()
                pg.wait_for_timeout(500)
                first = pg.evaluate(DIALOG_JS)
                c.check('no paid-test confirm for a free mode', not first or first.startswith('Test run'), first)
                dlg = None
                t0 = time.time()
                while time.time() - t0 < 240:
                    dlg = pg.evaluate(DIALOG_JS)
                    if dlg and dlg.startswith('Test run') and ('Cloudflare' in dlg or 'Kept' in dlg
                                                               or 'No results' in dlg or 'error' in dlg.lower()):
                        break
                    pg.wait_for_timeout(1500)
                c.check('the Test modal lands within 4 minutes', bool(dlg), dlg)
                dlg = dlg or ''
                blocked = CJ_CLOUDFLARE in dlg
                c.note('Cloudflare blocked this network' if blocked else f'result: {dlg[:160]}')
                c.check('…with the readable Cloudflare error, or results',
                        blocked or ('Kept' in dlg or 'Filtered' in dlg or 'No results' in dlg), dlg[:300])
                c.check('…never a raw traceback or HTML', '<html' not in dlg.lower() and 'Traceback' not in dlg, dlg[:200])
                _no_console_errors(c, pg, '/searches (caribbeanjobs Test)')
            finally:
                pg.context.close()
    finally:
        st = _drop(sid)
        c.check('scratch search deleted', st in (200, 204, 404), st)


# ── TrulyRemoteWork Test — JN_E2E_LIVE only ─────────────────────────────────
@case('modes-trulyremote-live')
def _trulyremote_live(c):
    C.live_only(c, 'the TrulyRemoteWork Test reads the board (its corpus, cached, and up to 20 job pages)')
    st, made = _mk(dict(TR_BODY, name=TR_LIVE_NAME, title_include_keywords=['engineer']))
    if not (isinstance(made, dict) and made.get('id')):
        c.check('created the scratch search', False, st)
        return
    sid = made['id']
    try:
        with h.browser() as b:
            pg = C.mpage(b)
            try:
                C.go(pg, '/searches')
                others = [s['name'] for s in _searches() if s.get('id') != sid]
                tsel = '[title^="Preview run"]'
                hit = C.row_index(pg, tsel, TR_LIVE_NAME, others)
                if not c.check('the row offers Test', hit is not None):
                    return
                pg.locator(tsel).nth(hit['i']).click()
                pg.wait_for_timeout(500)
                dlg = None
                t0 = time.time()
                while time.time() - t0 < 180:
                    dlg = pg.evaluate(DIALOG_JS)
                    if dlg and dlg.startswith('Test run') and ('Kept' in dlg or 'No results' in dlg
                                                               or 'error' in dlg.lower()):
                        break
                    pg.wait_for_timeout(1500)
                c.check('the Test modal lands within 3 minutes', bool(dlg), dlg)
                dlg = dlg or ''
                c.note(f'result: {dlg[:200]}')
                c.check('…with results', 'Kept' in dlg, dlg[:300])
                c.check('…the worldwide-remote params', 'worldwide remote · 7 new per run' in dlg, dlg[:300])
                c.check('…never a raw traceback or HTML', '<html' not in dlg.lower() and 'Traceback' not in dlg, dlg[:200])
                _no_console_errors(c, pg, '/searches (trulyremote Test)')
            finally:
                pg.context.close()
    finally:
        st = _drop(sid)
        c.check('scratch search deleted', st in (200, 204, 404), st)
