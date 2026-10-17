"""H-1B company LCA data checker + JD body scan; H-1B metrics live in the VisaCache table so any job's source company can show H-1B info."""
import json
import logging
import re
import time as _time
import asyncio
from datetime import datetime, timezone, timedelta

import httpx

from backend.models.db import SessionLocal, Company, Job, VisaCache, has_uncommitted_writes
from backend.accounts.settings import get_setting

logger = logging.getLogger("jobnavigator.h1b")

_TTL_DAYS = 90


_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"


class H1bRateLimited(Exception):
    """Both H-1B sources returned 429/403 — back off (counts against the breaker)."""


class _LiveBudget:
    """Bounds inline live lookups during a scrape to MAX_LOOKUPS per rolling WINDOW, and stops after MAX_RATE_STRIKES consecutive rate-limit hits until it rolls over."""
    WINDOW = 900          # seconds
    MAX_LOOKUPS = 10
    MAX_RATE_STRIKES = 3

    def __init__(self):
        self.start = 0.0
        self.lookups = 0
        self.strikes = 0

    def _roll(self):
        now = _time.time()
        if now - self.start > self.WINDOW:
            self.start, self.lookups, self.strikes = now, 0, 0

    def allow(self):
        self._roll()
        return self.strikes < self.MAX_RATE_STRIKES and self.lookups < self.MAX_LOOKUPS

    def note_lookup(self):
        self._roll()
        self.lookups += 1

    def note_rate_limit(self):
        self._roll()
        self.strikes += 1


_budget = _LiveBudget()


async def _fetch_myvisajobs(company_name: str, h1b_slug: str = None) -> dict:
    """Scrape myvisajobs.com for company H-1B LCA data. Raises H1bRateLimited on 429/403."""
    try:
        if h1b_slug:
            slug = h1b_slug
        else:
            slug = company_name.lower().replace(" ", "-").replace(".", "").replace(",", "")
        url = f"https://www.myvisajobs.com/employer/{slug}/"

        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": _UA})

            if resp.status_code in (429, 403):
                raise H1bRateLimited(f"myvisajobs {resp.status_code} for {company_name}")

            # Detect redirect to generic employers page (slug not found)
            if str(resp.url).rstrip("/").endswith("/employers"):
                logger.warning(f"H-1B slug '{slug}' redirected to generic page for {company_name}")
                return {"lca_count": 0, "approval_rate": 0, "median_salary": 0}

            if resp.status_code != 200:
                logger.warning(f"H-1B page returned {resp.status_code} for {company_name}")
                return {"lca_count": 0, "approval_rate": 0, "median_salary": 0}

            text = resp.text

            lca_count = 0
            approval_rate = 0.0
            median_salary = 0

            # LCA count: "LCA for H-1B: 9,362" or "filed 9,362 labor condition applications"
            lca_match = re.search(r'LCA for H-1B:\s*([\d,]+)', text)
            if not lca_match:
                lca_match = re.search(r'filed\s+([\d,]+)\s+labor condition', text, re.IGNORECASE)
            if lca_match:
                lca_count = int(lca_match.group(1).replace(",", ""))

            # Approval rate: compute from the "Certified" count vs lca_count if available.
            certified_match = re.search(r'certified[^>]*>\s*([\d,]+)', text, re.IGNORECASE)
            if certified_match and lca_count > 0:
                certified = int(certified_match.group(1).replace(",", ""))
                approval_rate = round((certified / lca_count) * 100, 1)

            # Salary: "H-1B Salary [$172,325]" or "$172,325" in salary table
            salary_match = re.search(r'H-1B Salary.*?\$([\d,]+)', text, re.IGNORECASE)
            if not salary_match:
                # Fallback: look for dollar amounts near "salary" or "average"
                salary_match = re.search(r'\$([\d]{2,3},\d{3})', text)
            if salary_match:
                median_salary = int(salary_match.group(1).replace(",", ""))

            return {
                "lca_count": lca_count,
                "approval_rate": approval_rate,
                "median_salary": median_salary,
            }

    except H1bRateLimited:
        raise
    except Exception as e:
        logger.error(f"H-1B myvisajobs fetch failed for {company_name}: {e}")
        return {"lca_count": 0, "approval_rate": 0, "median_salary": 0}


