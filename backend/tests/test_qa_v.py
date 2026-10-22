"""QA V (final verification) · regression tests.

V-02  the due check no longer skips every other scheduler tick: a target runs on the
      tick nearest its due time, and the scheduled batch stamps its own START time, so
      a 60-min interval runs on every 60-min tick (companies and searches, per account
      and shared), and custom intervals run every N-th tick.
V-03  a member's /monitor/history (and so the rail health line and Stats) includes the
      install-level scheduled scrape_all runs, with a summary built from the member's
      OWN ScrapeLog rows only — nothing of another account (summary, error, meta).
V-04  the body-exclusion log line names the company, not "?".
V-05  an account without base résumés logs one INFO line (not a WARNING every tick).
"""
import logging
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from backend.accounts.context import unscoped, use_account
from backend.models.db import Account, Company, JobRun, ScrapeLog, Search, Setting
from backend.tests.r4_support import (  # noqa: F401
    ACT_AS, client, _clear_running_state, assert_clean,
)

MARKER = "ZZV-OWNER-ONLY-9K2"
T0 = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)


# ── fixtures / helpers ───────────────────────────────────────────────────────

@pytest.fixture
def accounts(test_db):
    a = test_db.query(Account).filter(Account.role == "owner").one()
    b = Account(name="Partner", role="member", created_at=T0 - timedelta(days=30))
    test_db.add(b)
    test_db.commit()
    return a.id, b.id


class _Clock:
    """A settable clock patched in as the modules' `datetime` (now() only)."""
    t = T0

    @classmethod
    def install(cls, monkeypatch, *modules, start=T0):
        cls.t = start
        clock = cls

        class FakeDT(datetime):
            @classmethod
            def now(cls_, tz=None):
                return clock.t if tz is not None else clock.t.replace(tzinfo=None)
        for m in modules:
            monkeypatch.setattr(m, "datetime", FakeDT)
        # Cross-run fetch reuse measures its window on targets' own clock: ticks an hour
        # apart must not reuse each other's fetch.
        import time as _time
        from types import SimpleNamespace
        import backend.scraper.targets as targets
        monkeypatch.setattr(targets, "time", SimpleNamespace(
            time=_time.time, monotonic=lambda: clock.t.timestamp()))
        return cls

    @classmethod
    def advance(cls, minutes):
        cls.t = cls.t + timedelta(minutes=minutes)


@pytest.fixture
def offline(monkeypatch):
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


def _company(db, name="Acme", url="https://jobs.lever.co/acme", **kw):
    kw.setdefault("active", True)
    kw.setdefault("auto_scoring_depth", "off")
    c = Company(name=name, scrape_urls=[url], **kw)
    db.add(c)
    db.commit()
    return c


def _set_interval(db, minutes):
    row = db.query(Setting).filter(Setting.key == "scrape_interval_minutes").first()
    if row is None:
        db.add(Setting(key="scrape_interval_minutes", value=str(minutes)))
    else:
        row.value = str(minutes)
    db.commit()


# ══ V-02 · due check, unit level ═════════════════════════════════════════════

def _simulate(interval, tick, delays, own=True, kind="company"):
    """Tick k at T0 + k*tick (+ a few seconds of jitter); a run stamps `last` at the tick
    plus `delay` (the worst case: the stamp lands after the fetch). Returns the tick
    indexes that ran."""
    from backend.scraper.targets import company_due, search_due
    row = SimpleNamespace(scrape_interval_minutes=interval if own else None,
                          run_interval_minutes=interval, last_scraped_at=None, last_run_at=None)
    ran = []
    for k, delay in enumerate(delays):
        now = T0 + timedelta(minutes=k * tick, seconds=(k * 7) % 20)
        if kind == "company":
            due = company_due(row, now, tick)[0]
        else:
            due = search_due(row, now, tick)[0]
        if due:
            ran.append(k)
            stamp = now + timedelta(minutes=delay)
            row.last_scraped_at = row.last_run_at = stamp
    return ran


@pytest.mark.parametrize("delay", [0, 0.5, 5, 25, 29])
def test_global_interval_runs_on_every_tick(delay):
    # no interval of its own → the global one, which IS the tick period
    assert _simulate(60, 60, [delay] * 8, own=False) == list(range(8))


@pytest.mark.parametrize("delay", [0, 3, 25])
def test_custom_interval_equal_to_the_tick_runs_every_tick(delay):
    assert _simulate(60, 60, [delay] * 6) == list(range(6))
    assert _simulate(60, 60, [delay] * 6, kind="search") == list(range(6))


@pytest.mark.parametrize("delay", [0, 10, 25])
def test_custom_interval_runs_every_nth_tick(delay):
    assert _simulate(180, 60, [delay] * 10) == [0, 3, 6, 9]
    assert _simulate(120, 60, [delay] * 7, kind="search") == [0, 2, 4, 6]
    assert _simulate(30, 10, [delay / 10] * 7) == [0, 3, 6]


