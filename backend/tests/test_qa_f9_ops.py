"""QA F9 · account indexes, restorable backups, pruning and ops switches.

T6-01 composite indexes, T6-02 per-request accounts cache, T9-01/T9-02 restorable dumps,
T3-04 pre_remove pruning, T2-01 JN_SCHEDULER_DISABLED, T3-02 digest summary text.
"""
import io
import os
import sys
import time

import pytest
from sqlalchemy import inspect

from backend.accounts import auth as acct_auth
from backend.accounts.context import unscoped
from backend.models.db import Account, Base
from backend.tests.r4_support import client  # noqa: F401 (fixture)
from backend.tests.test_mt_telegram import accounts  # noqa: F401 (fixture)

EXPECTED_INDEXES = {
    "jobs": {"ix_jobs_acct_status", "ix_jobs_acct_discovered", "ix_jobs_acct_score"},   # score: PG; SQLite builds ix_jobs_acct_score_sqlite
    "applications": {"ix_applications_acct_status_updated"},
    "llm_call_log": {"ix_llm_call_log_acct_created"},
    "job_runs": {"ix_job_runs_acct_started"},
    "scrape_log": {"ix_scrape_log_acct_company"},
}


# ── T6-01 ────────────────────────────────────────────────────────────────────

def test_models_declare_the_composite_indexes():
    for table, names in EXPECTED_INDEXES.items():
        have = {i.name.replace("_sqlite", "") for i in Base.metadata.tables[table].indexes}
        assert names <= have, (table, names - have)


def test_sqlite_test_database_builds_them(test_db):
    insp = inspect(test_db.get_bind())
    for table, names in EXPECTED_INDEXES.items():
        have = {i["name"].replace("_sqlite", "") for i in insp.get_indexes(table)}
        assert names <= have, table


def test_migration_statements_match_the_models():
    from backend.seed import ACCOUNT_INDEX_STATEMENTS
    wanted = {n for names in EXPECTED_INDEXES.values() for n in names}
    in_sql = set()
    for sql in ACCOUNT_INDEX_STATEMENTS:
        assert sql.startswith("CREATE INDEX IF NOT EXISTS ")     # idempotent
        in_sql.add(sql.split()[5])
    assert in_sql == wanted


def test_migrations_are_run_at_startup():
    import inspect as pyinspect
    import backend.seed as seed
    assert "ACCOUNT_INDEX_STATEMENTS" in pyinspect.getsource(seed.run_migrations)


# ── T6-02 ────────────────────────────────────────────────────────────────────

@pytest.fixture
def lookups(monkeypatch):
    n = {"count": 0}
    real = acct_auth.load_accounts

    def counting(db):
        # only the middleware's cached lookup (the test client's token hook also reads)
        if sys._getframe(1).f_code.co_name == "cached_accounts":
            n["count"] += 1
        return real(db)
    monkeypatch.setattr(acct_auth, "load_accounts", counting)
    acct_auth.invalidate_accounts_cache()
    return n


def test_the_middleware_reads_the_accounts_once_for_many_requests(client, test_db, lookups):
    # /api/jobs/feed-stats does not read the accounts list itself
    for _ in range(6):
        assert client.get("/api/jobs/feed-stats").status_code == 200
    assert lookups["count"] == 1


def test_cache_expires(client, test_db, lookups, monkeypatch):
    monkeypatch.setattr(acct_auth, "ACCOUNTS_CACHE_TTL", 0.05)
    client.get("/api/jobs/feed-stats")
    client.get("/api/jobs/feed-stats")
    assert lookups["count"] == 1
    time.sleep(0.1)
    client.get("/api/jobs/feed-stats")
    assert lookups["count"] == 2


def test_cached_accounts_returns_copies(test_db):
    first = acct_auth.cached_accounts(test_db)
    first[0]["name"] = "tampered"
    assert acct_auth.cached_accounts(test_db)[0]["name"] != "tampered"


