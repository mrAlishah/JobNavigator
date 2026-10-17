"""MT-6 · Monitor, stats, tracer redirect, Telegram callbacks per account.

A is the owner (the request context), B a member. In-memory runs carry their account and
dedup per account; the timeline runs on SQLite and counts only A's jobs; public routes
(tracer redirect, Telegram webhook callbacks) resolve the row across accounts and then act
as the row's own account.
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from backend.accounts.context import unscoped, use_account
from backend.models.db import Account, Application, Job, Resume, TracerClickEvent, TracerLink
from backend.tests.r4_support import client, make_job, make_resume  # noqa: F401


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


# ── in-memory monitor ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_each_account_dedups_and_sees_only_its_own_runs(test_db, accounts):
    import backend.job_monitor as jm
    a, b = accounts
    gate = asyncio.Event()

    async def _work():
        await gate.wait()

    with use_account(a):
        jm.launch_background("scrape_all", _work)
        with pytest.raises(jm.JobAlreadyRunningError):
            jm.launch_background("scrape_all", _work)        # 409 within one account
    with use_account(b):
        jm.launch_background("scrape_all", _work)            # B may start its own
        with pytest.raises(jm.JobAlreadyRunningError):
            jm.launch_background("scrape_all", _work)
        mine = jm.get_all_running()
        assert len(mine) == 1 and jm.is_running("scrape_all") is not None
        assert jm._get_running_by_job_type("scrape_all") is not None
    with use_account(a):
        assert len(jm.get_all_running()) == 1
        assert "scrape_all" in jm._running                   # the owner keeps the old key
    assert len(jm.get_all_running(all_accounts=True)) == 2   # hook for the owner's "all" view
    gate.set()
    for _ in range(50):
        await asyncio.sleep(0)
        if not jm._running:
            break
    assert not jm._running


@pytest.mark.asyncio
async def test_install_wide_job_types_block_every_account(test_db, accounts):
    import backend.job_monitor as jm
    a, b = accounts
    gate = asyncio.Event()

    async def _work():
        await gate.wait()

    with use_account(a):
        jm.launch_background("db_backup", _work)
    with use_account(b):
        with pytest.raises(jm.JobAlreadyRunningError):
            jm.launch_background("db_backup", _work)
        assert jm._get_running_by_job_type("db_backup") is not None
    gate.set()
    for _ in range(50):
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_tracked_run_dedups_per_account(test_db, accounts):
    import backend.job_monitor as jm
    a, b = accounts
    with use_account(b):
        async with jm.tracked_run("email_check"):
            with use_account(a):
                async with jm.tracked_run("email_check"):      # A is not blocked by B
                    assert len(jm.get_all_running()) == 1
            with pytest.raises(jm.JobAlreadyRunningError):
                async with jm.tracked_run("email_check"):
                    pass


def test_monitor_endpoints_show_only_the_callers_runs(client, test_db, accounts):
    import uuid
    import backend.job_monitor as jm
    a, b = accounts
    with use_account(b):
        b_job = make_job(test_db, title="B job")
    a_job = make_job(test_db, title="A job")
    now = datetime.now(timezone.utc)
    for acc, job, key_acc in ((a, a_job, None), (b, b_job, b)):
        jm._running[jm._make_key("analyze_job", str(job.id), key_acc)] = jm.RunningJob(
            run_id=uuid.uuid4(), job_type="analyze_job", trigger="manual", started_at=now,
            scope_key=str(job.id), target_job_id=job.id, account_id=key_acc)
    active = client.get("/api/monitor/active").json()
    assert [r["target_job_id"] for r in active] == [str(a_job.id)]
    flight = client.get("/api/monitor/in-flight").json()
    assert set(flight) == {str(a_job.id)}


# ── stats ────────────────────────────────────────────────────────────────────

def test_timeline_runs_on_sqlite_and_counts_only_the_callers_jobs(client, test_db, accounts):
    a, b = accounts
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    make_job(test_db, status="new", discovered_at=yesterday)
    make_job(test_db, status="saved", discovered_at=yesterday)
    with use_account(b):
        for _ in range(3):
            make_job(test_db, status="applied", discovered_at=yesterday)
    r = client.get("/api/stats/timeline?days=7")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    day = rows[0]
    assert day["date"] == yesterday.date().isoformat()
    assert (day["total"], day["new"], day["saved"], day["applied"]) == (2, 1, 1, 0)


# ── public tracer redirect ───────────────────────────────────────────────────

def test_tracer_redirect_resolves_a_members_token_and_logs_under_that_link(client, test_db, accounts):
    a, b = accounts
    with use_account(b):
        rb = make_resume(test_db, name="B base")
        link = TracerLink(token="btok000001", resume_id=rb.id,
                          destination_url="https://b.example/profile", source_label="LinkedIn")
        test_db.add(link)
        test_db.commit()
        link_id = link.id
    r = client.get("/cv/btok000001", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "https://b.example/profile"
    r = client.get("/?cv=btok000001", follow_redirects=False)
    assert r.status_code == 302
    with unscoped():
        test_db.expire_all()
        assert test_db.query(TracerClickEvent).filter(
            TracerClickEvent.tracer_link_id == link_id).count() == 2
    # the owner's own views still do not show B's link
    assert client.get(f"/api/resumes/{rb.id}/tracer-stats").json() == []


def test_tracer_redirect_unknown_token_is_404(client):
    assert client.get("/cv/nope000000", follow_redirects=False).status_code == 404


# ── Telegram callbacks ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_telegram_callback_acts_as_the_jobs_account(test_db, accounts):
    from backend.notifier.telegram import handle_callback
    a, b = accounts
    with use_account(b):
        job = make_job(test_db, title="B role")
        job_id = job.id
    test_db.expunge_all()
    with use_account(a):                         # the webhook request runs as the owner
        assert await handle_callback(f"applied_{job_id}") == "Marked as applied!"
    with unscoped():
        test_db.expire_all()
        app = test_db.query(Application).filter(Application.job_id == job_id).one()
        assert app.account_id == b
        assert test_db.query(Job).filter(Job.id == job_id).one().status == "applied"


@pytest.mark.asyncio
async def test_telegram_callback_with_a_bad_id_is_unknown(test_db, accounts):
    from backend.notifier.telegram import handle_callback
    with use_account(accounts[0]):
        assert await handle_callback("save_not-a-uuid") in ("Unknown action",)
