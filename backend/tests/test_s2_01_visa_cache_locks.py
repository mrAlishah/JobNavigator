"""S2-01: two ingests of the same new company must never leave a row lock across an await.

The freeze: `resolve_company_h1b` added the new `visa_cache` row in the ingest's own
session and flushed it without committing, then the ingest awaited the next job's H-1B
lookup. A second account's ingest of the same company tried the same INSERT, and that
synchronous call waited on the first transaction on the event loop, so the first
ingest could never resume to commit. The whole backend hung.

SQLite (the suite's DB) has no row locks, so these tests check the structure that
rules the hang out: the cache row is written and committed in a short session of its
own, never through the caller's; no live lookup (the slow await) runs while the
caller's session holds uncommitted writes; and the ingest loops do their lookups
before they write. The `live` test at the end runs the real interleaving against
PostgreSQL in a throwaway database.
"""
import asyncio
import os
import time
import uuid
from datetime import datetime, timezone

import pytest

from backend.models.db import Job, Search, VisaCache, has_uncommitted_writes


def _data(lca=40, median=150000):
    return {"lca_count": lca, "approval_rate": 97.0, "median_salary": median}


@pytest.fixture
def h1b(monkeypatch):
    from backend.analyzer import h1b_checker
    monkeypatch.setattr(h1b_checker, "_budget", h1b_checker._LiveBudget())
    return h1b_checker


# ── the write-tracking flag ──────────────────────────────────────────────────

def test_flag_follows_the_transaction(test_db):
    db = test_db
    assert not has_uncommitted_writes(db)
    db.add(Job(external_id="f1", company="A", title="t", status="new"))
    assert not has_uncommitted_writes(db)          # pending in memory: no lock yet
    db.flush()
    assert has_uncommitted_writes(db)
    with db.begin_nested():
        db.add(Job(external_id="f2", company="A", title="t2", status="new"))
        db.flush()
    assert has_uncommitted_writes(db)               # a savepoint ending does not clear it
    db.commit()
    assert not has_uncommitted_writes(db)

    db.query(Job).filter(Job.external_id == "f1").update({Job.title: "u"})
    assert has_uncommitted_writes(db)               # bulk UPDATE through the session
    db.rollback()
    assert not has_uncommitted_writes(db)


def test_flag_is_false_for_non_sessions():
    from unittest.mock import MagicMock
    assert has_uncommitted_writes(None) is False
    assert has_uncommitted_writes(MagicMock()) is False


# ── resolve_company_h1b never writes through the caller's session ───────────

@pytest.mark.asyncio
async def test_live_result_is_committed_outside_the_callers_session(test_db, h1b, monkeypatch):
    async def fetch(name, h1b_slug=None):
        return _data()
    monkeypatch.setattr(h1b, "fetch_company_h1b_data", fetch)

    out = await h1b.resolve_company_h1b(test_db, "Tether")
    assert out["lca_count"] == 40 and out["has_data"] is True
    # The caller's session took no part in the write: nothing pending, nothing flushed.
    assert not test_db.new and not test_db.dirty
    assert not has_uncommitted_writes(test_db)

    from backend.models.db import SessionLocal
    other = SessionLocal()
    try:
        row = other.query(VisaCache).filter(VisaCache.name_key == "tether").one()
        assert row.lca_count == 40 and row.display_name == "Tether"
    finally:
        other.close()


@pytest.mark.asyncio
async def test_no_live_lookup_while_the_caller_holds_writes(test_db, h1b, monkeypatch):
    calls = []

    async def fetch(name, h1b_slug=None):
        calls.append(name)
        return _data()
    monkeypatch.setattr(h1b, "fetch_company_h1b_data", fetch)

    test_db.add(Job(external_id="w1", company="Blockstream", title="t", status="new"))
    test_db.flush()
    assert await h1b.resolve_company_h1b(test_db, "Blockstream") is None
    assert calls == []                               # the await never happened

    test_db.commit()
    out = await h1b.resolve_company_h1b(test_db, "Blockstream")
    assert calls == ["Blockstream"] and out["lca_count"] == 40


# ── the upsert ───────────────────────────────────────────────────────────────

