"""QA F4 · scheduler clean context and strict scoping hardened.

R1-01  scheduled jobs never inherit the context of the request that reconfigured the
       scheduler (real AsyncIOScheduler, ported from T4's proof, inverted), and every
       scheduled entry point resets account / unscoped / owner unlock itself.
R1-02  strict mode raises for any statement that reaches an account-owned table with no
       context (count(), exists(), unions, joins from unowned entities, Core tables).
R1-03  a failed owner lookup fails closed; no NULL account rows while accounts exist.
R1-04  INSERTs on account-owned tables through the Session are stamped (or refused).
R1-06  acting_as(None) clears unscoped.  R1-07 install-wide runs are the owner's.
R1-08  the owner unlock does not ride into background tasks.  R1-09 fixture
       `no_account_ctx` runs entry points with no context at all.
"""
import asyncio
import contextvars
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import exists, func, insert, select, update

from backend.accounts.context import (
    AccountContextMissing, OwnerLookupFailed, current_account, is_owner_unlocked,
    is_unscoped, owner_unlocked, unscoped, use_account,
)
from backend.models.db import Account, Job, JobLocation, JobRun, Setting
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


def _ctx():
    return (current_account.get(), is_unscoped(), is_owner_unlocked())


def _poisoned(member):
    """What routes_settings._reconfigure runs in for a member with the owner unlock."""
    from contextlib import ExitStack
    st = ExitStack()
    st.enter_context(use_account(member))
    st.enter_context(owner_unlocked(True))
    st.enter_context(unscoped())
    return st


def _runs(test_db, job_type):
    with unscoped():
        test_db.expire_all()
        return test_db.query(JobRun).filter(JobRun.job_type == job_type).all()


# ── R1-01: registration from a clean context (real APScheduler) ──────────────

@pytest.mark.asyncio
async def test_configure_scheduler_from_a_member_request_thread_fires_clean(
        test_db, accounts, monkeypatch):
    """Ported from T4 `test_PROVE_R1_01_*`: configure_scheduler() runs in a worker thread
    under the member + unscoped + unlock context (as a settings PATCH does). The job it
    registers must fire with no account context."""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.interval import IntervalTrigger
    import backend.scheduler as sched_mod
    a, b = accounts
    set_setting(test_db, "scrape_interval_minutes", "5")
    set_setting(test_db, "email_check_interval_minutes", "0")

    seen = []
    fired = asyncio.Event()

    async def _probe():                       # the raw job: no scheduled_job wrapper
        seen.append(_ctx())
        fired.set()
    monkeypatch.setattr(sched_mod, "run_all_scrapes", _probe)
    monkeypatch.setattr(sched_mod, "IntervalTrigger",
                        lambda minutes: IntervalTrigger(seconds=0.2))
    real = AsyncIOScheduler()
    monkeypatch.setattr(sched_mod, "scheduler", real)
    contextvars.Context().run(real.start)     # boot: clean, as the lifespan does
    try:
        def _patch_settings_thread():
            with _poisoned(b):
                sched_mod.configure_scheduler()
        await asyncio.to_thread(_patch_settings_thread)
        await asyncio.wait_for(fired.wait(), 5)
    finally:
        real.shutdown(wait=False)
    assert seen[0] == (None, False, False)


@pytest.mark.asyncio
async def test_a_job_added_from_a_poisoned_context_still_runs_per_account_clean(
        test_db, accounts, monkeypatch):
    """Second layer: even when add_job() itself is called from the poisoned context,
    the entry point (`scheduled_job`) resets it before `for_each_account`."""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    import backend.notifier.telegram as tg
    import backend.scheduler as sched_mod
    a, b = accounts
    seen = []
    done = asyncio.Event()

    async def _digest():
        seen.append(_ctx())
        if len(seen) == 2:
            done.set()
    monkeypatch.setattr(tg, "send_digest", _digest)
    real = AsyncIOScheduler()
    contextvars.Context().run(real.start)
    try:
        with _poisoned(b):
            real.add_job(sched_mod.send_daily_digest, "date",
                         run_date=datetime.now(timezone.utc) + timedelta(seconds=0.2))
        await asyncio.wait_for(done.wait(), 5)
    finally:
        real.shutdown(wait=False)
    assert seen == [(a, False, False), (b, False, False)]


