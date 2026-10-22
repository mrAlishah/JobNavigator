"""Company career-pages source — orchestrates ATS dispatch per URL; _dispatch_ats detects the ATS and calls the matching ats.<name>.scrape, falling through to ats.generic.scrape if nothing matches."""
import asyncio
import logging
import time
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError

from backend.models.db import (
    SessionLocal, Company, Job, ScrapeLog, get_existing_external_ids,
)
from backend.scraper._shared.browser import _get_browser, _new_page, _close_page
from backend.scraper._shared.filters import _apply_company_filters
from backend.scraper._shared.dedup import make_external_id, make_content_hash, mark_ignored, _normalize_url
from backend.scraper.ats import (
    workday, greenhouse, lever, ashby, oracle_hcm,
    phenom, talentbrew, rippling, smartrecruiters, meta, google, amazon, generic,
)
from backend.scraper.ats._descriptions import _fetch_descriptions_parallel

logger = logging.getLogger("jobnavigator.scraper.sources.company_pages")


# ── ATS dispatcher ───────────────────────────────────────────────────────────

async def _dispatch_ats(url: str, debug: bool = False, shared_browser=None, max_pages: int | None = None):
    """Detect ATS by URL and call the matching scraper, falling back to generic; order must match the if/elif chain in scrape_single_career_page, and `max_pages` only affects Playwright-paginated scrapers (Google, Meta) since HTTP-only ones are API-driven and unbounded."""
    if phenom.is_phenom(url):
        return await phenom.scrape(url, debug=debug)
    if talentbrew.is_talentbrew(url):
        return await talentbrew.scrape(url, debug=debug)
    if oracle_hcm.is_oracle_hcm(url):
        return await oracle_hcm.scrape(url, debug=debug)
    if lever.is_lever(url):
        return await lever.scrape(url, debug=debug)
    if workday.is_workday(url):
        return await workday.scrape(url, debug=debug)
    if ashby.is_ashby(url):
        return await ashby.scrape(url, debug=debug)
    if greenhouse.is_greenhouse(url):
        return await greenhouse.scrape(url, debug=debug)
    if rippling.is_rippling(url):
        return await rippling.scrape(url, debug=debug)
    if smartrecruiters.is_smartrecruiters(url):
        return await smartrecruiters.scrape(url, debug=debug)
    if amazon.is_amazon(url):
        return await amazon.scrape(url, debug=debug)
    if meta.is_meta(url):
        return await meta.scrape(url, browser=shared_browser, max_pages=max_pages, debug=debug)
    if google.is_google(url):
        return await google.scrape(url, browser=shared_browser, max_pages=max_pages, debug=debug)
    # The fallback honours the same per-company page cap as every other handler; None keeps generic's own default.
    return await generic.scrape(url, browser=shared_browser, debug=debug, **({'max_pages': max_pages} if max_pages else {}))


def _refresh_known_jobs(db, known: dict) -> int:
    """Fill location/arrangement on rows the feed already holds, from what this pass
    scraped. Only empty fields are written: both apply_* helpers leave set values
    alone, and `location` is filled only when the row has none. This is how a
    normal company pass back-fills postings scraped before the handlers emitted
    these fields, without re-fetching anything."""
    from backend.analyzer.work_arrangement import apply_arrangement_to_job
    from backend.analyzer.location import apply_location_to_job
    ids = list(known)
    rows = db.query(Job).filter(
        Job.external_id.in_(ids),
        ((Job.loc_country == None) & (Job.loc_region == None) & (Job.loc_city == None))
        | ((Job.arr_remote == None) & (Job.arr_hybrid == None) & (Job.arr_onsite == None)),
    ).all()
    touched = 0
    for job in rows:
        j = known.get(job.external_id) or {}
        before = (job.location, job.loc_country, job.loc_region, job.loc_city,
                  job.arr_remote, job.arr_hybrid, job.arr_onsite)
        try:
            if not (job.location or "").strip() and j.get("location"):
                job.location = j["location"]
            apply_arrangement_to_job(job, structured=j.get("arrangement"))
            apply_location_to_job(job, extra=j.get("locations"))
        except Exception as e:
            logger.warning(f"Refresh failed for '{job.title}': {e}")
            continue
        after = (job.location, job.loc_country, job.loc_region, job.loc_city,
                 job.arr_remote, job.arr_hybrid, job.arr_onsite)
        if after != before:
            touched += 1
    return touched