def test_account_writes_invalidate(test_db, accounts):
    a, b = accounts
    assert any(x["id"] == b for x in acct_auth.cached_accounts(test_db))
    with unscoped():
        row = test_db.query(Account).filter(Account.id == b).one()
        row.name = "Renamed"
        test_db.commit()
    assert {x["id"]: x["name"] for x in acct_auth.cached_accounts(test_db)}[b] == "Renamed"
    with unscoped():
        row = test_db.query(Account).filter(Account.id == b).one()
        row.pin_hash = acct_auth.hash_pin("1234")
        test_db.commit()
    assert {x["id"]: x["pin_hash"] for x in acct_auth.cached_accounts(test_db)}[b]
    with unscoped():
        test_db.delete(test_db.query(Account).filter(Account.id == b).one())
        test_db.commit()
    assert all(x["id"] != b for x in acct_auth.cached_accounts(test_db))


def test_bulk_delete_path_invalidates(test_db, accounts):
    """admin.delete_account_rows uses Query.delete (no mapper event): explicit call."""
    from backend.accounts import admin
    a, b = accounts
    assert any(x["id"] == b for x in acct_auth.cached_accounts(test_db))
    admin.delete_account_rows(test_db, b)
    assert all(x["id"] != b for x in acct_auth.cached_accounts(test_db))


def test_pin_change_through_the_api_is_seen_at_once(client, test_db, accounts):
    a, b = accounts
    assert not {x["id"]: x["pin_hash"] for x in acct_auth.cached_accounts(test_db)}[b]
    r = client.patch(f"/api/accounts/{b}", json={"pin": "4321"})
    assert r.status_code == 200
    assert {x["id"]: bool(x["pin_hash"]) for x in acct_auth.cached_accounts(test_db)}[b]


# ── T9-01 / T9-02 ────────────────────────────────────────────────────────────

DUMP = (b"-- header\n\\restrict abc\nSET statement_timeout = 0;\nSET transaction_timeout = 0;\n"
        b"SET client_encoding = 'UTF8';\n"
        b"COPY public.job_runs (id, job_type, trigger, status, finished_at) FROM stdin;\n"
        b"1\tdb_backup\tscheduler\trunning\t\\N\n"
        b"2\tdb_backup\tscheduler\tcompleted\t2026-10-09\n"
        b"3\tscrape_all\tscheduler\trunning\t\\N\n"
        b"\\.\n"
        b"COPY public.other (id, job_type, status) FROM stdin;\n"
        b"9\tdb_backup\trunning\n"
        b"\\.\n")


def _filter(data=DUMP):
    from backend.db_backup import filter_dump
    out = io.BytesIO()
    info = filter_dump(io.BytesIO(data), out)
    return out.getvalue(), info


def test_transaction_timeout_line_is_stripped():
    out, info = _filter()
    assert b"transaction_timeout" not in out
    assert b"SET statement_timeout = 0;" in out and b"SET client_encoding" in out
    assert info["dropped_settings"] == 1


def test_only_the_running_backup_row_is_dropped():
    out, info = _filter()
    assert b"1\tdb_backup\tscheduler\trunning" not in out
    assert b"2\tdb_backup\tscheduler\tcompleted" in out        # history kept
    assert b"3\tscrape_all\tscheduler\trunning" in out         # other runs untouched
    assert b"9\tdb_backup\trunning" in out                     # other tables untouched
    assert out.count(b"\\.\n") == 2
    assert info["dropped_runs"] == 1


def test_filter_leaves_a_clean_dump_alone():
    clean = b"SET x = 1;\nCOPY public.t (a) FROM stdin;\nrow\n\\.\n"
    assert _filter(clean)[0] == clean


class _Proc:
    def __init__(self, rc=0, err=""):
        self.returncode, self.stderr = rc, err