def test_upsert_twice_same_key_one_row_no_error(test_db, h1b):
    first = h1b._store_h1b("acme", "Acme", "acme-inc", _data(lca=12))
    second = h1b._store_h1b("acme", "ACME", None, _data(lca=30, median=99000))
    assert first["lca_count"] == 12 and second["lca_count"] == 30
    rows = test_db.query(VisaCache).filter(VisaCache.name_key == "acme").all()
    assert len(rows) == 1
    test_db.refresh(rows[0])
    assert rows[0].slug == "acme-inc"               # a missing slug keeps the stored one
    assert rows[0].display_name == "ACME" and rows[0].median_salary == 99000


def test_upsert_zero_result_keeps_good_metrics(test_db, h1b):
    h1b._store_h1b("acme", "Acme", None, _data(lca=55))
    out = h1b._store_h1b("acme", "Acme", None, _data(lca=0, median=0))
    assert out["lca_count"] == 55 and out["has_data"] is True
    row = test_db.query(VisaCache).filter(VisaCache.name_key == "acme").one()
    test_db.refresh(row)
    assert row.fetched_at is not None and row.lca_count == 55


def test_upsert_zero_result_on_a_zero_row_is_stored(test_db, h1b):
    h1b._store_h1b("nobody", "Nobody", None, _data(lca=0, median=0))
    out = h1b._store_h1b("nobody", "Nobody", None, _data(lca=0, median=0))
    assert out == {"lca_count": 0, "approval_rate": 97.0, "median_salary": 0, "has_data": False}


def test_upsert_compiles_for_postgres():
    from sqlalchemy.dialects import postgresql
    from backend.analyzer import h1b_checker
    stmt = h1b_checker._upsert_statement("postgresql", "acme", "Acme", None, _data(lca=0, median=0))
    sql = str(stmt.compile(dialect=postgresql.dialect())).lower()
    assert "on conflict (name_key, country) do update" in sql
    assert "case when" in sql and "excluded.lca_count" in sql
    assert "slug" not in sql.split("do update", 1)[1]   # slug untouched when not given


# ── two ingests of the same new company, interleaved on one loop ────────────

@pytest.mark.asyncio
async def test_two_interleaved_ingests_of_the_same_new_company(test_db, h1b, monkeypatch):
    """The S2-01 shape: both ingests reach the same new company together, both lookups
    are in flight at once, and each ingest writes rows between its lookups. No session
    may hold an uncommitted write while a lookup is suspended, both store the company
    without an error, and the cache ends with one row."""
    from backend.analyzer.h1b_checker import check_job_h1b
    from backend.models.db import SessionLocal

    sessions = {}
    seen_while_suspended = []
    both_in_flight = asyncio.Event()
    in_flight = []

    async def fetch(name, h1b_slug=None):
        in_flight.append(name)
        if len(in_flight) >= 2:
            both_in_flight.set()
        seen_while_suspended.append({k: has_uncommitted_writes(s) for k, s in sessions.items()})
        await asyncio.wait_for(both_in_flight.wait(), 5)
        return _data(lca=15, median=105000)
    monkeypatch.setattr(h1b, "fetch_company_h1b_data", fetch)

    async def ingest(tag):
        db = SessionLocal()
        sessions[tag] = db
        try:
            made = []
            for i, company in enumerate(["Tether", "Tether", "Mozilla"]):
                job = Job(external_id=f"{tag}-{i}", company=company, title=f"Role {i}", status="new")
                await check_job_h1b(job, db, company_lookup={}, phrases=[])
                db.add(job)
                db.flush()
                made.append((company, job.h1b_company_lca_count))
            db.commit()
            return made
        finally:
            db.close()

    a, b = await asyncio.gather(ingest("a"), ingest("b"))

    assert in_flight[:2] == ["Tether", "Tether"]      # truly interleaved: both looked it up
    assert all(not any(flags.values()) for flags in seen_while_suspended), seen_while_suspended
    assert a[0] == ("Tether", 15) and b[0] == ("Tether", 15)
    assert a[1] == ("Tether", 15) and b[1] == ("Tether", 15)   # second job: cache hit
    # Mozilla came after each ingest had written rows: no live lookup then (cache-only).
    assert "Mozilla" not in in_flight
    assert test_db.query(VisaCache).filter(VisaCache.name_key == "tether").count() == 1


