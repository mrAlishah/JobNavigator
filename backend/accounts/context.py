"""The account a piece of code acts for (MT-2).

`current_account` holds the id of the account whose rows every ORM query sees and
every new row belongs to (see `backend.accounts.scoping`). Request code gets it from
the HTTP middleware; background code wraps its entry point in `use_account(id)` or
`as_owner(...)`. System code that must see every account (backups, migrations, the
scheduler fan-out) runs inside `unscoped()` — keep that call grep-able.

When nothing set the context: with `MT_STRICT=1` (tests) a query on an account-owned
model raises `AccountContextMissing`; otherwise it falls back to the owner account, so
a single-user install behaves exactly as before.
"""
import functools
import logging
import os
import uuid
import weakref
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from typing import Optional

logger = logging.getLogger("jobnavigator.accounts")

current_account: ContextVar[Optional[uuid.UUID]] = ContextVar("jn_current_account", default=None)
_unscoped: ContextVar[bool] = ContextVar("jn_unscoped", default=False)


class AccountContextMissing(RuntimeError):
    """An account-owned model was queried or written with no account context (strict mode)."""


def _as_uuid(value) -> Optional[uuid.UUID]:
    if value is None or isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))


@contextmanager
def use_account(account_id, session=None):
    """Run the block as `account_id` (None clears the context).

    QA F10 / R1-05: query criteria follow the context, but a Session's identity map
    (`Session.get` hits) and lazy loads of rows it already holds do not. When the block
    keeps using a session (or a list of them) that also lives outside it, pass it as
    `session`: pending changes are flushed under the context they were made in, and on
    the way in and out every account-owned row the new context may not see is expunged
    from it. Code that opens its own session per account needs none of this."""
    sessions = _session_list(session)
    flush_pending(sessions)
    token = current_account.set(_as_uuid(account_id))
    try:
        drop_foreign(sessions)
        yield
        flush_pending(sessions)
    finally:
        current_account.reset(token)
        drop_foreign(sessions)


def _session_list(session) -> list:
    if session is None:
        return []
    if isinstance(session, (list, tuple, set)):
        return [s for s in session if s is not None]
    return [session]


def flush_pending(sessions) -> None:
    """Flush pending changes now, under the context they were made in (QA F10 / R1-05)."""
    for s in sessions:
        if s.new or s.dirty or s.deleted:
            s.flush()


def drop_foreign(sessions) -> None:
    """Expunge from each session the account-owned rows the current context may not see
    (QA F10 / R1-05; `scoping.drop_foreign_rows`)."""
    if not sessions:
        return
    from backend.accounts.scoping import drop_foreign_rows
    for s in sessions:
        drop_foreign_rows(s)


@contextmanager
def unscoped():
    """Escape hatch for system code: queries see every account's rows. New rows are still
    stamped with the current account, or the owner when none is set."""
    token = _unscoped.set(True)
    try:
        yield
    finally:
        _unscoped.reset(token)


def is_unscoped() -> bool:
    return _unscoped.get()


# MT-14: a member's browser that entered the owner's PIN in the last 15 minutes
# (`request.state.owner_unlocked_until`, MT-10). The middleware sets it per request; it
# lets that member write install-level settings and policies and start owner-only jobs.
_owner_unlocked: ContextVar[bool] = ContextVar("jn_owner_unlocked", default=False)


@contextmanager
def owner_unlocked(on: bool = True):
    token = _owner_unlocked.set(bool(on))
    try:
        yield
    finally:
        _owner_unlocked.reset(token)


def is_owner_unlocked() -> bool:
    return _owner_unlocked.get()


def strict_mode() -> bool:
    return os.environ.get("MT_STRICT", "").strip().lower() in ("1", "true", "yes")


# ── Owner id, cached per engine (it never changes; tests rebind the engine) ──
_owner_ids: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def _engine_of(bind):
    return getattr(bind, "engine", bind)


def remember_owner(bind, owner_id) -> None:
    """Prime the cache (tests create the owner themselves)."""
    _owner_ids[_engine_of(bind)] = _as_uuid(owner_id)


def forget_owner_cache() -> None:
    _owner_ids.clear()


class OwnerLookupFailed(RuntimeError):
    """The owner account could not be read (DB error other than "no accounts table yet").
    Raised instead of running unfiltered or stamping NULL (QA F4 / R1-03: fail closed)."""


