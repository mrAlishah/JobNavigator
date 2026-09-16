"""feed-zoom — the posting zoom floater steps the frame and remembers the level.

The Feed renders a posting as an iframe (live page, or the cached snapshot for an
applied job) and zooms it by scaling the ELEMENT: `transform: scale(z/100)` with
`width/height: (10000/z)%`, so a cross-origin page reflows to the new width
instead of being cropped. The level is per browser in
`jobnavigator_post_zoom`, not per job.

Read-only against the database: everything here is per-browser UI state in a
throwaway context.
"""
import json
from _suite import case
import _common as C
import h

KEY = 'jobnavigator_post_zoom'
# The Feed opens on status=new, which a long-running database can legitimately
# have none of. Widen it the way feed-filter-paging does, so the case tests the
# floater rather than the state of the job table.
FILTERS = {"status": ["new", "saved", "applied"], "company": [], "source": [],
           "h1b_verdict": [], "location": [], "arrangement": [],
           "min_score": "", "min_salary": "", "max_salary": ""}
PILL = '.v2-zoomfloat'
IN = '.v2-zoomfloat [aria-label="Zoom in"]'
FRAME = 'iframe[title="posting"], iframe[title="cached"]'


def _frame_box(pg):
    """The posting frame's own inline width + its computed transform."""
    return pg.evaluate("""(sel) => {
      const el = document.querySelector(sel);
      if (!el) return null;
      return { width: el.style.width, transform: getComputedStyle(el).transform };
    }""", FRAME)


@case('feed-zoom')
def _feed_zoom(c):
    with h.browser() as b:
        # seeded_page, not mpage: the case asserts what the APP wrote to the key,
        # and h.py's per-navigation init script would re-seed it underneath us.
        pg = C.seeded_page(b, extra={KEY: '100', 'v2_feed_filters': json.dumps(FILTERS)})
        try:
            C.go(pg, '/feed')
            # the floater lives over the posting — a job has to be open first, and
            # a posting whose host refuses to be framed shows the blocked panel
            # instead, which has nothing to zoom. Walk a few rows, and take the
            # cached snapshot (always a frame) when one is offered.
            rows = pg.locator('[data-row]')
            if rows.count() == 0:
                c.skip('the Feed has no job rows in this database — no posting to zoom')
            for i in range(min(6, rows.count())):
                rows.nth(i).click()
                pg.wait_for_timeout(1200)
                if pg.locator(PILL).count():
                    break
                cached = pg.locator('[aria-label="Posting view"] >> text=Cached')
                if cached.count():
                    cached.first.click()
                    pg.wait_for_timeout(900)
                    if pg.locator(PILL).count():
                        break
            if pg.locator(PILL).count() == 0:
                c.skip('no framable posting in the first rows — the floater only shows over a frame')

            before = _frame_box(pg)
            c.check('the posting frame is on screen', bool(before), before)
            c.eq('it starts at 100% (inline width untouched)', before and before['width'], '100%')

            title = pg.locator(PILL).first.get_attribute('title') or ''
            c.check('the pill names the level and the reset gesture',
                    'Zoom 100%' in title and 'double-click' in title, title)

            pg.locator(IN).first.click()
            pg.wait_for_timeout(400)
            after = _frame_box(pg)
            c.check('a + step re-widths the frame', after and after['width'] != before['width'], after)
            c.check('…and scales it back down', after and after['transform'] not in (None, 'none'), after)
            c.eq('…and writes the level', C.ls_get(pg, KEY), '110')

            # Double-click the + step, not the pill's own padding: the pill is a
            # rounded capsule, so its corners are not hittable and the frame under
            # it takes the point. The gesture is the same one a user makes — the
            # two clicks it contains step up (110 → 130), then the dblclick
            # reaches the pill's handler and resets over them.
            pg.locator(IN).first.dblclick()
            pg.wait_for_timeout(400)
            c.eq('a double-click resets the level', C.ls_get(pg, KEY), '100')
            c.eq('…and the frame is back to full width', (_frame_box(pg) or {}).get('width'), '100%')

            errs, perrs = C.console_errors(pg)
            c.check('no console errors while zooming', not errs and not perrs,
                    '; '.join((errs + perrs)[:2]))
        finally:
            pg.context.close()
