"""The employer's link for LinkedIn listings, looked up through the mock account.

Levels.fyi, Jobright and JobSpy hand over LinkedIn listings (`linkedin:<id>` keys, link kind
`board`). LinkedIn's public job page no longer names the employer's apply link; its logged-in
job API does (`companyApplyUrl`). This module asks it, once per LinkedIn job, through the mock
account's browser session that the extension import already uses, and never through the user's own
account: it refuses to run when that session is the personal account.

- One answer per LinkedIn job id, install-wide (`LinkedinLookup`), applied to every account's rows:
  `employer` (the link, followed when it is a short link), `easy_apply` (no employer link exists),
  `board` (LinkedIn names another board), `gone` (closed). Throttling and errors are not cached.
- One request every `pause` seconds (2), at most `per_run` per run; a run stops after LinkedIn
  answers nothing 3 times in a row.
- A row that gets an employer link is re-keyed, classified and relinked (posting_links.relink),
  so it joins the employer's posting the account already has.
"""
import asyncio
import json
import logging
from contextlib import asynccontextmanager

from backend.scraper._shared.link_kinds import BOARD, DIRECT, EASY_APPLY, classify, link_kind
from backend.scraper._shared.postings import is_short_link, unwrap

logger = logging.getLogger("jobnavigator.linkedin_lookup")

PAUSE = 2.0             # seconds between requests (the extension import uses 1 s on the same session)
PER_RUN = 200           # most LinkedIn jobs asked about after one scrape
STOP_AFTER_NO_DATA = 3



def mock_session_problem(db):
    """None when the mock session can be used, else why not: a mock account must be set, and the
    jar's sign-in record (refresh_linkedin_session.record_sign_in) must say the mock signed it."""
    from backend import refresh_linkedin_session as rls
    from backend.accounts.settings import get_setting
    mock = str(get_setting(db, "linkedin_mock_email") or "").strip().lower()
    if not mock:
        return "no mock account is set (Settings › LinkedIn)"
    main = str(get_setting(db, "linkedin_email") or "").strip().lower()
    if main and main == mock:
        return "the mock account is the main account (Settings › LinkedIn)"
    signed = rls.signed_in_as(rls.MOCK) or {}
    if signed.get("account") != rls.MOCK:
        return "the mock session has no sign-in record: sign the mock account in (Settings › LinkedIn)"
    if str(signed.get("email") or "").strip().lower() != mock:
        return "the mock session was signed in with another email: sign the mock account in again (Settings › LinkedIn)"
    return None


def pending_ids(db, limit: int) -> list:
    """LinkedIn ids of rows still on a board link (any account), newest first."""
    from sqlalchemy import or_
    from backend.models.db import Job
    rows = (db.query(Job.posting_key)
            .filter(Job.posting_key.like("linkedin:%"),
                    or_(Job.link_kind == BOARD, Job.link_kind.is_(None)),
                    Job.status.notin_(("ignored", "duplicate")), Job.duplicate_of.is_(None))
            .order_by(Job.discovered_at.desc()).limit(limit * 5).all())
    ids = list(dict.fromkeys(k.split(":", 1)[1] for (k,) in rows))
    # an id whose cached answer cannot change its rows (gone, another board) holds no slot
    from backend.models.db import LinkedinLookup
    final = {i for (i,) in db.query(LinkedinLookup.linkedin_id)
             .filter(LinkedinLookup.linkedin_id.in_(ids), LinkedinLookup.result.in_(("gone", BOARD))).all()} if ids else set()
    return [i for i in ids if i not in final][:limit]


GONE_REASON = "LinkedIn: the listing is gone"
CLOSED_REASON = "LinkedIn: no longer accepting applications"


def closed_on_linkedin(job_state) -> bool:
    return str(job_state or "").upper() == "CLOSED"