def _is_missing_table(exc: BaseException) -> bool:
    """The accounts table does not exist yet (fresh DB before the migration, partial test
    schemas): SQLite "no such table", Postgres UndefinedTable "does not exist"."""
    text = str(getattr(exc, "orig", None) or exc).lower()
    return "no such table" in text or ("relation" in text and "does not exist" in text) \
        or type(getattr(exc, "orig", None)).__name__ == "UndefinedTable"


def owner_id_for_session(session) -> Optional[uuid.UUID]:
    """The owner account of the database `session` talks to, or None before the migration
    has created it. Uses a Core select on the session's connection, so no ORM events fire.

    QA F4 / R1-03: only a missing accounts table counts as "no owner yet"; any other
    failure (aborted transaction, pool timeout, DB down) raises OwnerLookupFailed, so a
    caller never runs a statement unfiltered or stamps a row with no account."""
    try:
        engine = _engine_of(session.get_bind())
    except Exception as e:
        raise OwnerLookupFailed(f"owner lookup failed: no bind ({e})") from e
    cached = _owner_ids.get(engine)
    if cached is not None:
        return cached
    from sqlalchemy import select
    from backend.models.db import Account
    t = Account.__table__
    try:
        row = session.connection().execute(
            select(t.c.id).where(t.c.role == "owner").order_by(t.c.created_at).limit(1)
        ).first()
    except Exception as e:
        if _is_missing_table(e):
            logger.debug("owner lookup: no accounts table yet: %s", e)
            return None
        raise OwnerLookupFailed(f"owner lookup failed: {e}") from e
    owner = _as_uuid(row[0]) if row else None
    if owner is not None:
        _owner_ids[engine] = owner
    return owner


def owner_account_id() -> Optional[uuid.UUID]:
    """The owner id for the app's own SessionLocal (middleware, background entry points)."""
    from backend.models import db as db_mod
    engine = _engine_of(db_mod.SessionLocal.kw.get("bind"))
    cached = _owner_ids.get(engine) if engine is not None else None
    if cached is not None:
        return cached
    s = db_mod.SessionLocal()
    try:
        return owner_id_for_session(s)
    finally:
        s.close()


def resolve_account(session) -> Optional[uuid.UUID]:
    """The account a statement runs for: the context, else (non-strict) the owner."""
    acc = current_account.get()
    if acc is not None:
        return acc
    if strict_mode():
        raise AccountContextMissing(
            "No account context: wrap the code in use_account(...) or unscoped() (MT_STRICT=1)")
    return owner_id_for_session(session)


def owner_context():
    """`use_account(owner)` when no account is set yet, else a no-op. For entry points of
    background work with no account of their own: install-level scheduler jobs, tracked
    runs and launched tasks started without a context (per-account jobs use
    `backend.accounts.background.for_each_account`)."""
    if current_account.get() is not None or is_unscoped():
        return nullcontext()
    # QA F4 / R1-03: a failed lookup raises (OwnerLookupFailed) rather than letting the
    # work run with no account context.
    owner = owner_account_id()
    return use_account(owner) if owner is not None else nullcontext()


def as_owner(func):
    """Decorate an async entry point so it runs in the owner context when none is set."""
    @functools.wraps(func)
    async def _wrapped(*args, **kwargs):
        with owner_context():
            return await func(*args, **kwargs)
    return _wrapped


# ── Clean context (QA F4 / R1-01) ──────────────────────────────────────────
# APScheduler captures the contextvars of whoever last called add_job()/wakeup() (a
# settings PATCH from a member's browser, inside unscoped() and with the owner unlock)
# and every later fire inherits them. Scheduled work therefore starts from a clean
# context twice over: jobs are registered from an empty contextvars.Context, and each
# job's entry point resets the three account variables itself.


@contextmanager
def no_account_context():
    """Run the block with no account, not unscoped and no owner unlock, whatever the
    caller had set."""
    t_acc = current_account.set(None)
    t_uns = _unscoped.set(False)
    t_unl = _owner_unlocked.set(False)
    try:
        yield
    finally:
        _owner_unlocked.reset(t_unl)
        _unscoped.reset(t_uns)
        current_account.reset(t_acc)


def run_in_clean_context(fn, *args, **kwargs):
    """Call `fn` inside a brand-new, empty contextvars.Context (nothing the caller set
    is visible, and nothing `fn` sets leaks back)."""
    import contextvars
    return contextvars.Context().run(fn, *args, **kwargs)


def scheduled_job(func):
    """Decorate a scheduler entry point: it starts from a clean account context (then
    applies its own `as_owner` / `for_each_account`)."""
    @functools.wraps(func)
    async def _wrapped(*args, **kwargs):
        with no_account_context():
            return await func(*args, **kwargs)
    return _wrapped