def _ats_labels_for(urls) -> str:
    """Return a comma-separated list of distinct ATS labels for these URLs, for activity-log messages (e.g. "Acme (Greenhouse): 3 new"); strips the API/AJAX/(Playwright) suffix from detect_scrape_type's labels for compactness."""
    from backend.api.routes_companies import detect_scrape_type
    labels = set()
    for u in urls:
        u = (u or "").strip()
        if not u:
            continue
        # "Workday API" → "Workday"; "Generic (Playwright)" → "Generic"; "TalentBrew AJAX" → "TalentBrew"
        label = detect_scrape_type(u).split(" ", 1)[0]
        labels.add(label)
    if not labels:
        return "?"
    return ", ".join(sorted(labels))


def record_company_scrape_log(company_id, company_name: str, result: dict, db=None, run_id=None,
                              scraped_at=None):
    """Write the per-company ScrapeLog row for one career-page scrape; single source of truth for both the batch path and the manual single-company trigger so /api/scrape-log, is_warning and /health/entities see manual runs like scheduled ones. `db` lets a caller reuse an open session. The row belongs to the current account; `run_id` links the rows of every account one shared fetch fed (MT-8)."""
    own_db = db is None
    if own_db:
        db = SessionLocal()
    try:
        # QA F6 / R4-06, T3-03: every finished attempt stamps last_scraped_at, also an empty
        # board or a failed one; otherwise the company stays due and is fetched on every
        # scheduler tick whatever its interval. The error / warning lives on this row.
        comp = db.query(Company).filter(Company.id == company_id).first()
        if comp is None:
            # Deleted while it was being scraped (T5-01): no row to log against.
            logger.info(f"'{company_name}' no longer exists, no scrape log written")
            return
        # QA V-02: the scheduled batch passes its start time (`scraped_at`), so the
        # next tick measures the interval from start to start, not from the end of a
        # fetch that ran minutes into the previous tick.
        comp.last_scraped_at = scraped_at or datetime.now(timezone.utc)
        is_warning = (
            result.get("jobs_found", 0) == 0
            and not result.get("error")
        )
        log = ScrapeLog(
            source=f"playwright_{company_name}",
            company_id=company_id,
            jobs_found=result.get("jobs_found", 0),
            new_jobs=result.get("new_jobs", 0),
            error=result.get("error"),
            is_warning=is_warning,
            duration_seconds=result.get("duration", 0),
            run_id=run_id,
        )
        db.add(log)
        db.commit()
    finally:
        if own_db:
            db.close()


def _needs_browser(urls):
    """True if any URL requires a real browser (not a pure-API ATS)."""
    for u in urls:
        u = (u or "").strip()
        if not u:
            continue
        if (phenom.is_phenom(u) or talentbrew.is_talentbrew(u) or oracle_hcm.is_oracle_hcm(u)
                or lever.is_lever(u) or workday.is_workday(u) or ashby.is_ashby(u)
                or greenhouse.is_greenhouse(u) or rippling.is_rippling(u)
                or smartrecruiters.is_smartrecruiters(u) or amazon.is_amazon(u)):
            continue
        # Meta, Google, levels.fyi, or generic Playwright — needs browser
        return True
    return False


# ── Per-company scraper ──────────────────────────────────────────────────────

