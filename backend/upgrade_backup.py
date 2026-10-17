"""Automatic database backup before a version upgrade migrates the database.

The first boot of a new release migrates the schema in place (3.0 adds an account
column to every owned table and moves all rows to the owner). A half-migrated
database without a backup is the one state that cannot be fixed, so startup runs
`prepare_upgrade()` BEFORE create_tables / seeding:

- the release that last booted against this database is the marker row in
  `app_meta` (key `app_version`), written by `mark_booted()` after a successful
  startup;
- no marker: an empty database (no `settings` table) is a fresh install and takes
  no backup; a database with an `accounts` table is 3.0 from before the marker
  existed; anything else is 2.x;
- a known release other than this code's: pg_dump the whole database into
  backups/pre_upgrade_<old>_to_<new>_<timestamp>.sql (the same `run_pg_dump` the
  scheduled backup uses) and log where it went;
- the dump fails: log an ERROR and stop startup before anything is migrated.
  `JN_SKIP_UPGRADE_BACKUP=1` (in .env) skips the backup and migrates without one.

`app_meta` is install-level on purpose: it describes the database itself, not any
account's data, so it is not an AccountScoped model and is read and written with
plain SQL on the engine (the scoping listeners only see ORM queries). It is not a
`settings` row because settings carry sections, policies and per-account copies,
and must not be read before the settings table is migrated.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from sqlalchemy import inspect, text

from backend.version import APP_VERSION

logger = logging.getLogger("jobnavigator.upgrade")

MARKER_TABLE = "app_meta"
MARKER_KEY = "app_version"
SKIP_ENV = "JN_SKIP_UPGRADE_BACKUP"
PREFIX = "pre_upgrade_"
DUMP_TIMEOUT = 3600       # seconds; a large install may take minutes, never block forever
HEARTBEAT = 15            # seconds between "still dumping" lines


class UpgradeBackupFailed(RuntimeError):
    """Startup must stop: the database would be migrated without a backup."""


@dataclass(frozen=True)
class DbState:
    marker: Optional[str]     # the app_meta release, None when absent
    has_settings: bool        # any JobNavigator schema at all
    has_accounts: bool        # 3.0 schema (the accounts table is new in 3.0)


@dataclass(frozen=True)
class Decision:
    backup: bool
    old: Optional[str]        # the release the database is on ("2.x" when inferred)
    new: str
    reason: str


def decide(state: DbState, code_version: str = APP_VERSION) -> Decision:
    """Pure: should this boot back up the database first?"""
    if state.marker:
        if state.marker == code_version:
            return Decision(False, state.marker, code_version, "current")
        return Decision(True, state.marker, code_version, "version changed")
    if not state.has_settings:
        return Decision(False, None, code_version, "fresh database")
    if state.has_accounts:
        # 3.0 schema booted before the marker existed: nothing to migrate from
        return Decision(False, "3.0", code_version, "3.0 schema without marker")
    return Decision(True, "2.x", code_version, "2.x schema")


def _engine():
    # read at call time: the tests rebind backend.models.db.engine to their own DB
    import backend.models.db as db_mod
    return db_mod.engine


def read_state(engine=None) -> DbState:
    engine = engine or _engine()
    insp = inspect(engine)
    marker = None
    if insp.has_table(MARKER_TABLE):
        with engine.connect() as conn:
            marker = conn.execute(text(f"SELECT value FROM {MARKER_TABLE} WHERE key = :k"),
                                  {"k": MARKER_KEY}).scalar()
    return DbState(marker=marker or None, has_settings=insp.has_table("settings"),
                   has_accounts=insp.has_table("accounts"))


def mark_booted(engine=None, version: str = APP_VERSION) -> None:
    """Record that this release booted successfully against the database."""
    engine = engine or _engine()
    with engine.begin() as conn:
        conn.execute(text(f"CREATE TABLE IF NOT EXISTS {MARKER_TABLE} "
                          "(key VARCHAR(64) PRIMARY KEY, value TEXT NOT NULL)"))
        updated = conn.execute(text(f"UPDATE {MARKER_TABLE} SET value = :v WHERE key = :k"),
                               {"k": MARKER_KEY, "v": version}).rowcount
        if not updated:
            conn.execute(text(f"INSERT INTO {MARKER_TABLE} (key, value) VALUES (:k, :v)"),
                         {"k": MARKER_KEY, "v": version})


def _skip_requested() -> bool:
    return os.getenv(SKIP_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def _dump(prefix: str) -> dict:
    from backend.db_backup import run_pg_dump
    return run_pg_dump(prefix=prefix, rotate=False, timeout=DUMP_TIMEOUT)


def prepare_upgrade(state: Optional[DbState] = None,
                    dump: Callable[[str], dict] = _dump,
                    code_version: str = APP_VERSION) -> Decision:
    """Startup hook, before any migration. Takes the backup when the release changed;
    raises UpgradeBackupFailed (stop startup) when that backup fails, unless
    JN_SKIP_UPGRADE_BACKUP is set."""
    state = state if state is not None else read_state()
    decision = decide(state, code_version)
    if not decision.backup:
        if decision.reason == "3.0 schema without marker":
            logger.info("Database is on the 3.0 schema; recording the release marker.")
        return decision

    label = f"{decision.old} → {decision.new}"
    prefix = f"{PREFIX}{decision.old}_to_{decision.new}_"
    if _skip_requested():
        logger.warning("%s is set: migrating the database %s WITHOUT a pre-upgrade backup.", SKIP_ENV, label)
        return decision
    logger.warning("Upgrading the database %s: backing it up first to backups/%s<timestamp>.sql "
                   "(pg_dump; a large database can take a minute).", label, prefix)
    started = time.monotonic()
    done = threading.Event()

    def heartbeat():
        while not done.wait(HEARTBEAT):
            logger.info("Pre-upgrade backup still running (%ds)…", int(time.monotonic() - started))

    beat = threading.Thread(target=heartbeat, name="upgrade-backup-heartbeat", daemon=True)
    beat.start()
    try:
        result = dump(prefix)
    except Exception as error:
        done.set()
        detail = (str(error).strip() or error.__class__.__name__).splitlines()[-1][:300]
        message = (f"Pre-upgrade backup FAILED ({detail}). The database was NOT migrated ({label}) "
                   f"and the backend will not start without a backup. Check that backups/ is mounted "
                   f"and writable and the disk has room, then restart; or set {SKIP_ENV}=1 in .env to "
                   f"migrate without one.")
        logger.error(message)
        raise UpgradeBackupFailed(message) from error
    done.set()
    logger.warning("Pre-upgrade backup written: backups/%s (%.1f MB, %ds). Migrating %s.",
                   result.get("file"), result.get("size_mb") or 0, int(time.monotonic() - started), label)
    return decision