def apply_result(db, linkedin_id: str, apply_url, result: str, closed=None) -> int:
    """Give every account's rows of this LinkedIn job the answer. Returns rows changed.
    `closed` = (state, reason) to store as the rows' open state (a closed or gone listing)."""
    from datetime import datetime, timezone
    from sqlalchemy import or_
    from backend.models.db import Job
    from backend.scraper._shared.posting_links import key_of, relink
    rows = (db.query(Job)
            .filter(Job.posting_key == f"linkedin:{linkedin_id}",
                    or_(Job.link_kind.in_((BOARD, EASY_APPLY)), Job.link_kind.is_(None)),
                    Job.status != "ignored").all())
    if closed:
        for job in rows:
            job.open_state, job.open_reason = closed
            job.open_checked_at = datetime.now(timezone.utc)
    if result == "employer" and apply_url:
        for job in rows:
            job.apply_url = apply_url
            job.posting_key = key_of(job)
            classify(job)
        db.flush()
        for job in rows:
            relink(db, job)
    elif result == EASY_APPLY:
        for job in rows:
            job.link_kind = EASY_APPLY
    elif not closed:
        return 0
    db.commit()
    return len(rows)


def _error_page(url) -> bool:
    """A redirect that ended on the careers site's error page (…/careers/Error): no posting."""
    from urllib.parse import urlparse
    try:
        return urlparse(url).path.rstrip("/").lower().endswith("/error")
    except Exception:
        return False


def _cached_closed(row):
    """The open state a cached answer gives its rows, or None."""
    if row.result == "gone":
        return "closed", GONE_REASON
    if closed_on_linkedin(row.job_state):
        return "closed", CLOSED_REASON
    return None


async def _closed_state(result, link, job_state, check=None):
    """The open state a fresh answer gives its rows, or None. A listing LinkedIn closed whose
    employer link is known: the employer's page decides (the posting may still be open there);
    when that page says nothing definite, LinkedIn's word stands."""
    if result == "gone":
        return "closed", GONE_REASON
    if not closed_on_linkedin(job_state):
        return None
    if result == "employer" and link:
        from backend.scraper._shared.still_open import UNKNOWN, check_link
        state, reason = await (check or check_link)(link)
        if state != UNKNOWN:
            return state, reason or None
    return "closed", CLOSED_REASON


def _verdict(d):
    """(result, link) from one Voyager answer; None = no answer (counts toward the stop);
    ("unknown", None) = an answer that says nothing definite (not cached)."""
    if d is None:
        return None
    status = d.get("status", 200)
    if status in (404, 410):          # the listing was deleted
        return "gone", None
    if status != 200:                 # throttled, blocked, signed out: no answer
        return None
    if d.get("apply_url"):
        return "employer", d["apply_url"]
    if str(d.get("job_state") or "").upper() == "CLOSED":
        return "gone", None
    if "Onsite" in str(d.get("apply_type") or ""):
        return EASY_APPLY, None
    return "unknown", None


@asynccontextmanager
async def _fetcher(fetch):
    """(fetch(lid) -> dict | None, None), or (None, why not) when there is no mock session."""
    if fetch is not None:
        yield fetch, None
        return
    from backend.accounts.background import acting_as
    from backend.models.db import SessionLocal
    from backend.scraper.sources import linkedin_extension as le
    from backend import refresh_linkedin_session as rls
    with acting_as(None):
        sdb = SessionLocal()
        try:
            problem = mock_session_problem(sdb)
        finally:
            sdb.close()
        cookies = None
        if problem is None:
            try:   # the mock jar itself, never the import's fallback to the main session
                with open(rls.jar(rls.MOCK)) as f:
                    cookies = json.load(f) or None
            except (OSError, ValueError):
                cookies = None
    if problem is not None:
        yield None, problem
        return
    if not cookies:
        yield None, "no LinkedIn mock session"
        return
    from backend.scraper.sources.linkedin_personal import _get_linkedin_browser
    pw = browser = None
    try:
        pw, browser, context, page = await _get_linkedin_browser()
        # only the mock session: the helper may have loaded the personal scraper's cookies
        await context.clear_cookies()
        await context.add_cookies(cookies)
        # The feed: a signed-out session is redirected to the login page, so the sign-in check
        # (linkedin_personal._is_logged_in: /voyager/api/me, which can refuse a live session, or
        # the feed reached with li_at) answers before any job is asked about.
        await page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded", timeout=30000)
        from backend.scraper.sources.linkedin_personal import _is_logged_in
        if not await _is_logged_in(page, context):
            yield None, "the LinkedIn mock session is signed out: sign it in again in Settings › LinkedIn"
            return

        async def fetch_one(lid):
            # the raw answer, with its HTTP status (a 404 is an answer: the job was deleted)
            try:
                return await page.evaluate(le._VOYAGER_JOB_JS, lid)
            except Exception:
                return None
        yield fetch_one, None
    finally:
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass
        if pw is not None:
            try:
                await pw.stop()
            except Exception:
                pass


