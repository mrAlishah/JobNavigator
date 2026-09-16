"""Meta Careers scraper (Playwright DOM) — job cards render client-side via React, so this scrapes the DOM instead of an API."""
import asyncio
import logging
import re

from backend.scraper._shared.browser import _get_browser, _new_page, _close_page
from backend.scraper._shared.urls import host_matches
from backend.scraper._shared.filters import _validate_job

logger = logging.getLogger("jobnavigator.scraper.ats.meta")

# Meta separates the places of one card with a dot operator, not a semicolon.
_DOT = "⋅"
# "+4 more" is a count of the places the card did not print, not a place.
_MORE = re.compile(r"^\+\s*\d+\s+more$", re.I)
# A line that is one place on its own: "Menlo Park, CA".
_ONE_PLACE = re.compile(r"^[A-Za-z .'-]+, [A-Z]{2}$")

# Meta's class names are hashed, so the card is read by structure instead. Each
# separator sits in its own element, so the block that holds the places is the
# smallest one holding every dot - an outer block holds the same dots and more
# text, an inner one holds fewer. A card naming a single place carries no dot at
# all, and there the first line after the title that reads as a place is taken.
_PLACES_JS = """
el => {
  let best = null, most = 0;
  for (const node of el.querySelectorAll('*')) {
    const text = node.textContent || '';
    const dots = (text.match(/\\u22C5/g) || []).length;
    if (!dots) continue;
    if (!text.replace(/\\u22C5/g, '').trim()) continue;   // a lone separator
    if (dots > most || (dots === most && best !== null && text.length < best.length)) {
      most = dots; best = text;
    }
  }
  if (best !== null) return best;
  const lines = (el.innerText || '').split('\\n').map(s => s.trim()).filter(Boolean);
  const h3 = el.querySelector('h3');
  const title = h3 ? (h3.innerText || '').trim() : '';
  let start = 0;
  if (title) { const at = lines.indexOf(title); if (at >= 0) start = at + 1; }
  for (let i = start; i < lines.length; i++) {
    if (/^[A-Za-z .'-]+, [A-Z]{2}$/.test(lines[i])) return lines[i];
  }
  return '';
}
"""


def _places_from_text(text: str | None) -> list[str]:
    """Every place one Meta card names, in the order the card prints them.

    The line reads "Bellevue, WA ⋅ Redmond, WA ⋅ +4 more": dot operators
    separate the entries and the trailing "+N more" is a count, not a place.
    A line that names nothing place-shaped yields nothing at all, so a card the
    DOM route missed leaves `location` empty rather than filling it with a tag.
    """
    raw = (text or "").strip()
    if not raw:
        return []
    parts = [p.strip() for p in raw.split(_DOT)]
    if len(parts) == 1 and not _ONE_PLACE.match(parts[0]):
        return []
    out: list[str] = []
    for part in parts:
        if not part or _MORE.match(part):
            continue
        if part not in out:
            out.append(part)
    return out


def is_meta(url: str) -> bool:
    """Check if URL is a Meta Careers job search page."""
    return host_matches(url, "metacareers.com")


async def scrape(url: str, browser=None, max_pages: int | None = None, debug: bool = False) -> list[dict] | tuple:
    """Scrape Meta Careers via Playwright DOM extraction; `max_pages` caps pages walked (default 20 if unset)."""
    page_cap = max_pages if (max_pages is not None and max_pages > 0) else 20
    own_browser = browser is None
    pw = None
    if own_browser:
        pw, browser = await _get_browser()

    jobs = []
    rejected = []
    page = None
    try:
        page = await _new_page(browser)
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)

        try:
            await page.wait_for_selector('a[href*="/profile/job_details/"]', timeout=15000)
        except Exception:
            logger.warning("Meta: job card selector timed out, trying fallback wait")
            await asyncio.sleep(5)
        await asyncio.sleep(2)

        # Dismiss cookie banner via JS (overlay blocks normal clicks)
        await page.evaluate("""
            const btns = document.querySelectorAll('button');
            for (const b of btns) {
                if (b.textContent.includes('Accept All')) { b.click(); break; }
            }
        """)
        await asyncio.sleep(0.5)

        seen_ids = set()
        page_num = 0
        while page_num < page_cap:
            page_num += 1
            links = await page.query_selector_all('a[href*="/profile/job_details/"]')
            page_count = 0

            for link in links:
                href = await link.get_attribute("href") or ""
                job_id = href.split("/profile/job_details/")[-1].rstrip("/").split("?")[0]
                if not job_id or job_id in seen_ids:
                    continue
                seen_ids.add(job_id)
                page_count += 1

                h3 = await link.query_selector("h3")
                title = (await h3.inner_text()).strip() if h3 else ""
                job_url = f"https://www.metacareers.com/v2/jobs/{job_id}/"

                try:
                    places = _places_from_text(await link.evaluate(_PLACES_JS))
                except Exception:
                    places = []

                reason = _validate_job(title, job_url)
                if reason is None:
                    jobs.append({"title": title, "url": job_url,
                                 "location": places[0] if places else None,
                                 "locations": places})
                elif debug:
                    rejected.append({"title": title, "url": job_url, "selector": "meta_careers", "reason": reason})

            logger.info(f"Meta: page {page_num} — {page_count} new jobs")

            # Click next page button via JS (avoids overlay interception)
            next_btn = page.locator("[aria-label='Button to select next week']")
            if await next_btn.count() == 0:
                break
            disabled = await next_btn.get_attribute("aria-disabled")
            if disabled == "true":
                break
            await next_btn.evaluate("el => el.click()")
            await asyncio.sleep(2)
            try:
                await page.wait_for_selector('a[href*="/profile/job_details/"]', timeout=10000)
            except Exception:
                pass

    except Exception as e:
        logger.error(f"Meta scraper error: {e}")
        if debug:
            rejected.append({"title": "(error)", "url": url, "selector": "meta_careers", "reason": str(e)})
    finally:
        if page:
            await _close_page(page)
        if own_browser:
            if browser:
                await browser.close()
            if pw:
                await pw.stop()

    logger.info(f"Meta: {len(jobs)} jobs extracted, {len(rejected)} rejected")
    if debug:
        return jobs, rejected
    return jobs