def test_two_minute_tick_like_the_live_check():
    # FINAL-VERIFY tick 2 skipped with "not due (2m of 2m)"
    assert _simulate(2, 2, [0.05] * 5, own=False) == [0, 1, 2, 3, 4]


def test_not_due_well_before_the_interval():
    from backend.scraper.targets import company_due, search_due
    row = SimpleNamespace(scrape_interval_minutes=600, run_interval_minutes=600,
                          last_scraped_at=T0, last_run_at=T0)
    assert company_due(row, T0 + timedelta(minutes=60), 60)[0] is False
    assert search_due(row, T0 + timedelta(minutes=60), 60)[0] is False
    assert company_due(row, T0 + timedelta(minutes=571), 60)[0] is True   # nearest tick


def test_no_tick_keeps_the_strict_comparison():
    from backend.scraper.targets import search_due
    row = SimpleNamespace(run_interval_minutes=60, last_run_at=T0)
    assert search_due(row, T0 + timedelta(minutes=59))[0] is False
    assert search_due(row, T0 + timedelta(minutes=60))[0] is True


# ══ V-02 · the real batch over simulated ticks ═══════════════════════════════

def _fetching_handler(monkeypatch, fetch_minutes=7):
    import backend.scraper.sources.company_pages as cp
    calls = []

    async def fake_dispatch(url, **kw):
        calls.append((_Clock.t, url))
        _Clock.advance(fetch_minutes)         # the fetch takes a while
        slug = url.rstrip("/").rsplit("/", 1)[-1]
        return [{"title": f"Product Manager {i}", "url": f"https://jobs.lever.co/{slug}/{i}"}
                for i in range(2)]
    monkeypatch.setattr(cp, "_dispatch_ats", fake_dispatch)
    return calls


@pytest.mark.asyncio
async def test_company_batch_runs_on_every_60_minute_tick(test_db, accounts, monkeypatch, offline):
    import backend.scraper.sources.company_pages as cp
    a, b = accounts
    _set_interval(test_db, 60)
    _company(test_db, name="Shared", url="https://jobs.lever.co/shared")       # owner
    _company(test_db, name="Solo", url="https://jobs.lever.co/solo")           # owner only
    with use_account(b):
        _company(test_db, name="Shared", url="https://jobs.lever.co/shared")   # member: shared target
        _company(test_db, name="Mine", url="https://jobs.lever.co/mine")       # member only
    _Clock.install(monkeypatch, cp)
    calls = _fetching_handler(monkeypatch)

    per_tick = []
    for k in range(4):
        _Clock.t = T0 + timedelta(minutes=60 * k, seconds=k * 3)
        before = len(calls)
        summary = await cp.scrape_career_pages(force=False)
        per_tick.append(sorted(u.rsplit("/", 1)[-1] for _, u in calls[before:]))
        assert not [s for s in summary["skipped"] if s["reason"].startswith("not due")], (k, summary)
    # one fetch per target per tick: the shared one once for both accounts
    assert per_tick == [["mine", "shared", "solo"]] * 4
    test_db.expire_all()
    with unscoped():
        stamps = {(c.name, c.account_id): c.last_scraped_at for c in test_db.query(Company).all()}
    # the batch stamped its own start (tick 3), not the end of a 7-minute fetch
    start3 = (T0 + timedelta(minutes=180, seconds=9)).replace(tzinfo=None)
    assert all(s.replace(tzinfo=None) == start3 for s in stamps.values()), stamps


@pytest.mark.asyncio
async def test_company_batch_custom_interval_every_third_tick(test_db, accounts, monkeypatch, offline):
    import backend.scraper.sources.company_pages as cp
    a, b = accounts
    _set_interval(test_db, 60)
    with use_account(b):
        _company(test_db, name="Slow", url="https://jobs.lever.co/slow", scrape_interval_minutes=180)
    _Clock.install(monkeypatch, cp)
    calls = _fetching_handler(monkeypatch, fetch_minutes=12)
    ran = []
    for k in range(7):
        _Clock.t = T0 + timedelta(minutes=60 * k)
        before = len(calls)
        await cp.scrape_career_pages(force=False)
        if len(calls) > before:
            ran.append(k)
    assert ran == [0, 3, 6]


