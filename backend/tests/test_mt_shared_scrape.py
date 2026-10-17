"""MT-8 · Shared-scrape fan-out.

Identical targets of different accounts are fetched once and ingested per account: each
account applies its own title filters, excludes and dedup under its own context, writes
its own ScrapeLog row, and the rows of one fetch share a run_id. Different URLs (or any
fetch parameter) are separate targets; results_wanted / hours_old are fetched at the max
and trimmed per account when they are close (QA F10 / R4-05: within 1.5x, same hours
bucket; else separate fetches). A single account without duplicates behaves exactly as before.
"""
import sys
import types
from datetime import datetime, timedelta, timezone

import pytest

from backend.accounts.context import unscoped, use_account
from backend.models.db import Account, Company, Job, ScrapeLog, Search


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def accounts(test_db):
    a = test_db.query(Account).filter(Account.role == "owner").one()
    b = Account(name="Partner", role="member")
    test_db.add(b)
    test_db.commit()
    return a.id, b.id


@pytest.fixture
def offline(monkeypatch):
    """No network: no descriptions, no H-1B lookups, no pacing sleep."""
    import backend.analyzer.h1b_checker as h1b
    import backend.scraper.sources.company_pages as cp

    async def _no_desc(jobs):
        return []

    async def _no_h1b(job, db=None, company_lookup=None, phrases=None):
        job.h1b_jd_flag = False

    async def _instant(_s):
        return None
    monkeypatch.setattr(cp, "_fetch_descriptions_parallel", _no_desc)
    monkeypatch.setattr(h1b, "check_job_h1b", _no_h1b)
    monkeypatch.setattr(cp.asyncio, "sleep", _instant)


POSTINGS = [
    {"title": "Product Manager", "url": "https://jobs.lever.co/acme/1"},
    {"title": "Senior Product Manager", "url": "https://jobs.lever.co/acme/2"},
    {"title": "Software Engineer", "url": "https://jobs.lever.co/acme/3"},
    {"title": "Product Manager Intern", "url": "https://jobs.lever.co/acme/4"},
]


def _handler(monkeypatch, postings_by_url=None):
    """Stub the ATS handler; returns the list of URLs it was called with."""
    import backend.scraper.sources.company_pages as cp
    calls = []

    async def fake_dispatch(url, **kw):
        calls.append(url)
        if postings_by_url is not None:
            return [dict(p) for p in postings_by_url.get(url, [])]
        return [dict(p) for p in POSTINGS]
    monkeypatch.setattr(cp, "_dispatch_ats", fake_dispatch)
    return calls


def _company(db, name="Acme", url="https://jobs.lever.co/acme", **kw):
    kw.setdefault("active", True)
    kw.setdefault("auto_scoring_depth", "off")
    c = Company(name=name, scrape_urls=[url], **kw)
    db.add(c)
    db.commit()
    return c


def _jobs(db):
    """{account_id: {(title, status)}} across accounts."""
    with unscoped():
        db.expire_all()
        rows = db.query(Job).all()
    out = {}
    for j in rows:
        out.setdefault(j.account_id, set()).add((j.title, j.status))
    return out


def _logs(db):
    with unscoped():
        db.expire_all()
        return db.query(ScrapeLog).all()


