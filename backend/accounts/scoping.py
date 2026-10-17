"""Central account scoping (MT-2): two listeners on the Session *class*, so every
session from every sessionmaker (SessionLocal, test fixtures, scripts) is covered.

- do_orm_execute: every ORM SELECT / bulk UPDATE / bulk DELETE gets
  `with_loader_criteria(AccountScoped, account_id == current)`, which reaches every
  account-owned entity in the statement (joins, aliases, relationship loads). An
  `insert(...)` into an account-owned table executed through the Session gets its
  `account_id` stamped (QA F4 / R1-04).
- before_flush: new account-owned instances without an account get the current one.

Strict mode (`MT_STRICT=1`, tests) with no context and not `unscoped()`: any statement
that reaches an account-owned table anywhere (subquery, count(), exists(), union, a
join from an unowned entity, a Core table) raises AccountContextMissing (QA F4 / R1-02).

Not covered (see MT-PLAN.md "Unscoped call sites"): `text()` statements, Core
statements executed on a Connection (not through the Session), `bulk_insert_mappings` /
`bulk_save_objects` (no event fires; `test_qa_f4_scoping.py` forbids them in backend/),
and `Session.get()` / `Query.get()` hits served from the identity map, plus lazy loads
from rows loaded under another context (QA F10 / R1-05: a session reused across a switch
is passed to `use_account` / `acting_as` / `for_each_account` as `session=`, which drops
other accounts' rows from it via `drop_foreign_rows`; fan-out code uses a fresh session
per account, `background.account_session`).
"""
from sqlalchemy import event
from sqlalchemy.orm import Session, with_loader_criteria
from sqlalchemy.sql import visitors
from sqlalchemy.sql.selectable import TableClause

from backend.accounts.context import (
    AccountContextMissing, current_account, is_unscoped, owner_id_for_session,
    resolve_account, strict_mode,
)

_installed = False
_marker = None


def _owned_table_names(marker) -> frozenset:
    """Names of the tables of every model that carries the account marker."""
    names, todo = set(), list(marker.__subclasses__())
    while todo:
        cls = todo.pop()
        todo.extend(cls.__subclasses__())
        table = getattr(cls, "__table__", None)
        if table is not None:
            names.add(table.name)
    return frozenset(names)


def owned_tables_in(statement, marker=None) -> set:
    """Account-owned table names anywhere in `statement` (subqueries, exists, unions,
    joins, Core tables, aliases)."""
    owned = _owned_table_names(marker or _marker)
    found = set()
    for el in visitors.iterate(statement):
        if isinstance(el, TableClause) and getattr(el, "name", None) in owned:
            found.add(el.name)
    return found


def _missing_context(what: str):
    return AccountContextMissing(
        f"No account context for a statement on {what}: wrap the code in "
        "use_account(...) or unscoped() (MT_STRICT=1)")


def _stamp_insert(state, marker):
    """Give an INSERT into an account-owned table its account_id (R1-04)."""
    stmt = state.statement
    table = getattr(stmt, "table", None)
    name = getattr(table, "name", None)
    if name not in _owned_table_names(marker):
        return
    account = current_account.get()
    if account is None:
        if is_unscoped():
            account = owner_id_for_session(state.session)
        else:
            account = resolve_account(state.session)   # raises in strict mode
    if account is None:
        if strict_mode():
            raise _missing_context(name)
        return   # no accounts table yet (pre-migration): nothing to stamp with
    params = state.parameters
    if isinstance(params, list) and params:
        for p in params:
            if isinstance(p, dict) and p.get("account_id") is None:
                p["account_id"] = account
        return
    if isinstance(params, dict) and params:
        if params.get("account_id") is None:
            params["account_id"] = account
        return
    multi = getattr(stmt, "_multi_values", None)
    if multi:
        raise ValueError(
            f"multi-row values() INSERT into {name}: pass account_id on every row "
            "or insert through the ORM")
    values = getattr(stmt, "_values", None) or {}
    if any(getattr(k, "key", k) == "account_id" for k in values):
        return
    if getattr(stmt, "select", None) is not None:   # INSERT … FROM SELECT
        if strict_mode():
            raise ValueError(f"INSERT … FROM SELECT into {name} must carry account_id")
        return
    state.statement = stmt.values(account_id=account)


