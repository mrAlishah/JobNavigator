"""MT-2 · account context and the central query scoping (backend.accounts).

Every test here runs with MT_STRICT=1, so a query on an account-owned model with no
account context raises instead of falling back to the owner.
"""
import uuid

import pytest
from sqlalchemy import delete, func, select, update

from backend.accounts.context import (
    AccountContextMissing, as_owner, current_account, owner_context, unscoped, use_account,
)
from backend.models.db import Account, Application, Job, JobLocation, JobRun, Search
from backend.tests.r4_support import client, make_job  # noqa: F401 (client is a fixture)


@pytest.fixture(autouse=True)
def _strict(monkeypatch):
    monkeypatch.setenv("MT_STRICT", "1")


@pytest.fixture
def accounts(test_db):
    """(owner A, member B) — the test_db fixture already runs as the owner."""
    a = test_db.query(Account).filter(Account.role == "owner").one()
    b = Account(name="Partner", role="member")
    test_db.add(b)
    test_db.commit()
    return a.id, b.id


def _seed_both(db, a, b):
    with use_account(a):
        make_job(db, title="A job")
        db.add(Search(name="A search"))
        db.commit()
    with use_account(b):
        make_job(db, title="B job")
        db.add(Search(name="B search"))
        db.commit()
    db.expunge_all()


def test_rows_of_b_are_invisible_under_a(test_db, accounts):
    a, b = accounts
    _seed_both(test_db, a, b)
    with use_account(a):
        assert [j.title for j in test_db.query(Job).all()] == ["A job"]
        assert test_db.query(func.count(Job.id)).scalar() == 1
        assert [r[0] for r in test_db.query(Job.title).all()] == ["A job"]
        assert test_db.execute(select(Search.name)).scalars().all() == ["A search"]
        assert test_db.query(Job).filter(Job.title == "B job").first() is None
    with use_account(b):
        assert [j.title for j in test_db.query(Job).all()] == ["B job"]


def test_joined_entities_are_scoped_too(test_db, accounts):
    a, b = accounts
    _seed_both(test_db, a, b)
    with use_account(b):
        b_job = test_db.query(Job).one()
        test_db.add(Application(job_id=b_job.id))
        test_db.commit()
    test_db.expunge_all()
    with use_account(a):
        assert test_db.query(Application).join(Job).count() == 0
        assert test_db.query(Job).join(Application, Application.job_id == Job.id).count() == 0


def test_child_table_joined_to_an_owned_table_is_scoped(test_db, accounts):
    a, b = accounts
    with use_account(b):
        job = make_job(test_db, title="B job")
        test_db.add(JobLocation(job_id=job.id, city="berlin", is_primary=True))
        test_db.commit()
    test_db.expunge_all()
    with use_account(a):
        assert test_db.query(JobLocation).join(Job).count() == 0


def test_insert_under_a_stamps_a(test_db, accounts):
    a, b = accounts
    with use_account(a):
        s = Search(name="mine")
        test_db.add(s)
        test_db.commit()
        assert s.account_id == a


def test_an_explicit_account_is_kept(test_db, accounts):
    a, b = accounts
    with use_account(a):
        s = Search(name="for B", account_id=b)
        test_db.add(s)
        test_db.commit()
    with unscoped():
        assert test_db.query(Search).filter(Search.name == "for B").one().account_id == b


def test_bulk_update_and_delete_only_touch_the_current_account(test_db, accounts):
    a, b = accounts
    _seed_both(test_db, a, b)
    with use_account(a):
        test_db.query(Job).update({Job.status: "saved"}, synchronize_session=False)
        test_db.execute(update(Search).values(active=False))
        test_db.execute(delete(Search).where(Search.name.like("%search")))
        test_db.commit()
    test_db.expunge_all()
    with use_account(b):
        assert test_db.query(Job).one().status == "new"
        assert test_db.query(Search).one().name == "B search"
    with use_account(a):
        assert test_db.query(Job).one().status == "saved"
        assert test_db.query(Search).count() == 0


def test_unscoped_sees_both(test_db, accounts):
    a, b = accounts
    _seed_both(test_db, a, b)
    with use_account(a), unscoped():
        assert sorted(j.title for j in test_db.query(Job).all()) == ["A job", "B job"]
        assert test_db.query(Search).count() == 2


def test_unscoped_without_a_context_stamps_the_owner(test_db, accounts):
    a, b = accounts
    with use_account(None), unscoped():
        s = Search(name="system")
        test_db.add(s)
        test_db.commit()
        assert s.account_id == a


