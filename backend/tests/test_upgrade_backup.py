"""Automatic pg_dump before a version upgrade migrates the database.

The decision is pure (`decide`); `prepare_upgrade` runs the dump, stops startup when
it fails and honours JN_SKIP_UPGRADE_BACKUP; the marker round-trips through
`app_meta` on a real (SQLite) engine.
"""
import logging

import pytest
from sqlalchemy import create_engine, text

from backend import upgrade_backup as ub
from backend.upgrade_backup import DbState, decide, prepare_upgrade


# ── decide ──────────────────────────────────────────────────────────────────

def test_fresh_database_takes_no_backup():
    d = decide(DbState(marker=None, has_settings=False, has_accounts=False), "3.0")
    assert d.backup is False and d.reason == "fresh database"


def test_2x_database_is_backed_up():
    d = decide(DbState(marker=None, has_settings=True, has_accounts=False), "3.0")
    assert d.backup is True and d.old == "2.x" and d.new == "3.0"


def test_30_schema_without_marker_takes_no_backup():
    d = decide(DbState(marker=None, has_settings=True, has_accounts=True), "3.0")
    assert d.backup is False and d.old == "3.0"


def test_same_release_takes_no_backup():
    assert decide(DbState(marker="3.0", has_settings=True, has_accounts=True), "3.0").backup is False


def test_release_change_is_backed_up():
    d = decide(DbState(marker="3.0", has_settings=True, has_accounts=True), "3.1")
    assert d.backup is True and (d.old, d.new) == ("3.0", "3.1")


def test_marker_wins_over_schema_inference():
    # an older marker on a DB that already has accounts is still a release change
    assert decide(DbState(marker="2.2", has_settings=True, has_accounts=True), "3.0").backup is True


# ── prepare_upgrade ─────────────────────────────────────────────────────────

TWO_X = DbState(marker=None, has_settings=True, has_accounts=False)


def test_backup_runs_with_the_pre_upgrade_name(caplog, monkeypatch):
    monkeypatch.delenv(ub.SKIP_ENV, raising=False)
    prefixes = []

    def dump(prefix):
        prefixes.append(prefix)
        return {"file": prefix + "20261017_120000.sql", "size_mb": 23.4}
    with caplog.at_level(logging.INFO, logger="jobnavigator.upgrade"):
        d = prepare_upgrade(TWO_X, dump=dump, code_version="3.0")
    assert d.backup is True
    assert prefixes == ["pre_upgrade_2.x_to_3.0_"]
    assert "pre_upgrade_2.x_to_3.0_20261017_120000.sql" in caplog.text


def test_no_backup_when_current():
    d = prepare_upgrade(DbState("3.0", True, True), dump=lambda p: pytest.fail("dumped"), code_version="3.0")
    assert d.backup is False


def test_failed_backup_stops_startup(caplog, monkeypatch):
    monkeypatch.delenv(ub.SKIP_ENV, raising=False)

    def dump(prefix):
        raise RuntimeError("pg_dump failed: pg_dump: error: could not open output file: Permission denied")
    with caplog.at_level(logging.ERROR, logger="jobnavigator.upgrade"):
        with pytest.raises(ub.UpgradeBackupFailed) as exc:
            prepare_upgrade(TWO_X, dump=dump, code_version="3.0")
    msg = str(exc.value)
    assert "NOT migrated" in msg and "Permission denied" in msg and ub.SKIP_ENV in msg
    assert [r for r in caplog.records if r.levelname == "ERROR"]


def test_a_timeout_also_stops_startup(monkeypatch):
    import subprocess
    monkeypatch.delenv(ub.SKIP_ENV, raising=False)

    def dump(prefix):
        raise subprocess.TimeoutExpired("pg_dump", 3600)
    with pytest.raises(ub.UpgradeBackupFailed):
        prepare_upgrade(TWO_X, dump=dump, code_version="3.0")


def test_skip_env_migrates_without_a_backup(monkeypatch, caplog):
    monkeypatch.setenv(ub.SKIP_ENV, "1")
    called = []
    with caplog.at_level(logging.WARNING, logger="jobnavigator.upgrade"):
        d = prepare_upgrade(TWO_X, dump=lambda p: called.append(p), code_version="3.0")
    assert d.backup is True and called == []
    assert "WITHOUT a pre-upgrade backup" in caplog.text


# ── marker round trip ───────────────────────────────────────────────────────

@pytest.fixture
def engine():
    eng = create_engine("sqlite:///:memory:")
    yield eng
    eng.dispose()


def test_empty_database_reads_as_fresh(engine):
    assert ub.read_state(engine) == DbState(None, False, False)


def test_2x_schema_reads_as_2x(engine):
    with engine.begin() as c:
        c.execute(text("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)"))
    state = ub.read_state(engine)
    assert state == DbState(None, True, False)
    assert decide(state, "3.0").backup is True


def test_mark_booted_then_second_boot_takes_no_backup(engine):
    with engine.begin() as c:
        c.execute(text("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)"))
    ub.mark_booted(engine, "3.0")
    state = ub.read_state(engine)
    assert state.marker == "3.0"
    assert decide(state, "3.0").backup is False
    ub.mark_booted(engine, "3.1")      # update, not a second row
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*), max(value) FROM app_meta")).one() == (1, "3.1")