# ── companies ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_same_company_url_one_fetch_two_ingests_with_own_filters(test_db, accounts, monkeypatch, offline):
    from backend.scraper.sources.company_pages import scrape_career_pages
    a, b = accounts
    calls = _handler(monkeypatch)
    _company(test_db, title_include_expr="Product", title_exclude_keywords=["intern"])
    with use_account(b):
        # B spells the URL with an upper-case host and a trailing slash: still the same
        # target (QA F6 / R4-04: query params, tracking-looking or not, now count)
        _company(test_db, name="ACME Inc", url="https://Jobs.Lever.co/acme/",
                 title_include_expr="Engineer")

    summary = await scrape_career_pages(force=True)

    assert calls == ["https://jobs.lever.co/acme"], "one handler call for both accounts"
    assert summary["scraped"] == 2 and summary["failed"] == 0
    jobs = _jobs(test_db)
    assert {t for t, s in jobs[a] if s == "new"} == {"Product Manager", "Senior Product Manager"}
    assert {t for t, s in jobs[b] if s == "new"} == {"Software Engineer"}
    # each account stores its own filtered-out rows as ignored (its own dedup memory)
    assert ("Product Manager Intern", "ignored") in jobs[a]
    assert ("Product Manager", "ignored") in jobs[b]

    logs = _logs(test_db)
    by_acc = {l.account_id: l for l in logs}
    assert set(by_acc) == {a, b}
    assert by_acc[a].new_jobs == 2 and by_acc[b].new_jobs == 1
    assert by_acc[a].source == "playwright_Acme" and by_acc[b].source == "playwright_ACME Inc"
    assert by_acc[a].run_id is not None and by_acc[a].run_id == by_acc[b].run_id


@pytest.mark.asyncio
async def test_different_urls_are_two_fetches(test_db, accounts, monkeypatch, offline):
    from backend.scraper.sources.company_pages import scrape_career_pages
    a, b = accounts
    calls = _handler(monkeypatch)
    _company(test_db, url="https://jobs.lever.co/acme?location=Berlin")
    with use_account(b):
        _company(test_db, url="https://jobs.lever.co/acme?location=Paris")

    await scrape_career_pages(force=True)

    assert sorted(calls) == ["https://jobs.lever.co/acme?location=Berlin",
                             "https://jobs.lever.co/acme?location=Paris"]
    logs = _logs(test_db)
    assert len(logs) == 2 and logs[0].run_id != logs[1].run_id


@pytest.mark.asyncio
async def test_same_url_different_max_pages_is_two_targets(test_db, accounts, monkeypatch, offline):
    from backend.scraper.sources.company_pages import scrape_career_pages
    a, b = accounts
    calls = _handler(monkeypatch)
    _company(test_db, max_pages=5)
    with use_account(b):
        _company(test_db, max_pages=10)
    await scrape_career_pages(force=True)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_dedup_is_per_account(test_db, accounts, monkeypatch, offline):
    """A posting A already holds is still new for B."""
    from backend.scraper.sources.company_pages import scrape_career_pages
    a, b = accounts
    _handler(monkeypatch)
    _company(test_db)
    await scrape_career_pages(force=True)            # only A subscribes: 4 rows for A
    with use_account(b):
        _company(test_db)
    await scrape_career_pages(force=True)            # A dedups everything, B gets 4 new

    by_acc = {}
    for l in _logs(test_db):
        by_acc.setdefault(l.account_id, []).append(l.new_jobs)
    assert sorted(by_acc[a]) == [0, 4] and by_acc[b] == [4]


@pytest.mark.asyncio
async def test_due_when_any_subscriber_is_due_shortest_interval(test_db, accounts, monkeypatch, offline):
    from backend.scraper.sources.company_pages import scrape_career_pages
    from backend.scraper.targets import company_key, company_target_due, group_targets
    a, b = accounts
    calls = _handler(monkeypatch)
    ago = datetime.now(timezone.utc) - timedelta(minutes=90)
    ca = _company(test_db, scrape_interval_minutes=600, last_scraped_at=ago)   # not due
    with use_account(b):
        cb = _company(test_db, scrape_interval_minutes=60, last_scraped_at=ago)  # due

    with unscoped():
        (target,) = group_targets([ca, cb], company_key)
    due, effective = company_target_due(target, datetime.now(timezone.utc), 120)
    assert due and effective == 60

    summary = await scrape_career_pages(force=False)
    assert len(calls) == 1 and summary["scraped"] == 2 and summary["skipped"] == []
    assert {l.account_id for l in _logs(test_db)} == {a, b}, "a due target feeds every subscriber"


