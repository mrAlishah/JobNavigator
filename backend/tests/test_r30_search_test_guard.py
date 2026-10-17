"""R30 S-07: one background Test per search and account. An Apify Test is a paid run, so a
second Test of the same search (a double click, another tab, a script) answers 409 with the
running Test's run_id, an Apify search's Run waits for its Test, and its Test for its Run."""
import asyncio
import json

import pytest

from backend.accounts.context import use_account
from backend.models.db import Account, Search
from backend.scraper.sources import apify


@pytest.fixture
def accounts(test_db):
    a = test_db.query(Account).filter(Account.role == "owner").one()
    b = Account(name="Partner", role="member")
    test_db.add(b)
    test_db.commit()
    return a.id, b.id


def _search(db, name="t", **kw):
    s = Search(active=True, name=name, search_mode="apify", sources=["apify_indeed"],
               title_exclude_keywords=[], company_exclude=[], auto_scoring_depth="off", **kw)
    db.add(s)
    db.commit()
    return s


async def _settle(rs):
    """Let the background Test tasks finish (at most about five seconds)."""
    for _ in range(500):
        if not rs._tests_in_flight:
            return
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_a_second_test_of_the_same_search_is_409_with_the_running_run_id(test_db, monkeypatch):
    from backend.api import routes_searches as rs
    started, release = [], asyncio.Event()

    async def slow_preview(search, db):
        started.append(search.id)
        await release.wait()
        return {"jobs": []}

    monkeypatch.setattr(apify, "preview", slow_preview)
    s = _search(test_db, search_term="engineer")
    other = _search(test_db, name="other", search_term="pm")

    first = await rs.test_search(str(s.id), db=test_db)
    assert first.status_code == 202
    run_id = json.loads(first.body)["run_id"]

    again = await rs.test_search(str(s.id), db=test_db)
    assert again.status_code == 409
    body = json.loads(again.body)
    assert (body["detail"], body["run_id"]) == ("test_running", run_id)

    # A run of the same search would buy the same results again while the paid Test runs.
    run = await rs.trigger_search(str(s.id), db=test_db)
    assert run.status_code == 409 and json.loads(run.body)["detail"] == "test_running"

    # Another search is not blocked.
    assert (await rs.test_search(str(other.id), db=test_db)).status_code == 202

    await asyncio.sleep(0)
    release.set()
    await _settle(rs)
    assert rs._tests_in_flight == {}
    assert rs._test_results[run_id]["status"] == "done"
    assert len(started) == 2, "one paid Test per search"

    third = await rs.test_search(str(s.id), db=test_db)
    assert third.status_code == 202, "a finished Test frees the search"
    await _settle(rs)


@pytest.mark.asyncio
async def test_a_failed_test_frees_the_search_too(test_db, monkeypatch):
    from backend.api import routes_searches as rs

    async def broken_preview(search, db):
        raise RuntimeError("boom")

    monkeypatch.setattr(apify, "preview", broken_preview)
    s = _search(test_db, search_term="engineer")
    first = await rs.test_search(str(s.id), db=test_db)
    await _settle(rs)
    assert rs._tests_in_flight == {}
    assert rs._test_results[json.loads(first.body)["run_id"]]["result"] == {"error": "boom"}


@pytest.mark.asyncio
async def test_an_apify_test_waits_for_a_run_of_the_same_search(test_db, monkeypatch):
    from backend import job_monitor
    from backend.api import routes_searches as rs
    s = _search(test_db, search_term="engineer")
    monkeypatch.setattr(job_monitor, "is_running",
                        lambda job_type, scope_key=None: job_type == "search_run" and scope_key == str(s.id))
    r = await rs.test_search(str(s.id), db=test_db)
    assert r.status_code == 409 and json.loads(r.body)["detail"] == "search_running"
    assert rs._tests_in_flight == {}


def test_the_test_guard_is_per_account(test_db, accounts):
    from backend.api import routes_searches as rs
    a, b = accounts
    owner_key = rs._test_flight_key("s1")
    with use_account(b):
        member_key = rs._test_flight_key("s1")
    assert owner_key == "search_test:s1" and member_key == f"search_test:s1@{b}"