INSTALL_LEVEL = ["run_db_backup", "refresh_h1b_data", "run_all_scrapes"]


def _stub_bodies(monkeypatch, record):
    import backend.analyzer.cv_scorer as scorer
    import backend.analyzer.h1b_checker as h1b
    import backend.db_backup as db_backup
    import backend.email_monitor.gmail_client as gc
    import backend.notifier.telegram as tg
    import backend.scraper.orchestrator as orch

    async def _rec(*_a, **_k):
        record.append(_ctx())
        return {}
    monkeypatch.setattr(orch, "run_all", _rec)
    monkeypatch.setattr(scorer, "analyze_unscored_jobs", _rec)
    monkeypatch.setattr(h1b, "refresh_all_h1b", _rec)
    monkeypatch.setattr(gc, "check_emails", _rec)
    monkeypatch.setattr(tg, "send_digest", _rec)

    def _dump():
        record.append(_ctx())
        return {"path": "/x", "file": "x.sql", "size_mb": 1.0, "pruned": 0}
    monkeypatch.setattr(db_backup, "run_pg_dump", _dump)


@pytest.mark.asyncio
@pytest.mark.parametrize("fn_name,job_type", [
    ("run_db_backup", "db_backup"), ("refresh_h1b_data", "h1b_refresh"),
    ("run_all_scrapes", "scrape_all"), ("send_daily_digest", "daily_digest"),
    ("run_email_check", "email_check"), ("run_auto_reject", "auto_reject"),
    ("run_job_cleanup_auto", "job_cleanup"),
])
@pytest.mark.parametrize("caller", ["none", "member_poisoned"])
async def test_every_scheduled_entry_point_sets_its_own_context(
        no_account_ctx, accounts, monkeypatch, fn_name, job_type, caller):
    """R1-09: with NO context (strict mode raises on any unscoped query) and with the
    R1-01 poisoned context, each entry point runs its body as the owner / per account,
    never unscoped or unlocked, and install-level runs are the owner's."""
    import backend.scheduler as sched_mod
    a, b = accounts
    record = []
    _stub_bodies(monkeypatch, record)
    fn = getattr(sched_mod, fn_name)
    if caller == "none":
        assert _ctx() == (None, False, False)
        await fn()
    else:
        with _poisoned(b):
            await fn()
    assert all(u is False and l is False for _acc, u, l in record), record
    runs = _runs(no_account_ctx, job_type)
    assert runs, f"{fn_name} wrote no JobRun"
    owners = {r.account_id for r in runs}
    if fn_name in INSTALL_LEVEL:
        assert owners == {a}
        assert all(acc == a for acc, _u, _l in record[:1])
    elif fn_name == "run_email_check":
        assert owners == {a}          # only the Gmail-connected account (the owner)
    else:
        assert owners == {a, b}
    assert all(r.status == "completed" for r in runs), [(r.status, r.error) for r in runs]


# ── R1-07: install-wide run records are the owner's ──────────────────────────

@pytest.mark.asyncio
async def test_member_with_unlock_triggering_a_backup_records_it_as_the_owners(
        test_db, accounts, monkeypatch):
    import backend.job_monitor as jm
    a, b = accounts
    done = asyncio.Event()

    async def _backup():
        done.set()
        return "ok"
    with use_account(b), owner_unlocked(True):
        jm.launch_background("db_backup", _backup)
    await asyncio.wait_for(done.wait(), 5)
    await asyncio.sleep(0.05)
    runs = _runs(test_db, "db_backup")
    assert [(r.account_id, r.status) for r in runs] == [(a, "completed")]