@pytest.mark.asyncio
async def test_not_due_target_lists_every_subscriber_as_skipped(test_db, accounts, monkeypatch, offline):
    from backend.scraper.sources.company_pages import scrape_career_pages
    a, b = accounts
    calls = _handler(monkeypatch)
    ago = datetime.now(timezone.utc) - timedelta(minutes=5)
    _company(test_db, scrape_interval_minutes=600, last_scraped_at=ago)
    with use_account(b):
        _company(test_db, name="Acme B", scrape_interval_minutes=60, last_scraped_at=ago)
    summary = await scrape_career_pages(force=False)
    assert calls == []
    assert sorted(s["name"] for s in summary["skipped"]) == ["Acme", "Acme B"]


@pytest.mark.asyncio
async def test_one_accounts_ingest_failing_does_not_stop_the_other(test_db, accounts, monkeypatch, offline):
    import backend.scraper.sources.company_pages as cp
    from backend.accounts.context import current_account
    a, b = accounts
    calls = _handler(monkeypatch)
    _company(test_db)
    with use_account(b):
        _company(test_db)

    real_refresh = cp._apply_company_filters

    def _boom(jobs, company, *args):
        if current_account.get() == a:
            raise RuntimeError("owner ingest exploded")
        return real_refresh(jobs, company, *args)
    monkeypatch.setattr(cp, "_apply_company_filters", _boom)
    # a filter is needed for _apply_company_filters to run at all
    from backend.tests.r4_support import set_setting
    set_setting(test_db, "title_exclude_global", '["nothing-matches-this"]')

    summary = await cp.scrape_career_pages(force=True)

    assert len(calls) == 1
    assert summary["scraped"] == 2 and summary["failed"] == 1
    by_acc = {l.account_id: l for l in _logs(test_db)}
    assert "exploded" in (by_acc[a].error or "")
    assert by_acc[b].error is None and by_acc[b].new_jobs == 4
    assert by_acc[a].run_id == by_acc[b].run_id
    assert a not in _jobs(test_db) and len(_jobs(test_db)[b]) == 4


@pytest.mark.asyncio
async def test_shared_fetch_failure_reaches_every_subscriber_once(test_db, accounts, monkeypatch, offline):
    """A fetch that raises is not retried per account; each account logs the error."""
    import backend.scraper.sources.company_pages as cp
    a, b = accounts
    n = {"calls": 0}

    async def _broken(company, urls, browser):
        n["calls"] += 1
        raise RuntimeError("board down")
    monkeypatch.setattr(cp, "_fetch_postings", _broken)
    _company(test_db)
    with use_account(b):
        _company(test_db)
    await cp.scrape_career_pages(force=True)
    assert n["calls"] == 1
    assert all("board down" in (l.error or "") for l in _logs(test_db)) and len(_logs(test_db)) == 2


@pytest.mark.asyncio
async def test_manual_run_all_covers_only_the_callers_account(test_db, accounts, monkeypatch, offline):
    import backend.scraper.orchestrator as orch
    a, b = accounts
    calls = _handler(monkeypatch)
    _company(test_db)
    with use_account(b):
        _company(test_db)
    await orch.run_all(force=True, account_ids=[a])
    assert len(calls) == 1
    assert {l.account_id for l in _logs(test_db)} == {a}


# ── searches (JobSpy) ────────────────────────────────────────────────────────

def _fake_jobspy(monkeypatch, rows):
    import pandas as pd
    import jobspy.model  # noqa: F401  keep the real Country enum importable (backend.countries)
    calls = []
    mod = types.ModuleType("jobspy")

    def scrape_jobs(**kwargs):
        calls.append(kwargs)
        return pd.DataFrame(rows)
    mod.scrape_jobs = scrape_jobs
    monkeypatch.setitem(sys.modules, "jobspy", mod)
    return calls


def _row(site, title, url, days_ago=0):
    return {
        "site": site, "title": title, "company": "Acme", "job_url": url,
        "description": "A perfectly ordinary job description. " * 5,
        "location": "Remote", "min_amount": None, "max_amount": None,
        "date_posted": (datetime.now(timezone.utc) - timedelta(days=days_ago)).date(),
    }