@pytest.mark.asyncio
async def test_search_batch_runs_on_every_60_minute_tick(test_db, accounts, monkeypatch):
    import backend.scraper.orchestrator as orch
    a, b = accounts
    _set_interval(test_db, 60)
    ran = []

    async def _run(search, proxy_url=None, **kw):
        ran.append((_Clock.t, search.name))
        _Clock.advance(9)                      # the fetch takes a while
        return {"jobs_found": 1, "new_jobs": 0, "error": None, "duration": 1}
    monkeypatch.setattr(orch, "run_search", _run)
    for acc, name in ((None, "Hourly"), (b, "Member hourly")):
        ctx = use_account(acc) if acc else unscoped()
        with ctx:
            s = Search(name=name, search_mode="keyword", search_term=f"pm {name}", active=True,
                       run_interval_minutes=60)
            test_db.add(s)
            test_db.commit()
    with use_account(b):
        test_db.add(Search(name="Two-hourly", search_mode="keyword", search_term="eng",
                           active=True, run_interval_minutes=120))
        test_db.commit()
    _Clock.install(monkeypatch, orch)

    per_tick = []
    for k in range(5):
        _Clock.t = T0 + timedelta(minutes=60 * k, seconds=k * 2)
        before = len(ran)
        await orch.run_all(force=False)
        per_tick.append(sorted(n for _, n in ran[before:]))
    assert [("Hourly" in t, "Member hourly" in t) for t in per_tick] == [(True, True)] * 5
    assert [k for k, t in enumerate(per_tick) if "Two-hourly" in t] == [0, 2, 4]


# ══ V-03 · a member sees the scheduled scrape_all (its own part only) ═════════

def _seed_scheduled(test_db, a, b, *, status="completed", started=None, error=None):
    started = started or (datetime.now(timezone.utc) - timedelta(minutes=10))
    finished = started + timedelta(minutes=2)
    with unscoped():
        oc = Company(name=f"{MARKER} Owner Co", scrape_urls=["https://x.example/o"], account_id=a)
        test_db.add(oc)
        test_db.flush()
        run = JobRun(job_type="scrape_all", trigger="scheduler", status=status,
                     started_at=started, finished_at=finished, duration_seconds=120.0,
                     result_summary=f"4 sources - +9 new - per account: {MARKER} +7, Partner +2",
                     error=error, meta={"note": MARKER}, account_id=a)
        test_db.add(run)
        test_db.add(ScrapeLog(source=f"playwright_{MARKER}", company_id=oc.id, jobs_found=8,
                              new_jobs=7, error=MARKER, account_id=a,
                              ran_at=started + timedelta(seconds=30)))
        test_db.commit()
    with use_account(b):
        mc = Company(name="Member Co", scrape_urls=["https://x.example/m"])
        test_db.add(mc)
        test_db.flush()
        test_db.add(ScrapeLog(source="playwright_Member Co", company_id=mc.id, jobs_found=3,
                              new_jobs=2, ran_at=started + timedelta(seconds=40)))
        # a later manual run of the member: outside the window, not part of this run
        test_db.add(ScrapeLog(source="playwright_Member Co", company_id=mc.id, jobs_found=3,
                              new_jobs=5, ran_at=finished + timedelta(minutes=3)))
        test_db.commit()
    return run.id


def _no_owner_trace(resp, a):
    text = resp.text
    assert MARKER not in text
    assert str(a) not in text


def test_member_history_includes_the_scheduled_run_with_its_own_part(test_db, accounts, client):
    a, b = accounts
    run_id = _seed_scheduled(test_db, a, b)
    r = assert_clean(client.get("/api/monitor/history",
                                params={"limit": 1, "job_type": "scrape_all"},
                                headers={ACT_AS: str(b)}), 200)
    rows = r.json()
    assert [x["id"] for x in rows] == [str(run_id)]
    row = rows[0]
    assert row["status"] == "completed" and row["trigger"] == "scheduler"
    assert row["result_summary"] == "1 source - +2 new"
    assert row["error"] is None and row["meta"] == {"install_run": True}
    _no_owner_trace(r, a)

    # the run detail, as Stats / fetchRunOutcome read it
    d = assert_clean(client.get(f"/api/monitor/run/{run_id}", headers={ACT_AS: str(b)}), 200)
    assert d.json()["result_summary"] == "1 source - +2 new"
    _no_owner_trace(d, a)

    # the owner still sees the full line
    o = assert_clean(client.get("/api/monitor/history",
                                params={"limit": 1, "job_type": "scrape_all"}), 200)
    assert MARKER in o.json()[0]["result_summary"]


def test_member_failed_scheduled_run_shows_red_without_the_owners_error(test_db, accounts, client):
    a, b = accounts
    run_id = _seed_scheduled(test_db, a, b, status="failed",
                             error=f"RuntimeError: {MARKER} board broke")
    r = assert_clean(client.get("/api/monitor/history",
                                params={"limit": 1, "job_type": "scrape_all"},
                                headers={ACT_AS: str(b)}), 200)
    row = r.json()[0]
    assert row["id"] == str(run_id) and row["status"] == "failed"
    assert row["error"] and "owner" in row["error"]
    _no_owner_trace(r, a)
    f = assert_clean(client.get("/api/monitor/history", params={"status": "failed"},
                                headers={ACT_AS: str(b)}), 200)
    assert [x["id"] for x in f.json()] == [str(run_id)]


