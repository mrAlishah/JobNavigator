"""Uber careers: jobs.uber.com's own search API.

Uber moved its careers site from uber.com/careers to jobs.uber.com (2026). The old list URL
redirects to the new site, which does not read its filters and shows "No jobs found", so a page
scrape found only the "Saved jobs" link. The new site renders from one JSON endpoint:

    GET https://jobs.uber.com/api/jobs/search/?team=Product&location=San Francisco&page=2

- `team`: a whole team (Product, Engineer, Sales…); there are no sub-teams any more.
- `location`: a city name, repeatable. `keyword`: free text. `page`: 1-based, 10 jobs a page.
- Each job carries its id, title, full description (HTML), teams, cities, date and link.

Cloudflare rate-limits the API (about 75 quick requests blocked it for 40 minutes), so pages are
read one at a time, `PAUSE` seconds apart, at most `MAX_PAGES`.

A saved old URL still works: `department=` becomes the team and `location=USA-California-San
Francisco` becomes the city ("San Francisco"); the old sub-teams are dropped.
"""
import asyncio
import logging
import re
from urllib.parse import parse_qs, urlencode, urlparse

from bs4 import BeautifulSoup

from backend.scraper._shared.filters import _validate_job
from backend.scraper._shared.urls import host_matches

logger = logging.getLogger("jobnavigator.scraper.ats.uber")

API = "https://jobs.uber.com/api/jobs/search/"
SITE = "https://jobs.uber.com"
PAUSE = 3.0
MAX_PAGES = 10
_LIST_PATH = re.compile(r"^/(?:[a-z]{2}(?:-[a-z]{2})?/)?jobs/?$", re.I)
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/130.0.0.0 Safari/537.36",
            "Accept": "application/json", "Accept-Language": "en-US,en;q=0.9"}


def _old_list(parsed) -> bool:
    return host_matches(parsed.geturl(), "uber.com") and "/careers/list" in parsed.path \
        and not re.search(r"/careers/list/\d+", parsed.path)


def is_uber(url: str) -> bool:
    """An Uber job search: jobs.uber.com/<lang>/jobs/?… or the old uber.com/…/careers/list/?…"""
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    if host_matches(url, "jobs.uber.com"):
        return bool(_LIST_PATH.match(parsed.path or "/jobs/"))
    return _old_list(parsed)


def search_params(url: str) -> dict:
    """The API query for a saved search URL (old or new site)."""
    parsed = urlparse(url)
    q = parse_qs(parsed.query)
    if _old_list(parsed):
        teams = q.get("department") or []
        # "USA-California-San Francisco", "DEU--Berlin": the city is the last part
        places = [p.split("-")[-1].strip() for p in q.get("location") or []]
        keyword = q.get("query") or q.get("keyword") or []
    else:
        teams, places, keyword = q.get("team") or [], q.get("location") or [], q.get("keyword") or []
    params = {}
    if teams:
        params["team"] = teams
    if places:
        params["location"] = [p for p in places if p]
    if keyword and keyword[0].strip():
        params["keyword"] = [keyword[0].strip()]
    return params


def _text(html) -> str | None:
    text = BeautifulSoup(html or "", "html.parser").get_text(separator="\n", strip=True)
    return text[:30_000] if len(text) >= 50 else None


def _link(job: dict) -> str:
    urls = job.get("Urls") or []
    pick = next((u for u in urls if u.get("IsDefault")), None) or next(
        (u for u in urls if str(u.get("Culture", "")).lower().startswith("en")), None) or (urls[0] if urls else None)
    path = (pick or {}).get("Url") or f"/en/jobs/{job.get('Id')}/"
    return SITE + path if path.startswith("/") else path


def _places(job: dict) -> list:
    out = []
    for loc in job.get("Locations") or []:
        text = ", ".join(p for p in (loc.get("City"), loc.get("Region"), loc.get("Country")) if p)
        if text and text not in out:
            out.append(text)
    return out


def to_job(item: dict) -> dict:
    places = _places(item)
    remote = str(item.get("Remote")).lower() == "true"
    return {"title": (item.get("Title") or "").strip(), "url": _link(item),
            "location": places[0] if places else None, "locations": places,
            "arrangement": "remote" if remote else None,
            "description": _text(item.get("Description"))}


async def _get(params: dict, fetch):
    if fetch is not None:
        return await fetch(params)
    from backend.scraper._shared.url_safety import safe_get
    resp = await safe_get(f"{API}?{urlencode(params, doseq=True)}", timeout=20, headers=_HEADERS)
    if resp.status_code != 200:
        return resp.status_code, None
    return 200, resp.json()


async def scrape(url: str, debug: bool = False, fetch=None, pause: float = PAUSE) -> list[dict] | tuple:
    """Every job of one saved Uber search, page by page. A refused first page raises (the scrape
    log names the status); a refused later page keeps what was read."""
    params = search_params(url)
    jobs, rejected, seen = [], [], set()
    pages = 1
    page = 1
    while page <= min(pages, MAX_PAGES):
        if page > 1 and pause:
            await asyncio.sleep(pause)
        status, data = await _get({**params, "page": [str(page)]}, fetch)
        if status != 200 or not isinstance(data, dict):
            if page == 1:
                raise RuntimeError(f"Uber jobs API answered {status}" + (" (rate limited)" if status == 429 else ""))
            logger.warning(f"Uber jobs API answered {status} on page {page}; keeping {len(jobs)} jobs")
            break
        pages = int(data.get("totalPages") or 1)
        for item in data.get("jobs") or []:
            if item.get("Id") in seen:
                continue
            seen.add(item.get("Id"))
            job = to_job(item)
            reason = _validate_job(job["title"], job["url"])
            if reason is None:
                jobs.append(job)
            elif debug:
                rejected.append({"title": job["title"], "url": job["url"], "selector": "uber_api", "reason": reason})
        page += 1
    logger.info(f"Uber: {len(jobs)} jobs from {min(pages, MAX_PAGES)} pages")
    return (jobs, rejected) if debug else jobs