def _search(db, name="PM", **kw):
    kw.setdefault("search_mode", "keyword")
    kw.setdefault("sources", ["indeed", "linkedin"])
    kw.setdefault("search_term", "product manager")
    kw.setdefault("title_include_keywords", [])
    kw.setdefault("title_exclude_keywords", [])
    kw.setdefault("company_filter", [])
    kw.setdefault("company_exclude", [])
    kw.setdefault("auto_scoring_depth", "off")
    s = Search(name=name, active=True, **kw)
    db.add(s)
    db.commit()
    return s


ROWS = [
    _row("indeed", "Product Manager", "https://indeed.test/1", 0),
    _row("indeed", "Product Owner", "https://indeed.test/2", 0),
    _row("indeed", "Product Lead", "https://indeed.test/3", 2),
    _row("linkedin", "PM Generalist", "https://linkedin.test/4", 0),
    _row("linkedin", "Old PM", "https://linkedin.test/5", 5),
]


@pytest.mark.asyncio
async def test_searches_differing_only_in_results_and_hours_share_one_fetch(test_db, accounts, monkeypatch, offline):
    import backend.scraper.orchestrator as orch
    a, b = accounts
    calls = _fake_jobspy(monkeypatch, ROWS)
    # QA F10 / R4-05: close values only (results within 1.5x, same hours bucket)
    _search(test_db, results_wanted=2, hours_old=12)
    with use_account(b):
        _search(test_db, results_wanted=3, hours_old=24, search_term="  Product   Manager ")

    await orch.run_all(force=True)

    assert len(calls) == 1, "one JobSpy call for both accounts"
    assert calls[0]["results_wanted"] == 3 and calls[0]["hours_old"] == 24
    jobs = _jobs(test_db)
    # A: first two rows per board, posted within its window (the 2- and 5-day-old dropped)
    assert {t for t, _ in jobs[a]} == {"Product Manager", "Product Owner", "PM Generalist"}
    # B: everything
    assert {t for t, _ in jobs[b]} == {r["title"] for r in ROWS}
    logs = {l.account_id: l for l in _logs(test_db)}
    assert logs[a].run_id == logs[b].run_id and logs[a].jobs_found == 3 and logs[b].jobs_found == 5
    # `returned` reports this account's trimmed copy
    assert logs[a].source_breakdown["indeed"]["returned"] == 2


@pytest.mark.asyncio
async def test_searches_differing_in_a_fetch_parameter_are_two_fetches(test_db, accounts, monkeypatch, offline):
    import backend.scraper.orchestrator as orch
    a, b = accounts
    calls = _fake_jobspy(monkeypatch, ROWS)
    _search(test_db, location="Berlin")
    with use_account(b):
        _search(test_db, location="Paris")
    await orch.run_all(force=True)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_levels_fyi_url_query_param_makes_a_separate_target(test_db, accounts):
    from backend.scraper.targets import search_key
    a, b = accounts
    s1 = _search(test_db, search_mode="levels_fyi", direct_url="https://www.levels.fyi/jobs?location=berlin")
    s2 = _search(test_db, search_mode="levels_fyi", direct_url="HTTPS://WWW.levels.fyi/jobs/?location=berlin")
    s3 = _search(test_db, search_mode="levels_fyi", direct_url="https://www.levels.fyi/jobs?location=paris")
    assert search_key(s1) == search_key(s2) != search_key(s3)


def test_personal_session_modes_are_never_shared(test_db):
    from backend.scraper.targets import search_key
    s1 = _search(test_db, search_mode="jobright")
    s2 = _search(test_db, search_mode="jobright")
    assert search_key(s1) != search_key(s2)


@pytest.mark.asyncio
async def test_search_due_uses_the_shortest_interval(test_db, accounts):
    from backend.scraper.targets import group_targets, search_key, search_target_due
    a, b = accounts
    ago = datetime.now(timezone.utc) - timedelta(minutes=45)
    s1 = _search(test_db, run_interval_minutes=120, last_run_at=ago)
    with use_account(b):
        s2 = _search(test_db, run_interval_minutes=30, last_run_at=ago)
    with unscoped():
        (t,) = group_targets([s1, s2], search_key)
    assert search_target_due(t, datetime.now(timezone.utc)) == (True, 30)
    s2.run_interval_minutes = 60
    assert search_target_due(t, datetime.now(timezone.utc)) == (False, 60)