def _fake_dump(monkeypatch, tmp_path, rc=0):
    import backend.config as cfg
    import backend.db_backup as bk
    monkeypatch.setattr(cfg, "DATABASE_URL", "postgresql://u:p@db:5432/jn")
    monkeypatch.setattr(bk, "BACKUP_DIR", str(tmp_path))

    def run(cmd, **kw):
        if rc == 0:
            with open(cmd[cmd.index("-f") + 1], "wb") as fh:
                fh.write(DUMP)
        return _Proc(rc, "boom" if rc else "")
    monkeypatch.setattr(bk.subprocess, "run", run)
    return bk


def test_run_pg_dump_writes_the_filtered_file(monkeypatch, tmp_path):
    bk = _fake_dump(monkeypatch, tmp_path)
    res = bk.run_pg_dump()
    body = open(res["path"], "rb").read()
    assert b"transaction_timeout" not in body and b"1\tdb_backup\tscheduler\trunning" not in body
    assert sorted(os.listdir(tmp_path)) == [res["file"]]      # no .raw left behind


def test_failed_dump_leaves_nothing(monkeypatch, tmp_path):
    bk = _fake_dump(monkeypatch, tmp_path, rc=1)
    with pytest.raises(RuntimeError, match="boom"):
        bk.run_pg_dump()
    assert os.listdir(tmp_path) == []


# ── T3-04 ────────────────────────────────────────────────────────────────────

def test_prune_keeps_the_newest_five_per_prefix(monkeypatch, tmp_path):
    import backend.db_backup as bk
    monkeypatch.setattr(bk, "BACKUP_DIR", str(tmp_path))
    for d in range(1, 9):
        (tmp_path / f"pre_remove_202610{d:02d}_000000_x.sql").write_text("x")
        (tmp_path / f"jobnavigator_202610{d:02d}_000000.sql").write_text("x")
    (tmp_path / "mine.dump").write_text("keep")
    (tmp_path / "pre_remove_notes.txt").write_text("keep")
    assert bk.prune("pre_remove_") == 3
    left = sorted(os.listdir(tmp_path))
    assert [f for f in left if f.startswith("pre_remove_") and f.endswith(".sql")] == [
        f"pre_remove_202610{d:02d}_000000_x.sql" for d in range(4, 9)]
    assert len([f for f in left if f.startswith("jobnavigator_")]) == 8     # other prefix untouched
    assert "mine.dump" in left and "pre_remove_notes.txt" in left


def test_each_pre_remove_dump_prunes_its_own_prefix(monkeypatch, tmp_path):
    bk = _fake_dump(monkeypatch, tmp_path)
    for d in range(1, 8):
        (tmp_path / f"pre_remove_202601{d:02d}_000000_x.sql").write_text("x")
    res = bk.run_pg_dump(prefix="pre_remove_", suffix="_zed")
    names = sorted(f for f in os.listdir(tmp_path) if f.startswith("pre_remove_"))
    assert len(names) == 5 and res["file"] in names and res["pruned"] == 3


def test_account_removal_takes_a_pruned_dump(monkeypatch):
    from backend.api import routes_accounts
    import backend.db_backup as bk
    seen = {}

    def fake(prefix="", suffix="", rotate=True):
        seen.update(prefix=prefix, rotate=rotate)
        return {"file": "f.sql"}
    monkeypatch.setattr(bk, "run_pg_dump", fake)
    routes_accounts.backup_before_remove({"name": "Some One"})
    assert seen == {"prefix": "pre_remove_", "rotate": True}


# ── T2-01 ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value,expected", [("1", True), ("true", True), ("YES", True), (" on ", True),
                                            ("", False), ("0", False), ("false", False)])
def test_scheduler_disabled_env(monkeypatch, value, expected):
    from backend.scheduler import scheduler_disabled
    monkeypatch.setenv("JN_SCHEDULER_DISABLED", value)
    assert scheduler_disabled() is expected


def test_scheduler_enabled_by_default(monkeypatch):
    from backend.scheduler import scheduler_disabled
    monkeypatch.delenv("JN_SCHEDULER_DISABLED", raising=False)
    assert scheduler_disabled() is False


