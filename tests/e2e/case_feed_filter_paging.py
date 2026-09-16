"""feed-filter-paging — Feed paging stays inside the active filter.

A page requested just before the filter changed used to be appended under the new
filter, and a slow unfiltered first page could overwrite a faster filtered one, so a
three-company filter showed other companies after a scroll. Both are generation-guarded
in JobFeed.jsx (loadGenRef). Read-only against the database.
"""
import json
from _suite import case
import _common as C
import h

FILTERS = {"status": ["new", "saved", "applied", "ignored"], "company": ["Anthropic", "OpenAI", "Stripe"],
           "source": [], "h1b_verdict": [], "location": [], "arrangement": [],
           "min_score": "", "min_salary": "", "max_salary": ""}
COMPANIES_JS = """() => [...document.querySelectorAll('[data-row]')].map(r => {
  const s = r.querySelector('.v2-rowink span[title]'); return s ? s.title : '' })"""


@case('feed-filter-paging')
def _feed_filter_paging(c):
    _st, body = h.get('/jobs?status=new,saved,applied,ignored&company=Anthropic,OpenAI,Stripe&limit=1&brief=1')
    total = (body or {}).get('total', 0) if isinstance(body, dict) else 0
    if total < 60:
        c.skip('fewer than 60 jobs for the three filter companies — nothing to page')
    with h.browser() as b:
        pg = C.seeded_page(b, extra={'v2_feed_filters': json.dumps(FILTERS)})
        try:
            C.go(pg, '/feed')
            pg.wait_for_selector('[data-row]', timeout=15000)
            first = pg.locator('[data-row]').count()
            # 1. scroll until a second page lands
            pg.evaluate("() => { const el = document.querySelector('[data-row]').closest('.v2-scroll'); el.scrollTop = el.scrollHeight }")
            pg.wait_for_function(f"() => document.querySelectorAll('[data-row]').length > {first}", timeout=15000)
            after = pg.locator('[data-row]').count()
            outside = sorted({x for x in pg.evaluate(COMPANIES_JS) if x not in FILTERS['company']})
            c.eq(f'page 2 landed ({first} → {after} rows) inside the filter', outside, [])

            # 2. race: kick off another page, then narrow the filter before it can land
            pg.evaluate("() => { const el = document.querySelector('[data-row]').closest('.v2-scroll'); el.scrollTop = 0 }")
            pg.wait_for_timeout(150)
            pg.evaluate("() => { const el = document.querySelector('[data-row]').closest('.v2-scroll'); el.scrollTop = el.scrollHeight }")
            pg.evaluate("(f) => localStorage.setItem('v2_feed_filters', JSON.stringify(f))", dict(FILTERS, company=['Anthropic']))
            pg.reload(wait_until='domcontentloaded')
            pg.wait_for_selector('[data-row]', timeout=15000)
            pg.wait_for_timeout(2500)
            outside = sorted({x for x in pg.evaluate(COMPANIES_JS) if x != 'Anthropic'})
            c.eq('narrowing to one company keeps the list clean', outside, [])
        finally:
            pg.context.close()