@pytest.mark.asyncio
@pytest.mark.h1b_prefetch
async def test_prefetch_fills_each_new_company_once_before_any_write(test_db, h1b, monkeypatch):
    from backend.scraper._shared.analysis import prefetch_h1b
    from backend.models.db import Company
    test_db.add(Company(name="Mozilla", h1b_slug="mozilla-corp"))
    test_db.add(VisaCache(name_key="supabase", country="US", lca_count=3, has_data=True,
                          fetched_at=datetime.now(timezone.utc)))
    test_db.commit()
    calls = []

    async def fetch(name, h1b_slug=None):
        calls.append((name, h1b_slug))
        return _data()
    monkeypatch.setattr(h1b, "fetch_company_h1b_data", fetch)

    await prefetch_h1b(["Tether", "tether ", "Mozilla", "Supabase", None, ""])
    assert calls == [("Tether", None), ("Mozilla", "mozilla-corp")]   # fresh Supabase skipped
    assert {r.name_key for r in test_db.query(VisaCache).all()} == {"tether", "mozilla", "supabase"}


@pytest.mark.asyncio
@pytest.mark.h1b_prefetch
async def test_trulyremote_ingest_looks_up_before_it_writes(test_db, h1b, monkeypatch):
    """A real source end to end: every live lookup happens while the ingest has written
    nothing, and the stored jobs still carry the company data."""
    from backend.scraper.sources import trulyremote
    from backend.models.db import SessionLocal

    search = Search(name="s201", search_mode="trulyremote", results_wanted=10,
                    title_exclude_keywords=[], company_exclude=[])
    test_db.add(search)
    test_db.commit()

    roster = [
        {"title": f"Engineer {i}", "company": c, "url": f"https://trw/{i}", "location": "Worldwide",
         "description": "Great role", "employment_type": "Full-time", "posted": "2026-10-01",
         "salary_min": None, "salary_max": None, "salary_text": "", "salary_usd_annual": False}
        for i, c in enumerate(["Tether", "Blockstream", "Tether"])
    ]

    async def fake_roster(client):
        return roster

    async def fake_details(client, candidates, **kw):
        return {j["url"]: dict(j) for j in candidates}
    monkeypatch.setattr(trulyremote, "_fetch_roster", fake_roster)
    monkeypatch.setattr(trulyremote, "_fetch_details", fake_details)
    monkeypatch.setattr("backend.activity.log_activity", lambda *a, **k: None)

    rows_at_lookup = []

    async def fetch(name, h1b_slug=None):
        probe = SessionLocal()
        try:
            rows_at_lookup.append(probe.query(Job).count())
        finally:
            probe.close()
        return _data(lca=60)
    monkeypatch.setattr(h1b, "fetch_company_h1b_data", fetch)

    res = await trulyremote.run(search)
    assert res["error"] is None and res["new_jobs"] == 3
    assert rows_at_lookup == [0, 0]                  # Tether and Blockstream, once each
    test_db.expire_all()
    jobs = test_db.query(Job).all()
    assert len(jobs) == 3 and all(j.h1b_company_lca_count == 60 for j in jobs)
    assert {j.h1b_verdict for j in jobs} == {"likely"}


# ── PostgreSQL: the real interleaving, in a throwaway database ─────────────

