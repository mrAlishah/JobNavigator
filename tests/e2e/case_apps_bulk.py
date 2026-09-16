"""apps-bulk — the Applications screen's bulk selection bar.

Covers the whole round trip the Feed already had and Applications gained:
⌘/Ctrl-pick two rows → the floating bar counts them → a stage control moves both
through POST /api/applications/bulk-update → the rows regroup under the new stage
→ the undo toast puts them back through the same endpoint, one call per previous
status.

Entirely route-mocked, like case_apps_select: GET /api/applications and the two
bulk endpoints are answered by the test, so nothing is created, changed or left
behind in the real database.
"""
import json
import re

from _suite import case
import _common as C
import h

TS = '2026-08-01T12:00:00+00:00'
TITLE_A = 'ZZE Bulk Role A'
TITLE_B = 'ZZE Bulk Role B'


def _app(id_, title, status):
    return {
        'id': id_, 'job_id': f'zze-job-{id_}', 'status': status, 'applied_at': TS,
        'cv_version_used': None, 'notes': '', 'next_action': None, 'next_action_date': None,
        'last_email_received': None, 'last_email_snippet': None,
        'status_transitions': [{'from': None, 'to': status, 'at': TS, 'source': 'ui'}],
        'updated_at': TS, 'company': 'ZZE Bulk Co', 'company_canonical': 'ZZE Bulk Co',
        'title': title, 'url': f'https://example.com/{id_}', 'best_cv': None, 'short_id': id_[-4:],
        'location': 'Remote', 'salary_min': None, 'salary_max': None, 'source': 'manual',
        'has_cached_page': False, 'discovered_at': TS,
        'tailored_resume_id': None, 'tailored_resume_name': None, 'has_cover_letter': False,
        'interviews': [],
    }


# title → the stage header the row is painted under, read off the DOM in order.
# The list scroller's direct children are the stage heads and the rows themselves
# (React.Fragment adds no node), so walking them in order says which group a row
# ended up in — the one thing a status move has to prove.
GROUPS_JS = """() => {
  const anyRow = document.querySelector('.v2-arow');
  const scroller = anyRow && anyRow.closest('.v2-scroll');
  if (!scroller) return null;
  const out = {}; let head = null;
  for (const el of scroller.children) {
    const first = ((el.innerText || '').split('\\n')[0] || '').trim();
    if (el.classList.contains('v2-arow')) out[first] = head;
    else if (first) head = first.toUpperCase();
  }
  return out;
}"""


def _bar_text(pg):
    """InnerText of the floating bulk bar, or None when it is not up."""
    return pg.evaluate("""() => {
      const el = [...document.querySelectorAll('div')].find((d) => /^\\d+ selected/.test((d.innerText || '').trim()));
      return el ? (el.innerText || '').replace(/\\s+/g, ' ').trim() : null;
    }""")