async def _fetch_h1bdata(company_name: str) -> dict:
    """Fallback: parse h1bdata.info's raw DOL LCA disclosure rows into lca_count (row count) and median_salary; no approval_rate (LCA data, not USCIS decisions)."""
    from urllib.parse import quote
    import statistics
    url = f"https://h1bdata.info/index.php?em={quote(company_name)}"
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        resp = await client.get(url, headers={"User-Agent": _UA})
    if resp.status_code in (429, 403):
        raise H1bRateLimited(f"h1bdata {resp.status_code} for {company_name}")
    if resp.status_code != 200:
        logger.warning(f"h1bdata returned {resp.status_code} for {company_name}")
        return {"lca_count": 0, "approval_rate": 0, "median_salary": 0}

    salaries = []
    count = 0
    for row in re.findall(r'<tr[^>]*>(.*?)</tr>', resp.text, re.S):
        cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.S)
        if len(cells) < 6:
            continue  # header / non-data row
        count += 1
        sal = re.sub(r'<[^>]+>', '', cells[2]).strip().replace(",", "")
        if sal.isdigit():
            salaries.append(int(sal))
    median = int(statistics.median(salaries)) if salaries else 0
    return {"lca_count": count, "approval_rate": 0.0, "median_salary": median}


async def fetch_company_h1b_data(company_name: str, h1b_slug: str = None) -> dict:
    """Company H-1B LCA data: MyVisaJobs primary (richer, blocks some IPs), falling back to h1bdata.info; raises H1bRateLimited only when both are blocked."""
    mvj_blocked = False
    try:
        data = await _fetch_myvisajobs(company_name, h1b_slug)
        if (data.get("lca_count") or 0) > 0 or (data.get("median_salary") or 0) > 0:
            return data  # MyVisaJobs had a good answer — richest source, done
    except H1bRateLimited:
        mvj_blocked = True

    # MyVisaJobs was blocked or empty — try the fallback.
    try:
        return await _fetch_h1bdata(company_name)  # reached (even zeros = legit negative)
    except H1bRateLimited:
        pass  # fallback also blocked
    except Exception as e:
        logger.error("h1bdata fallback failed for %s: %s", company_name, e)

    if mvj_blocked:
        raise H1bRateLimited(f"both sources blocked for {company_name}")
    return {"lca_count": 0, "approval_rate": 0, "median_salary": 0}


# ── VisaCache: the single source of truth for company H-1B metrics ───────────

def _name_key(name: str) -> str:
    return (name or "").strip().lower()


def _row_to_dict(row):
    if not row:
        return None
    return {
        "lca_count": row.lca_count or 0,
        "approval_rate": row.approval_rate or 0.0,
        "median_salary": row.median_salary or 0,
        "has_data": bool(row.has_data),
    }


def _is_fresh(row, ttl_days) -> bool:
    ft = row.fetched_at if row else None
    if ft is not None and ft.tzinfo is None:  # SQLite stores naive datetimes
        ft = ft.replace(tzinfo=timezone.utc)
    return bool(ft and ft > datetime.now(timezone.utc) - timedelta(days=ttl_days))


def _read_cache(db, key):
    # populate_existing: a row this session loaded earlier may have been refreshed
    # since by another session's committed write; read the current values.
    return (db.query(VisaCache)
            .filter(VisaCache.name_key == key, VisaCache.country == "US")
            .populate_existing().first())


# VisaCache is install-wide (not AccountScoped), so the account-scoping listeners
# leave these statements alone and no unscoped() is needed.

def _has_data(data) -> bool:
    return (data["lca_count"] or 0) > 0 or (data["median_salary"] or 0) > 0