@pytest.mark.live
@pytest.mark.asyncio
async def test_postgres_interleaved_ingests_do_not_block(monkeypatch):
    """Two sessions on two real connections, both in a transaction that has written a
    row, both resolving the same new company with their lookups in flight at once. On
    the old code the second INSERT waited on the first's uncommitted row and the
    process hung; here both finish, with one cache row. The throwaway engine carries
    the app's lock_timeout, so a regression fails in seconds instead of hanging.

    Skips unless DATABASE_URL is PostgreSQL. It creates and drops its own database;
    nothing in the app's database is read or written."""
    url = os.getenv("DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("no Postgres DATABASE_URL")
    from sqlalchemy import create_engine, text, Column, Integer, String, MetaData, Table
    from sqlalchemy.engine import make_url
    from sqlalchemy.orm import sessionmaker
    from backend.analyzer import h1b_checker
    from backend.models import db as db_mod

    name = f"jn_s201_{uuid.uuid4().hex[:10]}"
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))
    engine = create_engine(make_url(url).set(database=name),
                           connect_args={"options": f"-c lock_timeout={db_mod.DB_LOCK_TIMEOUT}"})
    try:
        VisaCache.__table__.create(engine)
        md = MetaData()
        rows = Table("ingest_rows", md, Column("id", Integer, primary_key=True),
                     Column("k", String, unique=True))
        md.create_all(engine)
        Session = sessionmaker(bind=engine, autoflush=False)
        monkeypatch.setattr(h1b_checker, "SessionLocal", Session)
        monkeypatch.setattr(h1b_checker, "_budget", h1b_checker._LiveBudget())

        gate = asyncio.Event()
        in_flight = []

        async def fetch(company, h1b_slug=None):
            in_flight.append(company)
            if len(in_flight) >= 2:
                gate.set()
            await asyncio.wait_for(gate.wait(), 10)
            return _data(lca=15)
        monkeypatch.setattr(h1b_checker, "fetch_company_h1b_data", fetch)

        async def ingest(tag):
            s = Session()
            try:
                out = await h1b_checker.resolve_company_h1b(s, "Tether")
                s.execute(rows.insert().values(k=f"{tag}-1"))   # the ingest's own write
                # The ingest goes on to its next job and awaits, still in its transaction.
                # On the old code this is where it held the uncommitted visa_cache row
                # while the other ingest's INSERT of the same key blocked the loop.
                await asyncio.sleep(0.2)
                # The second job of the same company: a cache hit, no wait on anyone.
                again = await h1b_checker.resolve_company_h1b(s, "Tether")
                s.commit()
                return out, again
            finally:
                s.close()

        # The old code's symptom is a stopped event loop, so measure that directly. (With
        # lock_timeout the old code's wait ends after DB_LOCK_TIMEOUT and it swallowed the
        # error, so only the stall gives it away; without the timeout it never returned.)
        worst, done = [0.0], asyncio.Event()

        async def heartbeat():
            last = time.monotonic()
            while not done.is_set():
                await asyncio.sleep(0.05)
                now = time.monotonic()
                worst[0] = max(worst[0], now - last)
                last = now

        hb = asyncio.create_task(heartbeat())
        (a1, a2), (b1, b2) = await asyncio.wait_for(asyncio.gather(ingest("a"), ingest("b")), 30)
        done.set()
        await hb
        assert worst[0] < 1.0, f"the event loop stopped for {worst[0]:.1f}s"
        assert in_flight == ["Tether", "Tether"]
        assert a1["lca_count"] == b1["lca_count"] == a2["lca_count"] == b2["lca_count"] == 15
        with engine.connect() as c:
            assert c.execute(text("select count(*) from visa_cache where name_key='tether'")).scalar() == 1
            assert c.execute(text("show lock_timeout")).scalar() == db_mod.DB_LOCK_TIMEOUT
    finally:
        engine.dispose()
        with admin.connect() as c:
            c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.mark.live
def test_app_engine_connections_carry_lock_timeout():
    """The app's own engine: 10s on every connection, 0 inside the startup exemption,
    back to 10s after it. Read-only (SHOW)."""
    from backend.models import db as db_mod
    if db_mod.engine.dialect.name != "postgresql":
        pytest.skip("no Postgres engine")
    from sqlalchemy import text
    with db_mod.engine.connect() as c:
        assert c.execute(text("show lock_timeout")).scalar() == db_mod.DB_LOCK_TIMEOUT
    with db_mod.lock_timeout_exempt():
        with db_mod.engine.connect() as c:
            assert c.execute(text("show lock_timeout")).scalar() == "0"
    with db_mod.engine.connect() as c:
        assert c.execute(text("show lock_timeout")).scalar() == db_mod.DB_LOCK_TIMEOUT