# ── R1-08: the owner unlock does not ride into background tasks ──────────────

@pytest.mark.asyncio
async def test_launch_background_drops_the_owner_unlock(test_db, accounts):
    import backend.job_monitor as jm
    a, b = accounts
    seen = []
    done = asyncio.Event()

    async def _task():
        seen.append(_ctx())
        done.set()
    with use_account(b), owner_unlocked(True):
        jm.launch_background("tailor_resume", _task, scope_key="x")
    await asyncio.wait_for(done.wait(), 5)
    assert seen == [(b, False, False)]


# ── R1-06: acting_as(None) ───────────────────────────────────────────────────

def test_acting_as_none_clears_unscoped_and_acts_as_the_owner(test_db, accounts):
    from backend.accounts.background import acting_as
    a, b = accounts
    make_job(test_db, title="A job")
    with use_account(b):
        make_job(test_db, title="B job")
    with use_account(b), unscoped():
        with acting_as(None):
            assert _ctx()[:2] == (a, False)
            assert [j.title for j in test_db.query(Job).all()] == ["A job"]


# ── R1-02 / R1-09: strict mode with no context ───────────────────────────────

def _seed(test_db, accounts):
    a, b = accounts
    ja = make_job(test_db, title="A job")
    test_db.add(JobLocation(job_id=ja.id, country="US", is_primary=True))
    with use_account(b):
        jb = make_job(test_db, title="B job")
        test_db.add(JobLocation(job_id=jb.id, country="DE", is_primary=True))
    test_db.commit()
    return ja.id, jb.id


SHAPES = {
    "query_count": lambda s, jid: s.query(Job).count(),
    "func_count": lambda s, jid: s.execute(select(func.count()).select_from(Job)).scalar(),
    "exists": lambda s, jid: s.query(exists().where(Job.id == jid)).scalar(),
    "unowned_join": lambda s, jid: s.query(JobLocation).join(Job, JobLocation.job_id == Job.id).all(),
    "union": lambda s, jid: s.execute(select(Job.id).union(select(Job.id))).all(),
    "in_subquery": lambda s, jid: s.query(JobLocation).filter(
        JobLocation.job_id.in_(select(Job.id))).all(),
    "correlated_exists": lambda s, jid: s.query(JobLocation).filter(
        exists().where(Job.id == JobLocation.job_id)).all(),
    "core_select": lambda s, jid: s.execute(select(Job.__table__.c.id)).all(),
    "core_update": lambda s, jid: s.execute(
        update(Job.__table__).values(status="x")),
}


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_strict_mode_raises_for_every_shape_without_a_context(test_db, accounts, shape):
    ja, _jb = _seed(test_db, accounts)
    with use_account(None):
        with pytest.raises(AccountContextMissing):
            SHAPES[shape](test_db, ja)
    test_db.rollback()


def test_strict_mode_still_allows_unowned_tables_without_a_context(test_db, accounts):
    _seed(test_db, accounts)
    with use_account(None):
        test_db.query(Setting).all()
        test_db.query(Account).count()
        assert test_db.query(JobLocation).count() == 2   # child table alone: unowned


@pytest.mark.parametrize("shape", ["query_count", "func_count", "union"])
def test_shapes_are_scoped_under_a_context(test_db, accounts, shape):
    ja, jb = _seed(test_db, accounts)
    a, b = accounts
    with use_account(b):
        out = SHAPES[shape](test_db, jb)
    n = out if isinstance(out, int) else len(out)
    assert n == 1


# ── R1-04: INSERT stamping ───────────────────────────────────────────────────

def _job_row(**kw):
    d = {"external_id": kw.pop("external_id"), "company": "Acme", "title": "T",
         "url": "https://x/1", "source": "test", "status": "new"}
    d.update(kw)
    return d


def _account_of(test_db, external_id):
    with unscoped():
        test_db.expire_all()
        return test_db.query(Job.account_id).filter(Job.external_id == external_id).scalar()


