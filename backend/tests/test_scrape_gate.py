"""The scrape gate (`scrape_max_concurrent`): scrapes run one after another, a run that
waits for its turn says so in /monitor/active, and its clock starts when it gets the slot."""
import asyncio
from datetime import timezone

import pytest
from sqlalchemy import event
from sqlalchemy.orm import sessionmaker


@pytest.fixture(autouse=True)
def _monitor(test_db, monkeypatch):
    import backend.job_monitor as jm
    from backend.models.db import JobRun

    def _on_load(instance, context):
        for field in ("started_at", "finished_at"):
            v = getattr(instance, field, None)
            if v is not None and v.tzinfo is None:
                setattr(instance, field, v.replace(tzinfo=timezone.utc))

    event.listen(JobRun, "load", _on_load)
    monkeypatch.setattr(jm, "SessionLocal", sessionmaker(bind=test_db.get_bind()))
    jm._running.clear()
    jm.reset_limiter()
    yield jm
    jm._running.clear()
    jm.reset_limiter()
    event.remove(JobRun, "load", _on_load)


async def _drain(jm, keys, rounds=200):
    for _ in range(rounds):
        if not any(k in jm._running for k in keys):
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"runs still going: {[k for k in keys if k in jm._running]}")


def test_every_scrape_job_type_takes_the_scraping_gate(_monitor):
    jm = _monitor
    for job_type in ("scrape_all", "company_scrape", "search_run"):
        assert jm.limiter_for_job_type(job_type) is jm.get_limiter("scraping")


def test_scraping_limit_defaults_to_one_and_is_capped_at_two(_monitor, test_db, monkeypatch):
    jm = _monitor
    assert jm.get_limiter("scraping").value == 1
    import backend.job_monitor as mod
    monkeypatch.setattr(mod, "get_setting", lambda db, key, *a: "7" if key == "scrape_max_concurrent" else None)
    jm.reset_limiter("scraping")
    assert jm.get_limiter("scraping").value == 2


@pytest.mark.asyncio
async def test_two_manual_scrapes_run_one_after_the_other(_monitor):
    jm = _monitor
    order, release = [], asyncio.Event()

    async def first():
        order.append("first start")
        await release.wait()
        order.append("first end")

    async def second():
        order.append("second start")

    jm.launch_background("company_scrape", first, scope_key="c1")
    jm.launch_background("search_run", second, scope_key="s1")
    await asyncio.sleep(0.05)
    assert order == ["first start"]                     # the second waits, it does not fail
    active = {r["job_type"]: r for r in jm.get_all_running()}
    assert active["company_scrape"]["waiting"] is False
    assert active["search_run"]["waiting"] is True
    release.set()
    await _drain(jm, ["company_scrape:c1", "search_run:s1"])
    assert order == ["first start", "first end", "second start"]


@pytest.mark.asyncio
async def test_a_waiting_run_starts_its_clock_when_it_gets_the_slot(_monitor, test_db):
    jm = _monitor
    from backend.models.db import JobRun
    release = asyncio.Event()

    async def first():
        await release.wait()

    async def second():
        return "done"

    jm.launch_background("company_scrape", first, scope_key="c1")
    jm.launch_background("company_scrape", second, scope_key="c2")
    await asyncio.sleep(0.05)
    queued_at = jm._running["company_scrape:c2"].started_at
    await asyncio.sleep(0.3)
    release.set()
    await _drain(jm, ["company_scrape:c1", "company_scrape:c2"])
    s = sessionmaker(bind=test_db.get_bind())()
    rows = {r.result_summary: r for r in s.query(JobRun).filter_by(job_type="company_scrape").all()}
    s.close()
    assert rows["done"].started_at > queued_at          # the queue time is not run time
    assert rows["done"].duration_seconds < 0.25


@pytest.mark.asyncio
async def test_scheduled_scrape_waits_for_a_manual_one(_monitor):
    jm = _monitor
    order, release = [], asyncio.Event()

    async def manual():
        order.append("manual start")
        await release.wait()
        order.append("manual end")

    async def scheduled():
        async with jm.tracked_run("scrape_all", "scheduler", scope_key=jm.SCHEDULED_SCRAPE_SCOPE):
            order.append("scheduled start")

    jm.launch_background("scrape_all", manual)
    await asyncio.sleep(0.02)
    task = asyncio.create_task(scheduled())
    await asyncio.sleep(0.05)
    assert order == ["manual start"]
    waiting = [r for r in jm.get_all_running() if r["trigger"] == "scheduler"]
    assert waiting and waiting[0]["waiting"] is True
    release.set()
    await asyncio.wait_for(task, 2)
    assert order == ["manual start", "manual end", "scheduled start"]


@pytest.mark.asyncio
async def test_other_job_types_are_not_gated_by_scraping(_monitor):
    jm = _monitor
    release, ran = asyncio.Event(), asyncio.Event()

    async def scrape():
        await release.wait()

    async def backup():
        ran.set()

    jm.launch_background("company_scrape", scrape, scope_key="c1")
    jm.launch_background("db_backup", backup)
    await asyncio.wait_for(ran.wait(), 1)
    release.set()
    await _drain(jm, ["company_scrape:c1", "db_backup"])