def install_scoping(marker) -> None:
    """Attach the listeners once; `marker` is the mixin every account-owned model carries."""
    global _installed, _marker
    if _installed:
        return
    _installed = True
    _marker = marker

    @event.listens_for(Session, "do_orm_execute")
    def _scope_statement(state):
        if state.is_insert:
            if not state.execution_options.get("jn_unscoped"):
                _stamp_insert(state, marker)
            return
        if not (state.is_select or state.is_update or state.is_delete):
            return
        # Lazy/column loads inherit the criteria from the statement that loaded the parent.
        if state.is_column_load or state.is_relationship_load:
            return
        if is_unscoped() or state.execution_options.get("jn_unscoped"):
            return
        try:
            mappers = state.all_mappers
        except Exception:
            mappers = []
        if any(issubclass(m.class_, marker) for m in mappers):
            account = resolve_account(state.session)  # raises in strict mode without a context
        else:
            # An unowned entity that may join an owned one (JobLocation → Job), a SELECT
            # whose entities sit in a subquery (Query.count(), exists(), union) or a Core
            # statement: scope it when we can, and in strict mode refuse to run it with
            # no context when an owned table appears anywhere in it (R1-02).
            account = current_account.get()
            if account is None:
                if strict_mode():
                    hit = owned_tables_in(state.statement, marker)
                    if hit:
                        raise _missing_context(", ".join(sorted(hit)))
                elif mappers or state.is_select:
                    account = owner_id_for_session(state.session)
            if not mappers and not state.is_select:
                return  # Core-table UPDATE/DELETE: not ORM, criteria do not apply
        if account is None:
            return
        state.statement = state.statement.options(
            with_loader_criteria(marker, lambda cls: cls.account_id == account, include_aliases=True)
        )

    @event.listens_for(Session, "before_flush")
    def _stamp_new_rows(session, flush_context, instances):
        new = [o for o in session.new
               if isinstance(o, marker) and getattr(o, "account_id", None) is None]
        if not new:
            return
        account = current_account.get()
        if account is None:
            account = owner_id_for_session(session) if is_unscoped() else resolve_account(session)
        if account is None:
            # R1-03: never stamp NULL while accounts exist (the row would be invisible
            # and later backfilled to the owner, whoever created it). System code
            # (`unscoped()`) before the migration has made an owner may still do so.
            if not is_unscoped() and _accounts_exist(session):
                raise _missing_context(type(new[0]).__name__ + " (new row)")
            return
        for obj in new:
            obj.account_id = account


_UNLOADED = object()


def drop_foreign_rows(session) -> int:
    """Expunge from `session`'s identity map every account-owned row of another account
    than the current context's (QA F10 / R1-05), so `Session.get()` cannot answer with
    it from memory and no lazy load runs from it. A row whose account is not loaded is
    dropped too (fail closed). `unscoped()` sees every account: nothing is dropped.
    Returns how many rows were expunged."""
    if session is None or _marker is None or is_unscoped():
        return 0
    from sqlalchemy import inspect as sa_inspect
    account = current_account.get()
    if account is None:
        try:
            account = owner_id_for_session(session)
        except Exception:
            account = None   # unknown: keep nothing account-owned
    dropped = 0
    for obj in list(session.identity_map.values()):
        if not isinstance(obj, _marker):
            continue
        owner = sa_inspect(obj).dict.get("account_id", _UNLOADED)
        if owner is _UNLOADED or account is None or str(owner) != str(account):
            session.expunge(obj)
            dropped += 1
    return dropped


def _accounts_exist(session) -> bool:
    from sqlalchemy import func, select
    from backend.models.db import Account
    t = Account.__table__
    try:
        return bool(session.connection().execute(select(func.count()).select_from(t)).scalar())
    except Exception:
        return False