def _patch_lifespan(monkeypatch):
    from unittest.mock import MagicMock
    import backend.main as main_mod
    import backend.scheduler as sched_mod
    configured, fake = MagicMock(), MagicMock()
    fake.running = False
    monkeypatch.setattr(main_mod, "create_tables", lambda: None)
    monkeypatch.setattr(main_mod, "run_seeds", lambda: None)
    monkeypatch.setattr(main_mod, "cleanup_stale_runs", lambda: None)
    monkeypatch.setattr(sched_mod, "configure_scheduler", configured)
    monkeypatch.setattr(sched_mod, "scheduler", fake)
    return main_mod, configured, fake


def test_lifespan_does_not_start_the_scheduler_when_disabled(test_db, monkeypatch, caplog):
    import logging
    from fastapi.testclient import TestClient
    main_mod, configured, fake = _patch_lifespan(monkeypatch)
    monkeypatch.setenv("JN_SCHEDULER_DISABLED", "1")
    with caplog.at_level(logging.WARNING):
        with TestClient(main_mod.app):
            pass
    fake.start.assert_not_called()
    configured.assert_not_called()
    fake.shutdown.assert_not_called()
    assert "JN_SCHEDULER_DISABLED" in caplog.text


def test_lifespan_starts_it_otherwise(test_db, monkeypatch):
    from fastapi.testclient import TestClient
    main_mod, configured, fake = _patch_lifespan(monkeypatch)
    fake.running = True
    monkeypatch.delenv("JN_SCHEDULER_DISABLED", raising=False)
    with TestClient(main_mod.app):
        pass
    fake.start.assert_called_once()
    configured.assert_called_once()
    fake.shutdown.assert_called_once()


# ── T3-02 ────────────────────────────────────────────────────────────────────

def _digest_summaries(test_db):
    from backend.models.db import JobRun
    with unscoped():
        test_db.expire_all()
        return [r.result_summary for r in test_db.query(JobRun).filter(JobRun.job_type == "daily_digest").all()]


@pytest.mark.asyncio
async def test_digest_without_telegram_says_skipped(test_db, monkeypatch):
    import backend.notifier.telegram as tg
    from backend.scheduler import send_daily_digest, DIGEST_SKIPPED_SUMMARY
    monkeypatch.setattr(tg, "_is_enabled", lambda: False)
    await send_daily_digest()
    assert _digest_summaries(test_db) == [DIGEST_SKIPPED_SUMMARY]
    assert DIGEST_SKIPPED_SUMMARY == "Digest skipped (Telegram off / no chat)"


@pytest.mark.asyncio
async def test_digest_without_a_chat_says_skipped(test_db, monkeypatch):
    import backend.notifier.telegram as tg
    from backend.scheduler import send_daily_digest, DIGEST_SKIPPED_SUMMARY
    monkeypatch.setattr(tg, "_is_enabled", lambda: True)
    monkeypatch.setattr(tg, "resolve_target", lambda: ("", None))
    await send_daily_digest()
    assert _digest_summaries(test_db) == [DIGEST_SKIPPED_SUMMARY]


@pytest.mark.asyncio
async def test_digest_that_went_out_still_says_sent(test_db, monkeypatch):
    import backend.notifier.telegram as tg
    from backend.scheduler import send_daily_digest
    sent = []

    async def _send(chat_id, text, *a, **k):
        sent.append(text)
        return True
    monkeypatch.setattr(tg, "_send_message", _send)
    monkeypatch.setattr(tg, "_is_enabled", lambda: True)
    monkeypatch.setattr(tg, "resolve_target", lambda: ("123", None))
    monkeypatch.setattr(tg, "STRONG_MATCH_SQL", "COALESCE(best_cv_score, 0) >= :threshold")
    await send_daily_digest()
    assert sent and _digest_summaries(test_db) == ["1 alert"]


def test_send_digest_returns_false_when_skipped(test_db, monkeypatch):
    import asyncio
    import backend.notifier.telegram as tg
    monkeypatch.setattr(tg, "_is_enabled", lambda: False)
    assert asyncio.run(tg.send_digest()) is False