@pytest.mark.asyncio
async def test_one_search_ingest_failing_does_not_stop_the_other(test_db, accounts, monkeypatch, offline):
    import backend.scraper.orchestrator as orch
    import backend.scraper.sources.jobspy as js
    from backend.accounts.context import current_account
    a, b = accounts
    calls = _fake_jobspy(monkeypatch, ROWS)
    real = js.apply_company_filter

    def _boom(df, cf):
        if current_account.get() == a:
            raise RuntimeError("owner ingest exploded")
        return real(df, cf)
    monkeypatch.setattr(js, "apply_company_filter", _boom)
    _search(test_db)
    with use_account(b):
        _search(test_db)
    await orch.run_all(force=True)
    assert len(calls) == 1
    logs = {l.account_id: l for l in _logs(test_db)}
    assert "exploded" in (logs[a].error or "") and logs[b].error is None and logs[b].new_jobs == 5


def test_trim_postings_unit():
    import pandas as pd
    from backend.scraper.sources.jobspy import trim_postings
    df = pd.DataFrame(ROWS + [dict(_row("indeed", "No date", "https://indeed.test/9"), date_posted=None)])
    same = trim_postings(df, 50, 168, 50, 168)
    assert len(same) == len(df)
    by_age = trim_postings(df, 50, 24, 50, 168)
    assert set(by_age["title"]) == {"Product Manager", "Product Owner", "PM Generalist", "No date"}
    by_count = trim_postings(df, 1, 168, 50, 168)
    assert list(by_count["title"]) == ["Product Manager", "PM Generalist"]


# ── single account: identical to the pre-MT-8 path ──────────────────────────

def _snapshot(db):
    with unscoped():
        db.expire_all()
        jobs = sorted(
            (j.account_id, j.external_id, j.content_hash, j.company, j.title, j.url, j.source,
             j.status, j.search_id, j.location, j.seen)
            for j in db.query(Job).all())
        logs = sorted(
            ((l.account_id, l.search_id, l.company_id, l.source, l.jobs_found, l.new_jobs,
              l.error, l.is_warning, str(l.source_breakdown))
             for l in db.query(ScrapeLog).all()), key=str)
    return jobs, logs


def _reset(db, search, company):
    with unscoped():
        db.query(Job).delete()
        db.query(ScrapeLog).delete()
        db.commit()
    search.last_run_at = None
    company.last_scraped_at = None
    db.commit()


@pytest.mark.asyncio
async def test_single_account_path_is_identical_to_the_old_path(test_db, monkeypatch, offline):
    """Golden comparison: run_all (targets, fan-out) vs. the pre-MT-8 sequence (each search
    through run_search, each company through scrape_single_career_page with the batch dedup
    set, one ScrapeLog row each, first run marks jobs seen)."""
    import backend.scraper.orchestrator as orch
    from backend.models.db import get_existing_external_ids
    from backend.scraper.sources.company_pages import (
        record_company_scrape_log, scrape_single_career_page,
    )
    _fake_jobspy(monkeypatch, ROWS)
    _handler(monkeypatch)
    search = _search(test_db, title_exclude_keywords=["old"], results_wanted=1)
    company = _company(test_db, title_exclude_keywords=["intern"])

    await orch.run_all(force=True)
    new = _snapshot(test_db)
    assert all(l.run_id is not None for l in _logs(test_db))

    _reset(test_db, search, company)
    # the old path, spelled out
    result = await orch.run_search(search)
    orch._record_search_log(test_db, search, result)
    ids = get_existing_external_ids(test_db)
    result = await scrape_single_career_page(company, known_external_ids=ids)
    record_company_scrape_log(company.id, company.name, result, db=test_db)
    for j in test_db.query(Job).filter(Job.seen == False).all():  # noqa: E712
        j.seen = True
    test_db.commit()
    old = _snapshot(test_db)

    assert new == old
    assert len(new[0]) == len(ROWS) + len(POSTINGS) - 0 and len(new[1]) == 2