def _upsert_statement(dialect: str, key, name, slug, data):
    """INSERT … ON CONFLICT (name_key, country) DO UPDATE for PostgreSQL and SQLite, or
    None for another dialect. Same rules as `_apply_h1b_data`: a transient zero result
    never overwrites good metrics, and a missing slug keeps the stored one."""
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        return None
    from sqlalchemy import case, func
    import uuid as _uuid
    t = VisaCache.__table__
    has_data = _has_data(data)
    stmt = insert(t).values(
        id=_uuid.uuid4(), name_key=key, country="US", display_name=name, slug=slug,
        lca_count=data["lca_count"], approval_rate=data["approval_rate"],
        median_salary=data["median_salary"], has_data=has_data,
        fetched_at=datetime.now(timezone.utc), last_error=None,
    )
    ex = stmt.excluded
    keep_old = func.coalesce(t.c.lca_count, 0) > 0

    def metric(col):
        return ex[col] if has_data else case((keep_old, t.c[col]), else_=ex[col])

    set_ = {
        "display_name": ex.display_name,
        "fetched_at": ex.fetched_at,
        "last_error": None,
        "lca_count": metric("lca_count"),
        "approval_rate": metric("approval_rate"),
        "median_salary": metric("median_salary"),
        "has_data": metric("has_data"),
    }
    if slug:
        set_["slug"] = ex.slug
    return stmt.on_conflict_do_update(index_elements=["name_key", "country"], set_=set_)


def _store_h1b(key, name, slug, data):
    """Write one fetch result to VisaCache in its own short session and commit at once.

    S2-01: the row is never written in a caller's session, so no caller holds the
    unique-key lock across its next await. The upsert makes two ingests storing the
    same new company at the same time harmless: neither waits on the other's
    uncommitted row, neither raises. Returns the stored metrics; if the write fails,
    the fetched ones."""
    db = SessionLocal()
    try:
        stmt = _upsert_statement(db.get_bind().dialect.name, key, name, slug, data)
        if stmt is not None:
            db.execute(stmt)
        else:
            _apply_h1b_data(db, _read_cache(db, key), key, name, slug, data)
            db.flush()
        stored = _row_to_dict(_read_cache(db, key))
        db.commit()
        return stored
    except Exception as e:
        db.rollback()
        logger.warning("H-1B cache write failed for %s: %s", name, e)
        return {"lca_count": data["lca_count"] or 0, "approval_rate": data["approval_rate"] or 0.0,
                "median_salary": data["median_salary"] or 0, "has_data": _has_data(data)}
    finally:
        db.close()


async def _fetch_and_store(key, name, slug, cached, respect_budget):
    """Live lookup (budget-bounded when asked) and store; `cached` is returned when the
    budget is spent or the lookup fails. The caller must hold no uncommitted writes."""
    if respect_budget and not _budget.allow():
        return cached  # budget/breaker exhausted — cron will fill this in later
    if respect_budget:
        _budget.note_lookup()
    try:
        data = await fetch_company_h1b_data(name, h1b_slug=slug)
    except H1bRateLimited as e:
        if respect_budget:
            _budget.note_rate_limit()
        logger.warning("H-1B rate-limited for %s (%s)", name, e)
        return cached
    except Exception as e:
        logger.warning("H-1B fetch error for %s: %s", name, e)
        return cached
    return _store_h1b(key, name, slug, data)


async def resolve_company_h1b(db, name, slug=None, allow_live=True,
                              respect_budget=True, force=False, ttl_days=_TTL_DAYS):
    """Return H-1B metrics for a company name (dict) or None; cache-first, live fetch on a miss unless the budget/breaker blocks it.

    Reads through the caller's session but never writes through it: a fetched result
    is committed in a short session of its own (`_store_h1b`), so the caller's
    transaction is left exactly as it was. When the caller's session already holds
    uncommitted writes, the live lookup is skipped (S2-01): awaiting it would keep
    those rows locked while another coroutine's synchronous write waits on them on
    the event loop. Batch ingests call `prefetch_company_h1b` before writing."""
    key = _name_key(name)
    if not key:
        return None
    row = _read_cache(db, key)
    cached = _row_to_dict(row)
    if row and _is_fresh(row, ttl_days) and not force:
        return cached
    if not allow_live:
        return cached  # stale row, or None
    if has_uncommitted_writes(db):
        logger.debug("H-1B live lookup for %s skipped: the caller's session holds uncommitted writes", name)
        return cached
    return await _fetch_and_store(key, name, slug, cached, respect_budget)


