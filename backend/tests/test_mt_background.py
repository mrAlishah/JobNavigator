"""MT-7 · Scheduled background work runs once per account.

A is the owner, B a member. Each per-account job (digest, auto-reject, cleanup, the
saved-job scoring pass, email check) runs under each account's context: it sees only that
account's rows, writes JobRun / ActivityLog / LlmCallLog rows stamped with that account,
and dedups under that account's key. One account failing does not stop the other.
Install-level jobs (H-1B refresh) run once and read every account's rows.
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from backend.accounts.context import current_account, unscoped, use_account
from backend.models.db import (
    Account, ActivityLog, Application, Job, JobRun, LlmCallLog,
)
from backend.tests.r4_support import make_job, set_setting


@pytest.fixture
def accounts(test_db):
    a = test_db.query(Account).filter(Account.role == "owner").one()
    b = Account(name="Partner", role="member")
    test_db.add(b)
    test_db.commit()
    return a.id, b.id


@pytest.fixture(autouse=True)
def _clear_running():
    import backend.job_monitor as jm
    jm._running.clear()
    yield
    jm._running.clear()


def _runs(test_db, job_type):
    """{account_id: [JobRun, ...]} for one job type, across accounts."""
    with unscoped():
        test_db.expire_all()
        rows = test_db.query(JobRun).filter(JobRun.job_type == job_type).all()
    out = {}
    for r in rows:
        out.setdefault(r.account_id, []).append(r)
    return out


def _old(days):
    return datetime.now(timezone.utc) - timedelta(days=days)


def _make_app(test_db, status="applied", applied_days_ago=60):
    job = make_job(test_db, status="applied")
    app = Application(job_id=job.id, status=status, applied_at=_old(applied_days_ago),
                      status_transitions=[])
    test_db.add(app)
    test_db.commit()
    return app.id


# ── account listing ──────────────────────────────────────────────────────────

def test_all_account_ids_lists_the_owner_first(test_db, accounts):
    from backend.accounts.background import all_account_ids, gmail_account_ids
    a, b = accounts
    c = Account(name="Earlier member", role="member", created_at=_old(400))
    test_db.add(c)
    test_db.commit()
    ids = all_account_ids()
    assert ids[0] == a and set(ids) == {a, b, c.id}
    assert ids.index(c.id) < ids.index(b)          # members by creation time
    assert gmail_account_ids() == [a]              # no member connected Gmail: the owner (MT-19)


def test_all_account_ids_falls_back_to_the_owner_context_when_unreadable(monkeypatch):
    import backend.models.db as db_mod
    from backend.accounts.background import all_account_ids

    def _broken():
        raise RuntimeError("db down")
    monkeypatch.setattr(db_mod, "SessionLocal", _broken)
    assert all_account_ids() == [None]


# ── digest ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_digest_runs_per_account_with_only_its_own_counts(test_db, accounts, monkeypatch):
    import backend.notifier.telegram as tg
    from backend.scheduler import send_daily_digest
    a, b = accounts
    for _ in range(2):
        make_job(test_db, status="new", discovered_at=_old(0))
    with use_account(b):
        for _ in range(3):
            make_job(test_db, status="new", discovered_at=_old(0))
        make_job(test_db, status="new", discovered_at=_old(0), best_cv_score=90)
        _make_app(test_db, status="interview", applied_days_ago=3)

    sent = []

    async def _capture(chat_id, text, *a_, **k):
        sent.append((current_account.get(), text))
        return True
    monkeypatch.setattr(tg, "_send_message", _capture)
    monkeypatch.setattr(tg, "_is_enabled", lambda: True)
    monkeypatch.setattr(tg, "_get_chat_id", lambda: "123")
    # STRONG_MATCH_SQL is Postgres-only (jsonb); an equivalent on best_cv_score for SQLite.
    monkeypatch.setattr(tg, "STRONG_MATCH_SQL", "COALESCE(best_cv_score, 0) >= :threshold")

    await send_daily_digest()

    assert [acc for acc, _ in sent] == [a, b]
    a_text, b_text = sent[0][1], sent[1][1]
    assert "New jobs: 2" in a_text and "Strong matches: 0" in a_text and "Active apps: 0" in a_text
    # B: 3 new + the strong one (its interview job is status "applied", not new)
    assert "New jobs: 4" in b_text and "Strong matches: 1" in b_text and "Active apps: 1" in b_text

    runs = _runs(test_db, "daily_digest")
    assert set(runs) == {a, b}
    assert all(len(v) == 1 and v[0].status == "completed" for v in runs.values())
    # each run's summary counts only its own account's activity rows
    assert runs[a][0].result_summary == runs[b][0].result_summary == "1 alert"
    with unscoped():
        logs = test_db.query(ActivityLog).filter(ActivityLog.type == "telegram").all()
    assert sorted(str(l.account_id) for l in logs) == sorted([str(a), str(b)])
    assert {l.account_id: l.message for l in logs}[b].startswith("Daily digest sent: 4 new jobs")


@pytest.mark.asyncio
async def test_one_account_failing_does_not_stop_the_next(test_db, accounts, monkeypatch):
    import backend.notifier.telegram as tg
    from backend.scheduler import send_daily_digest
    a, b = accounts
    ran = []

    async def _digest():
        if current_account.get() == a:
            raise RuntimeError("owner digest exploded")
        ran.append(current_account.get())
    monkeypatch.setattr(tg, "send_digest", _digest)

    with pytest.raises(RuntimeError, match="owner digest exploded"):   # APScheduler logs it
        await send_daily_digest()
    assert ran == [b]
    runs = _runs(test_db, "daily_digest")
    assert runs[a][0].status == "failed" and "exploded" in runs[a][0].error
    assert runs[b][0].status == "completed"


@pytest.mark.asyncio
async def test_a_members_scheduled_run_has_its_own_dedup_key(test_db, accounts, monkeypatch):
    """The owner's digest still running does not block B's (and vice versa)."""
    import backend.job_monitor as jm
    import backend.notifier.telegram as tg
    from backend.scheduler import send_daily_digest
    a, b = accounts
    keys_seen = []

    async def _digest():
        keys_seen.append(sorted(jm._running))
    monkeypatch.setattr(tg, "send_digest", _digest)

    with use_account(a):
        async with jm.tracked_run("daily_digest", "manual"):   # owner's run in flight
            await send_daily_digest()                           # owner skipped, B runs

    assert keys_seen == [sorted(["daily_digest", f"daily_digest@{b}"])]
    runs = _runs(test_db, "daily_digest")
    assert [r.trigger for r in runs[a]] == ["manual"]
    assert [r.trigger for r in runs[b]] == ["scheduler"]


# ── auto-reject ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_auto_reject_touches_only_each_accounts_stale_applications(test_db, accounts):
    from backend.scheduler import run_auto_reject
    a, b = accounts
    set_setting(test_db, "auto_reject_after_days", "30")
    a_stale = _make_app(test_db, applied_days_ago=60)
    a_fresh = _make_app(test_db, applied_days_ago=5)
    with use_account(b):
        b_stale = [_make_app(test_db, applied_days_ago=90) for _ in range(2)]
        b_offer = _make_app(test_db, status="offer", applied_days_ago=90)

    await run_auto_reject()

    runs = _runs(test_db, "auto_reject")
    assert runs[a][0].result_summary == "1 application auto-rejected (>30d silent)"
    assert runs[b][0].result_summary == "2 applications auto-rejected (>30d silent)"
    with unscoped():
        test_db.expire_all()
        status = {x.id: x.status for x in test_db.query(Application).all()}
    assert status[a_stale] == "rejected" and status[a_fresh] == "applied"
    assert all(status[i] == "rejected" for i in b_stale) and status[b_offer] == "offer"


# ── cleanup ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_cleanup_deletes_only_each_accounts_old_skipped_jobs(test_db, accounts):
    from backend.scheduler import run_job_cleanup_auto
    a, b = accounts
    set_setting(test_db, "job_archive_after_days", "30")
    for _ in range(2):
        make_job(test_db, status="skip", discovered_at=_old(100))
    a_keep = make_job(test_db, status="skip", discovered_at=_old(2)).id
    with use_account(b):
        make_job(test_db, status="skip", discovered_at=_old(100))
        b_keep = make_job(test_db, status="new", discovered_at=_old(100)).id

    await run_job_cleanup_auto()

    runs = _runs(test_db, "job_cleanup")
    assert runs[a][0].result_summary == "2 skipped jobs deleted (>30d old)"
    assert runs[b][0].result_summary == "1 skipped job deleted (>30d old)"
    with unscoped():
        test_db.expire_all()
        left = {j.id: j.account_id for j in test_db.query(Job).all()}
    assert left == {a_keep: a, b_keep: b}


# ── scrape_all: owner scrape, per-account saved-job scoring ─────────────────

@pytest.mark.asyncio
async def test_saved_job_scoring_pass_runs_per_account(test_db, accounts, monkeypatch):
    import backend.analyzer.cv_scorer as scorer
    import backend.scraper.orchestrator as orch
    from backend.analyzer.llm_logger import log_llm_call
    from backend.models.db import SessionLocal
    from backend.scheduler import run_all_scrapes
    a, b = accounts
    a_saved = make_job(test_db, status="saved").id
    with use_account(b):
        b_saved = make_job(test_db, status="saved").id

    scraped_as, scored = [], {}

    async def _run_all(*_a, **_k):
        scraped_as.append(current_account.get())
        return {}

    async def _score(status="saved"):
        s = SessionLocal()
        try:
            ids = [j.id for j in s.query(Job).filter(Job.status == status).all()]
        finally:
            s.close()
        scored[current_account.get()] = ids
        for jid in ids:
            log_llm_call("score_light", "fake", "fake-model", {"input_tokens": 1}, job_id=jid)
    monkeypatch.setattr(orch, "run_all", _run_all)
    monkeypatch.setattr(scorer, "analyze_unscored_jobs", _score)

    await run_all_scrapes()

    assert scraped_as == [a]                       # scraping stays the owner's until MT-8
    assert scored == {a: [a_saved], b: [b_saved]}
    with unscoped():
        calls = {c.job_id: c.account_id for c in test_db.query(LlmCallLog).all()}
    assert calls == {a_saved: a, b_saved: b}
    runs = _runs(test_db, "scrape_all")
    assert set(runs) == {a} and runs[a][0].status == "completed"


# ── email ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_email_check_runs_only_for_the_gmail_account(test_db, accounts, monkeypatch):
    import backend.email_monitor.gmail_client as gc
    from backend.activity import log_activity
    from backend.scheduler import run_email_check
    a, b = accounts
    checked = []

    async def _check():
        checked.append(current_account.get())
        log_activity("email", "reply from recruiter")
    monkeypatch.setattr(gc, "check_emails", _check)

    await run_email_check()

    assert checked == [a]
    runs = _runs(test_db, "email_check")
    assert set(runs) == {a} and runs[a][0].result_summary == "1 reply"
    with unscoped():
        assert [l.account_id for l in test_db.query(ActivityLog).filter(
            ActivityLog.type == "email").all()] == [a]


# ── install-level ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_h1b_refresh_is_one_install_run_over_every_accounts_companies(
        test_db, accounts, monkeypatch):
    import backend.analyzer.h1b_checker as h1b
    from backend.scheduler import refresh_h1b_data
    a, b = accounts
    make_job(test_db, company="Alpha Corp")
    with use_account(b):
        make_job(test_db, company="Beta Inc")
    fetched = []

    async def _resolve(name, **_k):
        fetched.append(name)
        return None
    monkeypatch.setattr(h1b, "resolve_company_h1b_detached", _resolve)

    await refresh_h1b_data()      # two names, 0.5 s apart

    assert sorted(fetched) == ["Alpha Corp", "Beta Inc"]
    runs = _runs(test_db, "h1b_refresh")
    assert set(runs) == {a} and len(runs[a]) == 1


@pytest.mark.asyncio
async def test_scheduled_jobs_run_without_a_caller_context(test_db, accounts, monkeypatch):
    """APScheduler calls the job with no account context at all."""
    import backend.notifier.telegram as tg
    from backend.scheduler import send_daily_digest
    seen = []

    async def _digest():
        seen.append(current_account.get())
    monkeypatch.setattr(tg, "send_digest", _digest)
    with use_account(None):
        await send_daily_digest()
    assert seen == list(accounts)