@case('apps-bulk-move')
def _apps_bulk_move(c):
    state = {'zzebulkrowa': 'applied', 'zzebulkrowb': 'applied'}
    posted = []
    with h.browser() as b:
        pg = C.mpage(b)
        try:
            def mock_list(route):
                if route.request.method != 'GET':
                    return route.fallback()
                rows = [_app('zzebulkrowa', TITLE_A, state['zzebulkrowa']),
                        _app('zzebulkrowb', TITLE_B, state['zzebulkrowb'])]
                route.fulfill(status=200, content_type='application/json',
                              body=json.dumps({'total': len(rows), 'applications': rows}))

            def mock_bulk(route):
                body = {}
                try:
                    body = json.loads(route.request.post_data or '{}')
                except ValueError:
                    body = {'unparsed': route.request.post_data}
                posted.append(body)
                n = 0
                for i in body.get('ids') or []:
                    if i in state and state[i] != body.get('status'):
                        state[i] = body.get('status')
                        n += 1
                route.fulfill(status=200, content_type='application/json',
                              body=json.dumps({'updated': n, 'skipped': 0, 'not_found': []}))

            pg.route(re.compile(r'/api/applications(\?|$)'), mock_list)
            pg.route(re.compile(r'/api/applications/bulk-update$'), mock_bulk)

            C.go(pg, '/applications')
            c.check('both ZZE rows render', C.body_has(pg, TITLE_A) and C.body_has(pg, TITLE_B))
            row_a = pg.locator('.v2-arow', has_text=TITLE_A).first
            row_b = pg.locator('.v2-arow', has_text=TITLE_B).first
            if row_a.count() == 0 or row_b.count() == 0:
                c.check('both rows are present in the list', False)
                return

            c.check('no bulk bar before anything is picked', _bar_text(pg) is None)

            row_a.click(modifiers=['Control'])
            row_b.click(modifiers=['Control'])
            pg.wait_for_timeout(200)
            bar = _bar_text(pg)
            c.check('the bulk bar counts both picks', bool(bar) and bar.startswith('2 selected'), bar)
            c.check('…and offers the four stages',
                    bool(bar) and all(w in bar for w in ('Applied', 'Interview', 'Offer', 'Rejected')), bar)

            applied_btn = pg.locator('[title="All selected are already in Applied"]')
            c.check('the stage both rows are already in is inert', applied_btn.count() == 1,
                    applied_btn.count())

            move = pg.locator('[title="Move 2 to Interview"]').first
            c.check('Interview says how many it would move', move.count() > 0)
            if move.count() == 0:
                return
            move.click()
            pg.wait_for_timeout(900)

            c.check('one bulk-update carried both ids and the new status',
                    len(posted) == 1 and posted[0].get('status') == 'interview'
                    and sorted(posted[0].get('ids') or []) == ['zzebulkrowa', 'zzebulkrowb'], posted)

            groups = pg.evaluate(GROUPS_JS) or {}
            c.check('both rows regrouped under Interview',
                    groups.get(TITLE_A) == 'INTERVIEW' and groups.get(TITLE_B) == 'INTERVIEW', groups)
            c.check('the bar clears with the selection', _bar_text(pg) is None, _bar_text(pg))

            undo = pg.get_by_text('Undo', exact=True).first
            c.check('an undo toast is offered', undo.count() > 0)
            if undo.count() == 0:
                return
            undo.click()
            pg.wait_for_timeout(900)

            c.check('undo sent one call back to the previous status',
                    len(posted) == 2 and posted[1].get('status') == 'applied'
                    and sorted(posted[1].get('ids') or []) == ['zzebulkrowa', 'zzebulkrowb'], posted)
            groups = pg.evaluate(GROUPS_JS) or {}
            c.check('both rows are back under Applied',
                    groups.get(TITLE_A) == 'APPLIED' and groups.get(TITLE_B) == 'APPLIED', groups)

            errs, perrs = C.console_errors(pg)
            c.check('no page errors during the bulk round trip', not perrs, perrs[:2])
        finally:
            pg.unroute_all(behavior='ignoreErrors')
            pg.context.close()


@case('apps-bulk-escape')
def _apps_bulk_escape(c):
    """Escape drops the selection when nothing else is open; a plain click opens the row and keeps it (as on the Feed)."""
    with h.browser() as b:
        pg = C.mpage(b)
        try:
            rows = [_app('zzebulkesca', TITLE_A, 'applied'), _app('zzebulkescb', TITLE_B, 'applied')]

            def mock_list(route):
                if route.request.method != 'GET':
                    return route.fallback()
                route.fulfill(status=200, content_type='application/json',
                              body=json.dumps({'total': 2, 'applications': rows}))

            pg.route(re.compile(r'/api/applications(\?|$)'), mock_list)
            C.go(pg, '/applications')
            row_a = pg.locator('.v2-arow', has_text=TITLE_A).first
            row_b = pg.locator('.v2-arow', has_text=TITLE_B).first
            if row_a.count() == 0 or row_b.count() == 0:
                c.check('both rows are present in the list', False)
                return

            row_a.click(modifiers=['Control'])
            row_b.click(modifiers=['Control'])
            pg.wait_for_timeout(150)
            c.check('two rows picked', (_bar_text(pg) or '').startswith('2 selected'), _bar_text(pg))
            pg.keyboard.press('Escape')
            pg.wait_for_timeout(200)
            c.check('Escape clears the selection', _bar_text(pg) is None, _bar_text(pg))

            row_a.click(modifiers=['Control'])
            pg.wait_for_timeout(150)
            c.check('one row picked again', (_bar_text(pg) or '').startswith('1 selected'), _bar_text(pg))
            row_b.click()
            pg.wait_for_timeout(250)
            c.check('a plain click keeps the selection', (_bar_text(pg) or '').startswith('1 selected'), _bar_text(pg))
            c.check('…and opens that row in the detail pane',
                    row_b.get_attribute('aria-current') == 'true', row_b.get_attribute('aria-current'))
        finally:
            pg.unroute_all(behavior='ignoreErrors')
            pg.context.close()