async def prefetch_company_h1b(names, slugs=None, ttl_days=_TTL_DAYS) -> int:
    """Fill VisaCache for a batch's companies before its ingest writes anything.

    Each distinct name not freshly cached gets one live lookup (budget-bounded),
    with no session held across the await; the per-job checks that follow are then
    cache hits. `slugs` maps a lowercased name to its MyVisaJobs slug override; when
    omitted, the current account's companies (and aliases) supply it, as in
    `check_job_h1b`. Returns the number of live lookups made."""
    wanted = {}
    for n in names or ():
        k = _name_key(n)
        if k and k not in wanted:
            wanted[k] = n.strip()
    if not wanted:
        return 0
    db = SessionLocal()
    try:
        rows = db.query(VisaCache).filter(VisaCache.country == "US",
                                          VisaCache.name_key.in_(list(wanted))).all()
        known = {r.name_key: (_row_to_dict(r), _is_fresh(r, ttl_days)) for r in rows}
        if slugs is None:
            from backend.models.db import build_company_lookup
            lookup = build_company_lookup(db)
            slugs = {k: lookup[k].h1b_slug for k in wanted if k in lookup}
    finally:
        db.close()

    looked_up = 0
    for key, name in wanted.items():
        cached, fresh = known.get(key, (None, False))
        if fresh:
            continue
        if not _budget.allow():
            break  # the rest stay as they are; the H-1B cron fills them in
        await _fetch_and_store(key, name, (slugs or {}).get(key), cached, respect_budget=True)
        looked_up += 1
    return looked_up


def _apply_h1b_data(db, row, key, name, slug, data):
    """Write a fetch result onto a VisaCache row (creating it when absent). Only for a
    dialect `_upsert_statement` does not cover; both real ones use the upsert."""
    has_data = _has_data(data)
    if not row:
        row = VisaCache(name_key=key, country="US")
        db.add(row)
    row.display_name = name
    if slug:
        row.slug = slug
    # Don't overwrite good data with a transient zero result.
    if has_data or not (row.lca_count or 0):
        row.lca_count = data["lca_count"]
        row.approval_rate = data["approval_rate"]
        row.median_salary = data["median_salary"]
        row.has_data = has_data
    row.fetched_at = datetime.now(timezone.utc)
    row.last_error = None
    return row


async def resolve_company_h1b_detached(name, slug=None, respect_budget=False,
                                       force=True, ttl_days=_TTL_DAYS):
    """`resolve_company_h1b` for background callers, with no session held across the fetch.

    Reads the cache in one short session, awaits MyVisaJobs with no connection
    held, then upserts and commits in a second one (`_store_h1b`).
    """
    key = _name_key(name)
    if not key:
        return None

    db = SessionLocal()
    try:
        row = _read_cache(db, key)
        cached = _row_to_dict(row)
        if row and _is_fresh(row, ttl_days) and not force:
            return cached
    finally:
        db.close()

    return await _fetch_and_store(key, name, slug, cached, respect_budget)


async def refresh_all_h1b():
    """Cron: refresh stale VisaCache rows + fetch job companies not yet cached, bypassing the budget breaker.

    The name list is read in one short session; the fetch loop (hundreds of
    companies, 0.5 s apart) then runs with no connection held — it used to pin
    one for the whole run, which can be many minutes.
    """
    # Install-level (MT-7): VisaCache is shared, so the name list covers every account's
    # companies and jobs, whoever triggered the run.
    from backend.accounts.context import unscoped
    db = SessionLocal()
    try:
        with unscoped():
            cutoff = datetime.now(timezone.utc) - timedelta(days=_TTL_DAYS)
            cached_keys = {r[0] for r in db.query(VisaCache.name_key).all()}
            slug_map = {_name_key(c.name): c.h1b_slug for c in db.query(Company).all()}

            # 1) names to refresh: stale cache rows
            stale = db.query(VisaCache).filter(
                (VisaCache.fetched_at == None) | (VisaCache.fetched_at < cutoff)
            ).all()
            names = {(r.display_name or r.name_key) for r in stale}
            # 2) + job companies not cached at all
            for (n,) in db.query(Job.company).distinct().all():
                if n and _name_key(n) not in cached_keys:
                    names.add(n)
    finally:
        db.close()

    logger.info("H-1B cron: %d companies to fetch", len(names))
    updated = 0
    for name in names:
        data = await resolve_company_h1b_detached(
            name, slug=slug_map.get(_name_key(name)), respect_budget=False, force=True)
        if data:
            updated += 1
        await asyncio.sleep(0.5)  # be polite to MyVisaJobs

    db = SessionLocal()
    try:
        from backend.activity import log_activity
        log_activity("h1b", f"H-1B refresh complete: {updated} companies fetched", db=db)
        db.commit()
    finally:
        db.close()