async def _fetch_postings(company, target_urls: list, browser) -> tuple[list, list]:
    """The fetch half of a company scrape (MT-8): every URL through its ATS handler, deduped
    by posting URL. Returns (postings, url_errors); one URL failing never stops the rest.
    Reads only what the target key covers (URLs, max_pages, wait_for_selector), so the
    result can be shared by every company with the same key."""
    max_pages = getattr(company, 'max_pages', 5) or 5
    unique_jobs = []
    seen_urls = set()
    url_errors = []

    for target_url in target_urls:
        try:
            # Handled explicitly here (not via _dispatch_ats's generic branch) because generic
            # needs company.wait_for_selector + max_pages, not _dispatch_ats's defaults.
            if (phenom.is_phenom(target_url) or talentbrew.is_talentbrew(target_url)
                    or oracle_hcm.is_oracle_hcm(target_url) or lever.is_lever(target_url)
                    or workday.is_workday(target_url) or ashby.is_ashby(target_url)
                    or greenhouse.is_greenhouse(target_url) or rippling.is_rippling(target_url)
                    or smartrecruiters.is_smartrecruiters(target_url)
                    or amazon.is_amazon(target_url)
                    or meta.is_meta(target_url) or google.is_google(target_url)):
                page_jobs = await _dispatch_ats(target_url, debug=False, shared_browser=browser, max_pages=max_pages)
            else:
                page = await _new_page(browser)
                await generic._setup_route_blocks(page)
                await page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
                wait_sel = getattr(company, 'wait_for_selector', None)
                await generic._wait_for_content(page, wait_sel)
                page_jobs = await generic._extract_all_pages(
                    page, target_url, max_pages, wait_for_selector=wait_sel,
                )
                await _close_page(page)
            for j in page_jobs:
                if j["url"] not in seen_urls:
                    seen_urls.add(j["url"])
                    unique_jobs.append(j)
        except Exception as e:
            logger.warning(f"Scrape error on {target_url}: {e}")
            url_errors.append(f"{target_url}: {type(e).__name__}: {e}")
    return unique_jobs, url_errors