def test_query_without_a_context_raises_in_strict_mode(test_db, accounts):
    with use_account(None):
        with pytest.raises(AccountContextMissing):
            test_db.query(Job).all()
        with pytest.raises(AccountContextMissing):
            test_db.add(Search(name="orphan"))
            test_db.flush()
    test_db.rollback()


def test_unowned_tables_need_no_context(test_db, accounts):
    from backend.models.db import Setting
    with use_account(None):
        assert test_db.query(Setting).count() == 0
        assert test_db.query(Account).count() == 2


def test_no_context_falls_back_to_the_owner_when_not_strict(test_db, accounts, monkeypatch):
    a, b = accounts
    _seed_both(test_db, a, b)
    monkeypatch.delenv("MT_STRICT")
    with use_account(None):
        assert current_account.get() is None
        assert [j.title for j in test_db.query(Job).all()] == ["A job"]
        s = Search(name="fallback")
        test_db.add(s)
        test_db.commit()
        assert s.account_id == a


def test_no_owner_and_no_context_is_unscoped_when_not_strict(test_db, accounts, monkeypatch):
    """Before the migration has created an owner nothing is filtered (fresh install boot)."""
    from backend.accounts import context
    a, b = accounts
    _seed_both(test_db, a, b)
    monkeypatch.delenv("MT_STRICT")
    from backend.accounts import scoping
    monkeypatch.setattr(context, "owner_id_for_session", lambda session: None)
    monkeypatch.setattr(scoping, "owner_id_for_session", lambda session: None)
    with use_account(None):
        assert test_db.query(Job).count() == 2


def test_use_account_accepts_strings_and_restores(test_db, accounts):
    a, b = accounts
    with use_account(str(b)):
        assert current_account.get() == b
        with use_account(a):
            assert current_account.get() == a
        assert current_account.get() == b
    assert current_account.get() == a  # test_db's owner context


def test_owner_context_only_fills_an_empty_context(test_db, accounts):
    a, b = accounts
    with use_account(b), owner_context():
        assert current_account.get() == b
    with use_account(None), owner_context():
        assert current_account.get() == a


@pytest.mark.asyncio
async def test_as_owner_runs_a_job_with_no_context_as_the_owner(test_db, accounts):
    a, b = accounts
    seen = []

    @as_owner
    async def job():
        seen.append(current_account.get())
        test_db.add(Search(name="scheduled"))
        test_db.commit()

    with use_account(None):
        await job()
    assert seen == [a]


@pytest.mark.asyncio
async def test_tracked_run_without_a_context_writes_an_owner_row(test_db, accounts):
    import backend.job_monitor as jm
    a, b = accounts
    jm._running.clear()
    with use_account(None):
        async with jm.tracked_run("mt_probe", "scheduler"):
            assert current_account.get() == a
    with use_account(a):
        assert test_db.query(JobRun).filter(JobRun.job_type == "mt_probe").one().account_id == a


@pytest.mark.asyncio
async def test_launch_background_keeps_the_callers_account(test_db, accounts):
    import asyncio
    import backend.job_monitor as jm
    a, b = accounts
    jm._running.clear()
    seen = []

    async def work():
        seen.append(current_account.get())

    with use_account(b):
        jm.launch_background("mt_bg", work)
        task = jm._running[jm._make_key("mt_bg", None, b)].task   # a member's key carries its account (MT-6)
    await asyncio.wait_for(task, 5)
    assert seen == [b]
    with use_account(b):
        assert test_db.query(JobRun).filter(JobRun.job_type == "mt_bg").one().account_id == b


def test_requests_run_as_the_owner(client, test_db, accounts):
    """The middleware sets the owner context; TestClient runs the app in another thread,
    so nothing from the test's own context leaks in."""
    a, b = accounts
    _seed_both(test_db, a, b)
    r = client.get("/api/searches")
    assert r.status_code == 200
    names = {s["name"] for s in r.json()}
    assert "A search" in names and "B search" not in names
    r = client.post("/api/searches", json={"name": "via api", "search_mode": "keyword"})
    assert r.status_code in (200, 201), r.text
    with unscoped():
        assert test_db.query(Search).filter(Search.name == "via api").one().account_id == a


def test_session_get_identity_map_hit_is_not_scoped(test_db, accounts):
    """Known gap, listed in MT-PLAN.md "Unscoped call sites": Session.get() answers from the
    identity map without a query. MT-4..6 replace such calls with filtered queries."""
    a, b = accounts
    with use_account(b):
        job = make_job(test_db, title="B job")
    with use_account(a):
        assert test_db.get(Job, job.id) is job