async def fetch_h1b_for_company_id(company_id: str):
    """Fetch + cache H-1B for a single company by ID (on apply). Fire-and-forget."""
    db = SessionLocal()
    try:
        company = db.query(Company).filter(Company.id == company_id).first()
        if not company:
            return
        name, slug = company.name, company.h1b_slug
    finally:
        db.close()

    try:
        # The MyVisaJobs round trip runs with the connection already released.
        await resolve_company_h1b_detached(name, slug=slug, respect_budget=False, force=True)
        logger.info("H-1B auto-fetched for %s", name)
    except Exception as e:
        logger.error(f"H-1B auto-fetch failed for company {company_id}: {e}")


def scan_jd_for_h1b_flags(description: str, exclusion_phrases: list) -> dict:
    """Scan a job description for H-1B exclusion phrases; jd_snippet (~100 chars of context) is for the UI tooltip only, never log lines — it can leak recruiter-confidential text."""
    if not description:
        return {"jd_flag": False, "jd_snippet": None, "matched_phrase": None}

    desc_lower = description.lower()

    for phrase in exclusion_phrases:
        if phrase.lower() in desc_lower:
            idx = desc_lower.index(phrase.lower())
            start = max(0, idx - 50)
            end = min(len(description), idx + len(phrase) + 50)
            snippet = description[start:end].strip()
            return {"jd_flag": True, "jd_snippet": snippet, "matched_phrase": phrase}

    return {"jd_flag": False, "jd_snippet": None, "matched_phrase": None}


def determine_h1b_verdict(lca_count: int, jd_flag: bool) -> str:
    """Determine overall H-1B verdict: likely (>50 LCAs, no JD flag), unlikely (<10 LCAs or JD flag), unknown (no data)."""
    if jd_flag:
        return "unlikely"
    lca_count = lca_count or 0  # untracked companies have no LCA data (None)
    if lca_count > 50:
        return "likely"
    if lca_count >= 10:
        return "possible"
    if lca_count > 0:
        return "unlikely"
    return "unknown"


def load_exclusion_phrases(db) -> list:
    """Read + parse the body_exclusion_phrases setting once; batch callers should call this before their job loop and pass the result into check_job_h1b to avoid re-parsing it per job."""
    exclusion_value = get_setting(db, "body_exclusion_phrases")
    if exclusion_value is not None:
        try:
            return json.loads(exclusion_value)
        except json.JSONDecodeError:
            pass
    return []


async def check_job_h1b(job: Job, db, company_lookup: dict = None, phrases: list = None) -> None:
    """Run H-1B checks on a job and update its fields; batch callers should pass `company_lookup` and `phrases` to avoid a full Companies scan and a Settings read per job."""
    if company_lookup is not None:
        company = company_lookup.get((job.company or "").strip().lower())
    else:
        from backend.models.db import find_company_by_name
        company = find_company_by_name(db, job.company or "")

    # H-1B metrics from VisaCache (live-on-miss, budget-bounded). The Company row,
    # when present, only supplies the per-company slug override.
    slug = company.h1b_slug if company else None
    data = await resolve_company_h1b(db, job.company or "", slug=slug,
                                     allow_live=True, respect_budget=True) or {}
    lca_count = data.get("lca_count", 0) or 0
    approval_rate = data.get("approval_rate", 0.0) or 0.0
    job.h1b_company_lca_count = lca_count
    job.h1b_company_approval_rate = approval_rate
    # Transient (not a column): median salary for the salary extractor to reuse
    # without a second cache lookup.
    job._h1b_median = data.get("median_salary") or None

    # Layer 2: JD body scan
    if phrases is None:
        phrases = load_exclusion_phrases(db)

    jd_result = scan_jd_for_h1b_flags(job.description or "", phrases)
    job.h1b_jd_flag = jd_result["jd_flag"]
    job.h1b_jd_snippet = jd_result["jd_snippet"]
    # Transient attribute (not a column) — lets scrapers log which phrase
    # triggered the exclusion without spilling the JD excerpt into logs.
    job._h1b_matched_phrase = jd_result["matched_phrase"]

    job.h1b_verdict = determine_h1b_verdict(lca_count, jd_result["jd_flag"])