SHORT_LINK_RETRY_DAYS = 7   # a short link that fails is tried once a day while its job is this new


def short_link_verdict(url: str, got):
    """Where a stored short link went: ("employer", link), ("closed", why) when the posting is
    gone, or ("retry", why) when the link could not be followed. `got` is (status, final url,
    page text) or None."""
    from urllib.parse import urlparse
    from backend.scraper._shared.bot_wall import is_bot_wall
    from backend.scraper._shared.posting_links import employer_link
    if got is None:
        return "retry", "the short link could not be followed"
    status, final, text = got
    if is_short_link(final):          # stuck on the tracker (Appcast answers 403 once a link expires)
        return "retry", f"the short link answers {status}"
    link = employer_link(final)
    if link:
        return "employer", link
    p = urlparse(final)
    where = f"{(p.hostname or '').removeprefix('www.')}{p.path}"
    if status in (404, 410):
        return "closed", f"the short link lands on a missing page ({where})"
    if "greenhouse.io" in (p.hostname or "") and "error=true" in (p.query or ""):
        return "closed", "Greenhouse: the posting is gone"
    if status >= 400 or is_bot_wall(text):
        return "retry", f"the short link lands on {where} ({status})"
    from backend.scraper._shared.still_open import CLOSED_PHRASES, _HIDDEN, _TAG
    page = " ".join(_TAG.sub(" ", _HIDDEN.sub(" ", text or "")).lower().split())
    for phrase in CLOSED_PHRASES:
        if phrase in page:
            return "closed", f"the short link's page says “{phrase}”"
    # A Greenhouse short link always names one posting: landing anywhere else means it was removed.
    # Other trackers may land on a careers site whose links are not read, so they are only retried.
    if urlparse(url).hostname == "grnh.se":
        return "closed", f"the short link lands on {where}, not a posting"
    return "retry", f"the short link lands on {where}, not a posting"


def _short_link_rows(db, limit: int):
    """Jobs on a short link with no employer link that are worth following: not skipped or
    ignored, not already closed, not checked in the last day, and either never tried or still
    new. Never-tried first, then newest."""
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import or_
    from backend.models.db import Job
    from backend.scraper._shared.postings import SHORT_LINK_HOSTS
    now = datetime.now(timezone.utc)
    rows = (db.query(Job)
            .filter(Job.apply_url.is_(None), Job.status.notin_(("ignored", "skip")),
                    or_(Job.link_kind != BOARD, Job.link_kind.is_(None)),
                    or_(Job.open_state.is_(None), Job.open_state != "closed"),
                    or_(Job.open_checked_at.is_(None),
                        (Job.open_checked_at < now - timedelta(days=1))
                        & (Job.discovered_at > now - timedelta(days=SHORT_LINK_RETRY_DAYS))),
                    or_(*[Job.url.like(f"%{h}/%") for h in SHORT_LINK_HOSTS]))
            .order_by(Job.open_checked_at.is_(None).desc(), Job.discovered_at.desc())
            .limit(limit).all())
    return [j for j in rows if is_short_link(j.url)]


def pending_short_links(db, limit: int) -> list:
    return _short_link_rows(db, limit)