async def scrape_single_career_page(company: Company, shared_browser=None,
                                     known_external_ids: set = None,
                                     shared_fetch=None) -> dict:
    """Scrape a single company career page via company.scrape_urls, optionally reusing shared_browser; `known_external_ids`, when passed, is the batch's dedup set (of the current account) loaded once and mutated in place so later companies see earlier inserts.

    `shared_fetch` (a `backend.scraper.targets.SharedFetch`) makes several companies with
    the same target key (MT-8) share one fetch: the first call fetches, later calls ingest a
    copy of the same postings. Everything after the fetch (filters, dedup, H-1B, salary,
    location) is this company's own ingest, under the current account."""
    start_time = time.time()
    # Plain values: the row may be deleted (and expired) while the board is fetched.
    company_id = company.id
    company_name = company.name

    target_urls = company.scrape_urls or []

    target_urls = [u.strip() for u in target_urls if u and u.strip()]

    if not target_urls:
        return {"jobs_found": 0, "new_jobs": 0, "error": "No career page URLs"}

    prefetched = shared_fetch is not None and shared_fetch.done
    # Only launch a browser if a URL needs one; API-only ATS batches (Lever, Greenhouse, etc.)
    # skip Chromium, avoiding failures on hosts without Playwright browsers installed (CI).
    # A subscriber whose postings were already fetched needs no browser at all.
    needs_browser = not prefetched and _needs_browser(target_urls)
    own_browser = shared_browser is None and needs_browser
    pw = None
    browser = shared_browser
    try:
        if own_browser:
            pw, browser = await _get_browser()

        if shared_fetch is None:
            unique_jobs, url_errors = await _fetch_postings(company, target_urls, browser)
        else:
            unique_jobs, url_errors = await shared_fetch.get(
                lambda: _fetch_postings(company, target_urls, browser))
            if prefetched:
                # This account's run includes the fetch it rode on, so durations compare.
                start_time -= shared_fetch.duration

        if not unique_jobs:
            duration = time.time() - start_time
            return {
                "jobs_found": 0,
                "new_jobs": 0,
                "error": "; ".join(url_errors) if url_errors else None,
                "duration": duration,
            }

        from backend.models.db import get_global_title_exclude
        _gte_db = SessionLocal()
        try:
            _global_title_excl = get_global_title_exclude(_gte_db)
        finally:
            _gte_db.close()
        filtered_out = []
        has_filters = (
            (company.title_include_expr and company.title_include_expr.strip())
            or (company.title_exclude_keywords and len(company.title_exclude_keywords) > 0)
            or _global_title_excl
        )
        if has_filters:
            before_count = len(unique_jobs)
            unique_jobs, filtered_out = _apply_company_filters(unique_jobs, company, _global_title_excl)
            logger.info(
                f"Keyword filter for {company.name}: {before_count} -> {len(unique_jobs)} kept, {len(filtered_out)} ignored"
            )

        db = SessionLocal()
        new_jobs = 0
        try:
            existing_ids = known_external_ids if known_external_ids is not None else get_existing_external_ids(db)
            # Per-company hoist for the per-job H-1B scan, avoiding a Settings read + JSON parse
            # and a company lookup per job.
            from backend.analyzer.h1b_checker import load_exclusion_phrases
            _phrases = load_exclusion_phrases(db)
            _company_lookup = {company.name.strip().lower(): company}

            jobs_needing_desc = []
            known_here = {}   # ext_id -> scraped dict, for rows the feed already holds
            for j in unique_jobs:
                ext_id = make_external_id(company.name, j["title"], j["url"])
                content_hash = make_content_hash(company.name, j["title"])
                if ext_id in existing_ids:
                    known_here[ext_id] = j
                    continue
                j["_ext_id"] = ext_id
                j["_content_hash"] = content_hash
                jobs_needing_desc.append(j)

            if jobs_needing_desc:
                desc_results = await _fetch_descriptions_parallel(jobs_needing_desc)
                desc_map = {}
                for result in desc_results:
                    if isinstance(result, Exception):
                        continue
                    job_dict, desc = result
                    desc_map[job_dict["url"]] = desc
            else:
                desc_map = {}

            # QA F6 / T5-04: the description fetch is the long await; a company deleted
            # meanwhile gets nothing inserted. (The locked re-check before the commit
            # covers the rest of the ingest.)
            if jobs_needing_desc and db.query(Company.id).filter(Company.id == company_id).first() is None:
                db.rollback()
                logger.info(f"{company_name} was deleted during its scrape; ingest discarded")
                return {"jobs_found": len(unique_jobs), "new_jobs": 0, "error": None,
                        "duration": time.time() - start_time, "deleted": True}

            # The loop below writes rows; the H-1B lookup happens before it (S2-01).
            if jobs_needing_desc:
                from backend.scraper._shared.analysis import prefetch_h1b
                await prefetch_h1b([company.name])

            for j in jobs_needing_desc:
                ext_id = j["_ext_id"]
                content_hash = j["_content_hash"]
                desc = desc_map.get(j["url"])

                job = Job(
                    external_id=ext_id,
                    content_hash=content_hash,
                    company=company.name,
                    title=j["title"],
                    url=_normalize_url(j["url"]) or j["url"],
                    source="direct",
                    status="new",
                    seen=False,
                    saved=False,
                    description=desc,
                    # Optional: an ATS handler supplies it only when its board does.
                    location=j.get("location") or None,
                )

                # Always run even without a description — the company-level LCA check doesn't need it.
                try:
                    from backend.analyzer.h1b_checker import check_job_h1b
                    from backend.analyzer.salary_extractor import apply_salary_to_job
                    from backend.analyzer.work_arrangement import apply_arrangement_to_job
                    from backend.analyzer.location import apply_location_to_job
                    await check_job_h1b(job, db, company_lookup=_company_lookup, phrases=_phrases)
                    apply_salary_to_job(job, getattr(job, "_h1b_median", None))
                    apply_arrangement_to_job(job, structured=j.get("arrangement"))
                    apply_location_to_job(job, extra=j.get("locations"))
                except Exception as analysis_err:
                    logger.warning(f"Inline analysis failed for {j['title']}: {analysis_err}")

                if job.h1b_jd_flag:
                    _phrase = getattr(job, "_h1b_matched_phrase", None) or "?"
                    logger.info(f"Skipping job (body exclusion): {j['title']} @ {job.company or company_name} — matched phrase: {_phrase!r}")
                    mark_ignored(job)

                try:
                    with db.begin_nested():
                        db.add(job)
                        db.flush()
                    if job.status == "new":
                        new_jobs += 1
                    existing_ids.add(ext_id)
                except IntegrityError:
                    logger.debug(f"Duplicate external_id for '{j['title']}' at {company.name}, skipping")
                    continue
                except Exception as e:
                    logger.warning(f"Insert failed for '{j['title']}' at {company.name} ({j['url']}): {e}")
                    continue

            # Save filtered-out jobs as "ignored" for dedup purposes
            for j in filtered_out:
                ext_id = make_external_id(company.name, j["title"], j["url"])
                if ext_id in existing_ids:
                    continue

                job = Job(
                    external_id=ext_id,
                    company=company.name,
                    title=j["title"],
                    url=_normalize_url(j["url"]) or j["url"],
                    source="direct",
                    status="ignored",
                    seen=False,
                    saved=False,
                    location=j.get("location") or None,
                )
                try:
                    with db.begin_nested():
                        db.add(job)
                        db.flush()
                    existing_ids.add(ext_id)
                except IntegrityError:
                    continue
                except Exception as e:
                    logger.warning(f"Insert failed for ignored job '{j['title']}' at {company.name} ({j['url']}): {e}")
                    continue

            refreshed = _refresh_known_jobs(db, known_here) if known_here else 0

            # QA F6 / T5-04: the company (or its account) may have been deleted while the
            # board was fetched. Re-read it with a row lock just before the commit: gone →
            # throw this ingest away, so a deleted company's postings never reappear; there
            # → a concurrent DELETE waits for this commit (Postgres; SQLite has no FOR UPDATE).
            comp = (db.query(Company).filter(Company.id == company_id)
                    .with_for_update().first())
            if comp is None:
                db.rollback()
                logger.info(f"{company_name} was deleted during its scrape; ingest discarded")
                return {"jobs_found": len(unique_jobs), "new_jobs": 0, "error": None,
                        "duration": time.time() - start_time, "deleted": True}
            comp.last_scraped_at = datetime.now(timezone.utc)

            db.commit()
            if refreshed:
                logger.info(f"{company_name}: location/arrangement filled on {refreshed} existing job(s)")
        finally:
            db.close()

        duration = time.time() - start_time

        from backend.activity import log_activity
        ats_label = _ats_labels_for(target_urls)
        log_activity("scrape", f"{company_name} ({ats_label}): {new_jobs} new / {len(unique_jobs)} found in {duration:.1f}s", company=company_name)

        return {
            "jobs_found": len(unique_jobs),
            "new_jobs": new_jobs,
            "error": "; ".join(url_errors) if url_errors else None,
            "duration": duration,
        }

    except Exception as e:
        duration = time.time() - start_time
        ats_label = _ats_labels_for(target_urls)
        logger.error(f"{company_name} ({ats_label}) scrape failed: {e}")

        from backend.activity import log_activity
        log_activity("scrape", f"{company_name} ({ats_label}) failed: {e}", company=company_name)

        return {"jobs_found": 0, "new_jobs": 0, "error": str(e), "duration": duration}
    finally:
        if own_browser:
            if browser:
                await browser.close()
            if pw:
                await pw.stop()