def test_orm_insert_values_is_stamped(test_db, accounts):
    a, b = accounts
    with use_account(b):
        test_db.execute(insert(Job).values(**_job_row(external_id="ins-1")))
        test_db.commit()
    assert _account_of(test_db, "ins-1") == b


def test_orm_bulk_insert_with_params_is_stamped(test_db, accounts):
    a, b = accounts
    with use_account(b):
        test_db.execute(insert(Job), [_job_row(external_id="bulk-1"),
                                      _job_row(external_id="bulk-2", url="https://x/2")])
        test_db.commit()
    assert _account_of(test_db, "bulk-1") == b and _account_of(test_db, "bulk-2") == b


def test_core_table_insert_through_the_session_is_stamped(test_db, accounts):
    import uuid
    a, b = accounts
    with use_account(b):
        test_db.execute(insert(Job.__table__).values(id=uuid.uuid4(), **_job_row(external_id="core-1")))
        test_db.commit()
    assert _account_of(test_db, "core-1") == b


def test_unscoped_insert_is_stamped_as_the_owner(test_db, accounts):
    a, b = accounts
    with use_account(None), unscoped():
        test_db.execute(insert(Job).values(**_job_row(external_id="sys-1")))
        test_db.commit()
    assert _account_of(test_db, "sys-1") == a


def test_insert_without_a_context_raises_in_strict_mode(test_db, accounts):
    with use_account(None):
        with pytest.raises(AccountContextMissing):
            test_db.execute(insert(Job).values(**_job_row(external_id="none-1")))
    test_db.rollback()


def test_no_bulk_save_helpers_in_backend():
    """bulk_insert_mappings / bulk_save_objects fire no event, so they could write NULL
    account rows; none may be used in app code."""
    root = Path(__file__).resolve().parents[1]
    hits = []
    for p in root.rglob("*.py"):
        if "tests" in p.parts:
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        if re.search(r"\.bulk_(insert|save|update)_(mappings|objects)\(", text):
            hits.append(str(p))
    assert hits == []


# ── R1-03: fail closed ───────────────────────────────────────────────────────

class _Bind:
    pass


class _BrokenSession:
    def __init__(self, exc):
        self._exc, self._bind = exc, _Bind()

    def get_bind(self):
        return self._bind

    def connection(self):
        exc = self._exc

        class _C:
            def execute(self, *_a, **_k):
                raise exc
        return _C()


def test_owner_lookup_error_raises():
    from backend.accounts.context import owner_id_for_session
    with pytest.raises(OwnerLookupFailed):
        owner_id_for_session(_BrokenSession(RuntimeError("current transaction is aborted")))


def test_owner_lookup_without_an_accounts_table_is_none():
    from backend.accounts.context import owner_id_for_session
    assert owner_id_for_session(_BrokenSession(RuntimeError("no such table: accounts"))) is None


def test_owner_context_raises_when_the_lookup_fails(test_db, monkeypatch):
    from backend.accounts import context
    from backend.accounts.context import owner_context

    def _boom():
        raise OwnerLookupFailed("db down")
    monkeypatch.setattr(context, "owner_account_id", _boom)
    with use_account(None):
        with pytest.raises(OwnerLookupFailed):
            with owner_context():
                pass


def test_no_null_account_row_while_accounts_exist(test_db, accounts, monkeypatch):
    """Non-strict, no context, owner unresolvable: the flush refuses instead of writing
    a NULL account row."""
    from backend.accounts import context, scoping
    monkeypatch.delenv("MT_STRICT")
    monkeypatch.setattr(context, "owner_id_for_session", lambda session: None)
    monkeypatch.setattr(scoping, "owner_id_for_session", lambda session: None)
    with use_account(None):
        test_db.add(Job(**_job_row(external_id="null-1")))
        with pytest.raises(AccountContextMissing):
            test_db.flush()
    test_db.rollback()