def test_member_history_merges_by_time_and_pages(test_db, accounts, client):
    a, b = accounts
    now = datetime.now(timezone.utc)
    sched = _seed_scheduled(test_db, a, b, started=now - timedelta(minutes=30))
    with use_account(b):
        newer = JobRun(job_type="scrape_all", trigger="manual", status="completed",
                       started_at=now - timedelta(minutes=5), finished_at=now,
                       result_summary="member manual")
        older = JobRun(job_type="email_check", trigger="scheduler", status="completed",
                       started_at=now - timedelta(hours=2), finished_at=now - timedelta(hours=2))
        test_db.add_all([newer, older])
        test_db.commit()
    with unscoped():
        # owner runs a member never sees: a manual run, another job type, and a
        # scheduled run from before the member existed
        test_db.add_all([
            JobRun(job_type="scrape_all", trigger="manual", status="completed", account_id=a,
                   started_at=now - timedelta(minutes=20), result_summary=MARKER),
            JobRun(job_type="email_check", trigger="scheduler", status="completed", account_id=a,
                   started_at=now - timedelta(minutes=25), result_summary=MARKER),
            JobRun(job_type="scrape_all", trigger="scheduler", status="completed", account_id=a,
                   started_at=T0 - timedelta(days=60), result_summary=MARKER),
        ])
        test_db.commit()
    r = assert_clean(client.get("/api/monitor/history", headers={ACT_AS: str(b)}), 200)
    assert [x["id"] for x in r.json()] == [str(newer.id), str(sched), str(older.id)]
    _no_owner_trace(r, a)
    p = assert_clean(client.get("/api/monitor/history", params={"limit": 1, "offset": 1},
                                headers={ACT_AS: str(b)}), 200)
    assert [x["id"] for x in p.json()] == [str(sched)]
    e = assert_clean(client.get("/api/monitor/history", params={"job_type": "email_check"},
                                headers={ACT_AS: str(b)}), 200)
    assert [x["id"] for x in e.json()] == [str(older.id)]


def test_member_cannot_open_other_owner_runs(test_db, accounts, client):
    a, b = accounts
    with unscoped():
        manual = JobRun(job_type="scrape_all", trigger="manual", status="completed",
                        account_id=a, started_at=datetime.now(timezone.utc), result_summary=MARKER)
        test_db.add(manual)
        test_db.commit()
    assert_clean(client.get(f"/api/monitor/run/{manual.id}", headers={ACT_AS: str(b)}), 404)


# ══ V-04 · body-exclusion log names the company ══════════════════════════════

@pytest.mark.asyncio
async def test_body_exclusion_log_names_the_company(test_db, monkeypatch, caplog):
    import backend.analyzer.h1b_checker as h1b
    import backend.scraper.sources.company_pages as cp

    async def _no_desc(jobs):
        return []

    async def _flag(job, db=None, company_lookup=None, phrases=None):
        job.h1b_jd_flag = True
        job._h1b_matched_phrase = "10+ years"

    async def _postings(url, **kw):
        return [{"title": "Partnerships Manager", "url": "https://jobs.lever.co/acme/1"}]
    monkeypatch.setattr(cp, "_fetch_descriptions_parallel", _no_desc)
    monkeypatch.setattr(h1b, "check_job_h1b", _flag)
    monkeypatch.setattr(cp, "_dispatch_ats", _postings)
    c = _company(test_db, name="Addepar", url="https://jobs.lever.co/acme")
    with caplog.at_level(logging.INFO):
        await cp.scrape_single_career_page(c)
    lines = [r.getMessage() for r in caplog.records if "body exclusion" in r.getMessage()]
    assert lines and all("@ Addepar —" in m for m in lines), lines
    assert not any("@ ?" in m for m in lines)


# ══ V-05 · no base résumés: one INFO line, not a WARNING per tick ════════════

@pytest.mark.asyncio
async def test_no_base_resumes_logs_info_once(test_db, accounts, caplog):
    import backend.analyzer.cv_scorer as cs
    a, b = accounts
    cs._no_resume_logged.clear()
    with caplog.at_level(logging.DEBUG, logger="jobnavigator.cv_scorer"):
        with use_account(b):
            for _ in range(3):           # three scheduler ticks
                await cs._analyze_unscored_jobs("new", set())
    recs = [r for r in caplog.records if r.getMessage() == cs.NO_BASE_RESUMES_MESSAGE]
    assert [r.levelno for r in recs] == [logging.INFO, logging.DEBUG, logging.DEBUG]
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert not [r for r in caplog.records if "No CVs" in r.getMessage()]
    cs._no_resume_logged.clear()