# ── Batch scraper ────────────────────────────────────────────────────────────

def _row_still_there(db, row) -> bool:
    """False when `row` was deleted since it was loaded (refreshing an expired row then
    raises ObjectDeletedError). Rolls back a session left in a failed state first."""
    from sqlalchemy.orm.exc import ObjectDeletedError
    from sqlalchemy.exc import PendingRollbackError
    try:
        row.id   # an expired row reloads here (a column load, not account-filtered)
        return True
    except ObjectDeletedError:
        return False
    except PendingRollbackError:
        db.rollback()
        try:
            row.id
            return True
        except ObjectDeletedError:
            return False


async def scrape_career_pages(force: bool = False, account_ids=None,
                              skip_company_ids=None) -> dict:
    """Scrape career pages for every active company with a scrape URL (gated only on `active` and having a URL, deliberately not `playwright_enabled`), honoring per-company or global scrape_interval_minutes unless force=True skips interval checks; one company's failure is logged to its own ScrapeLog row without ending the batch, and one shared Playwright browser serves all companies that need it.

    MT-8: the companies of every account (`account_ids=None`) or of the listed accounts are
    grouped into targets (`backend.scraper.targets.company_key`). A due target is fetched
    once and ingested per subscribing company under its own account (`acting_as`), with
    that account's dedup set; each ingest writes its own ScrapeLog row, and the rows of one
    fetch share a `run_id`. One subscriber failing does not stop the others.

    QA F6: `skip_company_ids` are left out (a manual scrape of that company is in flight);
    a company deleted mid-run is skipped (T5-01/T5-04); errors that are not the company's
    own scrape result (an ingest crash, a scoring sweep) are returned in `errors` as
    {"account", "where", "error"} for the run summary."""
    import uuid
    from backend.accounts.settings import get_setting
    from backend.accounts.background import account_session, acting_as
    from backend.scraper.targets import (
        company_due, company_key, company_target_due, group_targets, load_active, reusable_key,
        shared_fetch_for,
    )
    db = SessionLocal()
    shared_pw = None
    shared_browser = None
    skipped: list[dict] = []
    errors: list[dict] = []
    scraped = 0
    failed = 0
    skip_ids = {str(i) for i in (skip_company_ids or ())}
    try:
        global_interval = int(get_setting(db, "scrape_interval_minutes", "60"))

        active = load_active(db, Company, account_ids)
        # Plain (id, name, account) per row, taken now: later commits expire the rows, and
        # a row deleted meanwhile raises on any attribute access (T5-01).
        ident = {id(c): (c.id, c.name, c.account_id) for c in active}

        companies = []
        for c in active:
            if str(c.id) in skip_ids:
                skipped.append({"name": c.name, "reason": "manual scrape in progress"})
                continue
            if c.scrape_urls and any((u or "").strip() for u in c.scrape_urls):
                companies.append(c)
            else:
                skipped.append({"name": c.name, "reason": "no scrape URLs"})

        logger.info(
            f"Playwright: {len(companies)} companies with scrape URLs"
            + (f", {len(skipped)} active without URLs" if skipped else "")
        )

        any_needs_browser = any(_needs_browser(c.scrape_urls or []) for c in companies)
        if any_needs_browser:
            shared_pw, shared_browser = await _get_browser()
            logger.info("Playwright: launched shared browser for batch scrape")

        # Dedup set per account, loaded once for the whole batch (avoids re-materializing ~14k
        # external_ids per company); mutated in place so later companies of the same account
        # dedup against earlier inserts. Loaded lazily under that account's context.
        external_ids_by_account: dict = {}

        now = datetime.now(timezone.utc)
        sweep_accounts: list = []   # accounts that need the post-batch scoring sweep
        for target in group_targets(companies, company_key):
            # T5-01: the rows were loaded before earlier targets committed; drop subscribers
            # deleted since then before anything reads their attributes.
            alive = []
            for company in target.subscribers:
                if _row_still_there(db, company):
                    alive.append(company)
                else:
                    skipped.append({"name": ident[id(company)][1], "reason": "deleted during the run"})
            if not alive:
                continue
            target.subscribers = alive
            # Interval check; a manual run (force=True) runs every active company with URLs —
            # the interval only paces the scheduler. A shared target is due when any
            # subscriber is (effective interval = the shortest), and then feeds them all.
            if not force:
                due, _effective = company_target_due(target, now, global_interval)
                if not due:
                    for company in target.subscribers:
                        _, elapsed, interval = company_due(company, now, global_interval)
                        logger.debug(f"Skipping {company.name}: scraped {elapsed:.0f}m ago (interval={interval}m)")
                        skipped.append({
                            "name": company.name,
                            "reason": f"not due ({elapsed:.1f}m of {interval}m)",
                        })
                    continue

            run_id = uuid.uuid4()
            # Every target gets one (cross-run reuse, `shared_fetch_for`), not only shared ones.
            shared = shared_fetch_for(target.key) if reusable_key(target.key) else None
            for company in target.subscribers:
                cid, cname, account = ident[id(company)]
                # One company's failure must never take the batch down: the scraper's own errors come
                # back as a result dict, and anything else (browser death, a DB error while logging,
                # the company deleted mid-run) is handled here so the loop carries on.
                # QA F10 / R1-05: each subscriber works in a fresh session of its own
                # account, never in the batch session that loaded every account's rows
                # (its identity map and lazy loads would ignore the context switch).
                with account_session(account, SessionLocal) as sdb:
                    try:
                        # QA F6 / T5-01: re-read the row (fresh, under its account). A company
                        # or account deleted since the batch loaded is skipped, never touched.
                        fresh = sdb.query(Company).filter(Company.id == cid).first()
                        if fresh is None:
                            skipped.append({"name": cname, "reason": "deleted during the run"})
                            continue
                        company = fresh
                        if account not in external_ids_by_account:
                            external_ids_by_account[account] = get_existing_external_ids(sdb)
                        kwargs = {"shared_fetch": shared} if shared is not None else {}
                        result = await scrape_single_career_page(
                            company, shared_browser=shared_browser,
                            known_external_ids=external_ids_by_account[account], **kwargs)
                        if result.get("deleted"):
                            skipped.append({"name": cname, "reason": "deleted during the run"})
                            continue
                        auto_depth = company.auto_scoring_depth
                        record_company_scrape_log(cid, cname, result, db=sdb, run_id=run_id,
                                                  scraped_at=now)
                        scraped += 1
                        if result.get("error"):
                            failed += 1

                        logger.info(
                            f"Playwright {cname}: found={result['jobs_found']}, new={result['new_jobs']}"
                            + (f" (shared fetch, {len(target.subscribers)} subscribers)" if target.shared else "")
                        )

                        # Mark for one pool sweep per account at batch end rather than per company — the
                        # sweep picks up all unscored auto-score jobs (including retries), so per-company
                        # would re-walk it N times.
                        if (auto_depth in ("light", "full") and result.get("new_jobs", 0) > 0
                                and account not in sweep_accounts):
                            sweep_accounts.append(account)
                    except Exception as e:
                        # Roll back first: after a failed flush every ORM attribute access
                        # raises PendingRollbackError (T5-01), so nothing below reads `company`.
                        try:
                            sdb.rollback()
                        except Exception:
                            logger.exception("Rollback failed")
                        try:
                            gone = sdb.query(Company.id).filter(Company.id == cid).first() is None
                        except Exception:
                            gone = False
                        if gone:
                            logger.info(f"Company '{cname}' was deleted during the run, skipped: {e}")
                            skipped.append({"name": cname, "reason": "deleted during the run"})
                            continue
                        failed += 1
                        logger.exception(f"Company '{cname}' failed, batch continues: {e}")
                        errors.append({"account": account, "where": f"company {cname}",
                                       "error": f"{type(e).__name__}: {e}"})
                        try:
                            record_company_scrape_log(
                                cid, cname,
                                {"jobs_found": 0, "new_jobs": 0, "error": f"{type(e).__name__}: {e}", "duration": 0},
                                run_id=run_id, scraped_at=now,
                            )
                        except Exception:
                            logger.exception(f"Could not record the failure of '{cname}'")

            # End the batch session's read transaction and expire its rows, so the next
            # target's due/deleted checks read fresh values.
            db.commit()
            await asyncio.sleep(2)

        for account in sweep_accounts:
            with acting_as(account):
                try:
                    from backend.analyzer.cv_scorer import analyze_unscored_jobs
                    await analyze_unscored_jobs(status="new")
                except Exception as e:
                    logger.exception(f"Post-batch scoring sweep failed (scrape results are saved): {e}")
                    errors.append({"account": account, "where": "company scoring sweep",
                                   "error": f"{type(e).__name__}: {e}"})

        if skipped:
            logger.info(
                "Company scrapes skipped: "
                + ", ".join(f"{s['name']} ({s['reason']})" for s in skipped)
            )
        return {"scraped": scraped, "failed": failed, "skipped": skipped, "errors": errors}
    finally:
        if shared_browser:
            await shared_browser.close()
        if shared_pw:
            await shared_pw.stop()
        db.close()