async def resolve_stored_short_links(limit: int = 50, follow=None) -> int:
    """Stored jobs whose link is a short link (grnh.se, prng.co, appcast) and that have no employer
    link yet: follow it (short_link_verdict). An employer posting becomes `apply_url` and the job is
    re-keyed, classified and relinked; a removed posting marks the job closed (open_state) and is
    not asked again; a failure is tried again a day later while the job is new. Returns the jobs
    that got their employer link."""
    from datetime import datetime, timezone
    from backend.accounts.context import unscoped
    from backend.models.db import SessionLocal
    from backend.scraper._shared.posting_links import key_of, relink
    from backend.scraper._shared.still_open import CLOSED, UNKNOWN, _fetch
    follow = follow or _fetch
    done = 0
    # unscoped: install-level pass over every account's rows; relink names each row's account.
    with unscoped():
        db = SessionLocal()
        try:
            rows = _short_link_rows(db, limit)
            todo = [(j.id, j.url) for j in rows]
            db.commit()   # no pooled connection waits on an outside site
            by_id = {j.id: j for j in rows}
            for job_id, url in todo:
                result, what = short_link_verdict(url, await follow(url))
                job = by_id[job_id]
                if result == "employer":
                    job.apply_url = what
                    job.posting_key = key_of(job)
                    classify(job)
                    db.flush()
                    relink(db, job)
                    done += 1
                else:
                    job.open_state = CLOSED if result == "closed" else UNKNOWN
                    job.open_reason = what
                    job.open_checked_at = datetime.now(timezone.utc)
                db.commit()
        finally:
            db.close()
    return done


async def run_lookup(per_run: int, fetch=None, pause: float = PAUSE, resolve=None, follow=None,
                     check=None) -> dict:
    """One lookup run: every account's pending LinkedIn rows, then stored short links (install-level).
    `check` replaces still_open.check_link (tests)."""
    out = await _linkedin_pass(per_run, fetch, pause, resolve, check)
    out["short_links"] = await resolve_stored_short_links(limit=50, follow=follow)
    logger.info(f"LinkedIn lookup: {out}")
    return out


async def _linkedin_pass(per_run: int, fetch, pause: float, resolve, check=None) -> dict:
    from backend.accounts.context import unscoped
    from backend.models.db import LinkedinLookup, SessionLocal
    from backend.scraper._shared.postings import resolve_short_link
    resolve = resolve or resolve_short_link
    out = {"looked_up": 0, "employer": 0, "easy_apply": 0, "board": 0, "gone": 0, "closed": 0,
           "cached": 0, "stopped": None}
    # unscoped: an install-level pass over every account's rows; every query below and in
    # posting_links names the account it reads.
    with unscoped():
        db = SessionLocal()
        try:
            ids = pending_ids(db, limit=max(per_run * 5, 50))
            cached = {r.linkedin_id: r for r in
                      db.query(LinkedinLookup).filter(LinkedinLookup.linkedin_id.in_(ids)).all()} if ids else {}
            for lid in ids:
                if lid in cached:
                    apply_result(db, lid, cached[lid].apply_url, cached[lid].result,
                                 _cached_closed(cached[lid]))
                    out["cached"] += 1
            todo = [i for i in ids if i not in cached][:per_run]
            if not todo:
                return out
            async with _fetcher(fetch) as (fetch_one, why_not):
                if fetch_one is None:
                    out["stopped"] = why_not
                    logger.warning(f"LinkedIn lookup skipped: {why_not}")
                    return out
                misses = 0
                for n, lid in enumerate(todo):
                    if n and pause:
                        await asyncio.sleep(pause)
                    d = await fetch_one(lid)
                    out["looked_up"] += 1
                    verdict = _verdict(d)
                    if verdict is None:
                        misses += 1
                        if misses >= STOP_AFTER_NO_DATA:
                            out["stopped"] = f"no data from LinkedIn {STOP_AFTER_NO_DATA} times in a row"
                            break
                        continue
                    misses = 0
                    result, link = verdict
                    if result == "unknown":
                        continue
                    job_state = d.get("job_state") or None
                    if result == "employer":
                        link = unwrap(await resolve(link))
                        if is_short_link(link) or _error_page(link):
                            # did not resolve: an error, not an answer, unless LinkedIn closed the
                            # listing (an expired ad redirector answers 500)
                            if not closed_on_linkedin(job_state):
                                continue
                            result, link = "gone", None
                        elif link_kind(link, None) != DIRECT:
                            result = "board"
                    db.merge(LinkedinLookup(linkedin_id=lid, apply_url=link, result=result,
                                            job_state=job_state))
                    db.commit()   # no pooled connection waits on the employer's page
                    closed = await _closed_state(result, link, job_state, check)
                    apply_result(db, lid, link, result, closed)
                    out[result] += 1
                    if closed and closed[0] == "closed":
                        out["closed"] += 1
        finally:
            db.close()
    return out
