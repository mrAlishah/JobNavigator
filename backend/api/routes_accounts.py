"""Accounts API (MT-11): list, add (with copy-from), rename/colour/PIN, remove, export.

Roles: one owner, up to four members (MAX_ACCOUNTS = 5 in total).
- GET    /accounts                 owner: every account with counts; member: its own row in
                                   full, the others as {id, name, color, role}.
- POST   /accounts                 owner only. 409 `account_limit` at 5 accounts. PINs are
                                   optional, the owner's included (QA F2 / D3): an owner
                                   without a PIN opens from the picker with one click.
- PATCH  /accounts/{account_id}    owner: any account; member: itself. PIN rules below.
- DELETE /accounts/{account_id}    owner only; never the owner or the caller; typed name;
                                   pg_dump first (abort when it fails); itemised counts.
- GET    /accounts/{account_id}/export   owner: any account; member: itself. Zip of JSON.
- POST   /accounts/{account_id}/api-token   the caller's own account only (QA F1): a signed
                                   `X-Account-Token` for scripts.

PIN (body key `pin`: 4 digits = set/change, null or "" = remove):
- on oneself, `current_pin` is required when a PIN is set (wrong -> 401 `wrong_pin`, with
  the MT-10 per-account cooldown); afterwards the caller's `jn_account` cookie is re-signed
  (a PIN change invalidates every cookie issued for the account, MT-10);
- the owner may set or clear a member's PIN without it (a reset; nothing secret returned);
- the owner may remove their own PIN at any time, members or not (QA F2 / D3); without an
  owner PIN the owner sections a member sees are only a privacy latch (a plain Unlock).
"""
import logging
import re
import uuid
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session

from backend.accounts import admin
from backend.accounts import auth as acct_auth
from backend.accounts.context import current_account
from backend.models.db import ACCOUNT_COLORS, Account, get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/accounts", tags=["accounts"])

NAME_MAX = 40
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


# ── helpers ──────────────────────────────────────────────────────────────────

def _error(status: int, code: str, message: str, **extra) -> JSONResponse:
    return JSONResponse(status_code=status, content={"detail": code, "message": message, **extra})


def _accounts(db):
    return acct_auth.load_accounts(db)


def _caller(accounts: list) -> Optional[dict]:
    """The account this request acts for (the owner when no context is set)."""
    return acct_auth._find(accounts, current_account.get()) or acct_auth._owner_of(accounts)


def _is_owner(acc: Optional[dict]) -> bool:
    return acc is not None and acc["role"] == "owner"


def _iso(dt):
    return dt.isoformat() if dt is not None else None


def _full_view(acc: dict, row: Optional[Account], counts: dict, caller: dict) -> dict:
    out = acct_auth.public_account(acc)
    out.update({
        "created_at": _iso(acc.get("created_at")),
        "last_active_at": _iso(row.last_active_at) if row is not None else None,
        "counts": {**admin.empty_counts(), **counts.get(acc["id"], {})},
        "is_self": acc["id"] == caller["id"],
    })
    return out


def _target_or_404(accounts: list, account_id: str) -> dict:
    acc = acct_auth._find(accounts, account_id)
    if acc is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return acc


def _clean_name(value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise HTTPException(status_code=422, detail="name is required")
    name = " ".join(value.split())
    if len(name) > NAME_MAX:
        raise HTTPException(status_code=422, detail=f"name is at most {NAME_MAX} characters")
    return name


def _clean_color(value) -> str:
    if not isinstance(value, str) or not _COLOR_RE.match(value.strip()):
        raise HTTPException(status_code=422, detail="color must be a #rrggbb hex colour")
    return value.strip().lower()


def _name_taken(accounts: list, name: str, except_id=None) -> bool:
    return any(a["name"].strip().lower() == name.lower() and a["id"] != except_id for a in accounts)


def _clean_pin(value) -> str:
    if not acct_auth.valid_pin_format(value):
        raise HTTPException(status_code=422, detail="pin must be exactly 4 digits")
    return value


def _rows_by_id(db) -> dict:
    return {r.id: r for r in db.query(Account).all()}


# ── GET /accounts ────────────────────────────────────────────────────────────

@router.get("")
def list_accounts(db: Session = Depends(get_db)):
    """Owner: every account `{id, name, color, role, has_pin, created_at, last_active_at, counts{jobs, applications, resumes, cover_letters, searches, companies}, is_self}`. Member: its own row in that shape, the other accounts as `{id, name, color, role}` only. Plus `max_accounts` and `self_id`."""
    accounts = _accounts(db)
    caller = _caller(accounts)
    if caller is None:
        raise HTTPException(status_code=404, detail="No account")
    rows = _rows_by_id(db)
    counts = admin.account_counts(db)
    out = []
    for acc in accounts:
        if _is_owner(caller) or acc["id"] == caller["id"]:
            out.append(_full_view(acc, rows.get(acc["id"]), counts, caller))
        else:
            out.append({"id": str(acc["id"]), "name": acc["name"], "color": acc["color"],
                        "role": acc["role"]})
    return {"accounts": out, "max_accounts": admin.MAX_ACCOUNTS, "self_id": str(caller["id"])}


# ── POST /accounts ───────────────────────────────────────────────────────────

@router.post("", status_code=201)
def create_account(response: Response, body: dict = Body(...), db: Session = Depends(get_db)):
    """Owner only. Body `{name, color?, pin?, copy_from?: {account_id, items: [persona, qa, resumes, companies, searches, settings, voices, excludes]}}`. The new account is always a member and starts with an empty Persona and the two built-in extension searches; copy-from deep-copies the chosen items (new ids; résumé ids inside companies' `selected_resume_ids` and `default_resume_id` point at the copies, ids not copied are dropped; no jobs, applications or letters). PINs are optional, the owner's too. 409 `account_limit` (5 accounts), 409 `name_taken`, 422 bad name/colour/PIN/items, 404 unknown copy source."""
    body = body or {}
    accounts = _accounts(db)
    caller = _caller(accounts)
    if not _is_owner(caller):
        raise HTTPException(status_code=403, detail="owner_only")
    if len(accounts) >= admin.MAX_ACCOUNTS:
        return _error(409, "account_limit",
                      f"An install holds at most {admin.MAX_ACCOUNTS} accounts; remove one first.")
    # QA F2 / D3: PINs are optional, the owner's too (no `owner_pin_required` any more).

    name = _clean_name(body.get("name"))
    if _name_taken(accounts, name):
        return _error(409, "name_taken", f"An account named {name!r} already exists.")
    used = {a["color"].lower() for a in accounts if a.get("color")}
    if body.get("color") in (None, ""):
        color = next((c for c in ACCOUNT_COLORS if c.lower() not in used), ACCOUNT_COLORS[0])
    else:
        color = _clean_color(body.get("color"))
    pin = body.get("pin")
    pin_hash = acct_auth.hash_pin(_clean_pin(pin)) if pin not in (None, "") else None

    source_id, items = None, []
    cf = body.get("copy_from")
    if cf not in (None, {}):
        if not isinstance(cf, dict):
            raise HTTPException(status_code=422, detail="copy_from must be {account_id, items}")
        items = cf.get("items") or []
        if not isinstance(items, list) or any(i not in admin.COPY_ITEMS for i in items):
            raise HTTPException(status_code=422,
                                detail="copy_from.items: any of " + ", ".join(admin.COPY_ITEMS))
        source = acct_auth._find(accounts, cf.get("account_id"))
        if source is None:
            raise HTTPException(status_code=404, detail="Copy source account not found")
        source_id = source["id"]

    new = Account(id=uuid.uuid4(), name=name, color=color, role="member", pin_hash=pin_hash)
    db.add(new)
    db.flush()
    admin.seed_new_account(db, new.id)
    copied = {}
    if source_id is not None and items:
        copied = admin.copy_from(db, source_id, new.id, items, owner_id=caller["id"])
    db.commit()
    acct_auth.invalidate_accounts_cache()   # QA F9 / T6-02
    logger.info("account created: %s (%s), copied %s", new.id, name, copied or "nothing")

    accounts = _accounts(db)
    acc = acct_auth._find(accounts, new.id)
    view = _full_view(acc, db.query(Account).filter(Account.id == new.id).first(),
                      admin.account_counts(db), caller)
    # MT-13: a browser that has been the owner only because the install had one account
    # carries no jn_account cookie; with a second account it would land on the picker on
    # its next request. Pin this browser to the owner it already is.
    owner_now = acct_auth._find(accounts, caller["id"])
    if owner_now is not None:
        acct_auth.set_account_cookie(response, db, owner_now)
    return {"account": view, "copied": copied}


# ── PATCH /accounts/{id} ─────────────────────────────────────────────────────

@router.patch("/{account_id}")
def update_account(account_id: str, request: Request, response: Response,
                   body: dict = Body(...), db: Session = Depends(get_db)):
    """Body: any of `name`, `color`, `pin` (4 digits to set/change, null or "" to remove), `current_pin`. Owner: any account; member: itself (else 403). Changing one's own PIN needs `current_pin` when one is set (401 `pin_required` / `wrong_pin`, 429 `pin_cooldown`) and re-issues this browser's `jn_account` cookie; the owner may reset a member's PIN without it. Every PIN is optional, the owner's included (it can be removed with members present). Returns `{account, pin_changed}` — never a hash."""
    body = body or {}
    accounts = _accounts(db)
    caller = _caller(accounts)
    target = _target_or_404(accounts, account_id)
    is_self = caller is not None and target["id"] == caller["id"]
    if not (_is_owner(caller) or is_self):
        raise HTTPException(status_code=403, detail="owner_only")

    row = db.query(Account).filter(Account.id == target["id"]).first()
    changes = {}
    if "name" in body:
        name = _clean_name(body.get("name"))
        if _name_taken(accounts, name, except_id=target["id"]):
            return _error(409, "name_taken", f"An account named {name!r} already exists.")
        changes["name"] = name
    if "color" in body:
        changes["color"] = _clean_color(body.get("color"))

    pin_changed = False
    if "pin" in body:
        new_pin = body.get("pin")
        removing = new_pin in (None, "")
        if not removing:
            _clean_pin(new_pin)
        if is_self and target["pin_hash"]:
            current = body.get("current_pin")
            if current is not None and not isinstance(current, str):
                current = str(current)
            try:
                # QA F3: the same per-(account, client) counters as the switch.
                from backend.main import _auth_client_ip
                acct_auth.check_pin(target, current or None, client=_auth_client_ip(request))
            except acct_auth.SwitchDenied as e:
                content = {"detail": e.detail}
                if e.tries_left is not None:
                    content["tries_left"] = e.tries_left
                headers = {"Retry-After": str(e.retry_after)} if e.retry_after else None
                return JSONResponse(status_code=e.status, content=content, headers=headers)
        changes["pin_hash"] = None if removing else acct_auth.hash_pin(new_pin)
        pin_changed = True

    for k, v in changes.items():
        setattr(row, k, v)
    db.commit()
    acct_auth.invalidate_accounts_cache()   # QA F9 / T6-02

    if pin_changed:
        # R2-14: a reset PIN starts with fresh counters (also when the owner resets it).
        acct_auth._clear(target["id"])
        if target["role"] == "owner":
            # QA F3 / R2-06: any owner PIN change ends every owner unlock already issued.
            acct_auth.revoke_all_unlocks()

    accounts = _accounts(db)
    updated = acct_auth._find(accounts, target["id"])
    if pin_changed and is_self:
        until = getattr(request.state, "owner_unlocked_until", 0) or 0
        unlock_id = getattr(request.state, "owner_unlock_id", None)
        live = until > acct_auth._now() and acct_auth.unlock_live(unlock_id)
        acct_auth.set_account_cookie(response, db, updated,
                                     unlocked_until=until if live else 0,
                                     unlock_id=unlock_id if live else None)
    if pin_changed:
        logger.info("PIN %s for account %s by %s", "set" if changes["pin_hash"] else "removed",
                    target["id"], "itself" if is_self else "the owner")
    view = _full_view(updated, db.query(Account).filter(Account.id == target["id"]).first(),
                      admin.account_counts(db), caller)
    return {"account": view, "pin_changed": pin_changed}


# ── DELETE /accounts/{id} ────────────────────────────────────────────────────

def backup_before_remove(account: dict) -> str:
    """Whole-database pg_dump (newest 5 `pre_remove_` files kept); returns the file name. Raises
    RuntimeError when the dump fails (the removal is then aborted)."""
    from backend.db_backup import run_pg_dump
    slug = re.sub(r"[^a-z0-9]+", "-", account["name"].lower()).strip("-")[:20] or "account"
    return run_pg_dump(prefix="pre_remove_", suffix=f"_{slug}")["file"]


@router.delete("/{account_id}")
def delete_account(account_id: str, body: dict = Body(None), db: Session = Depends(get_db)):
    """Owner only. Body `{confirm_name}` must equal the account's name exactly (400 `confirm_name_mismatch`). The owner and the caller cannot be removed (409 `cannot_remove_owner` / `cannot_remove_self`); an account with running background work is refused (409 `account_busy`). A whole-database pg_dump runs first (`backups/pre_remove_<timestamp>_<name>.sql`, the newest 5 `pre_remove_` files are kept, older ones deleted); if it fails nothing is deleted (503 `backup_failed`). Then every row the account owns is deleted (no undo). Returns `{deleted: {table: rows, ...}, backup}`."""
    body = body or {}
    accounts = _accounts(db)
    caller = _caller(accounts)
    if not _is_owner(caller):
        raise HTTPException(status_code=403, detail="owner_only")
    target = _target_or_404(accounts, account_id)
    if target["role"] == "owner":
        return _error(409, "cannot_remove_owner", "The owner account cannot be removed.")
    if target["id"] == caller["id"]:
        return _error(409, "cannot_remove_self", "You cannot remove the account you are using.")
    if body.get("confirm_name") != target["name"]:
        return _error(400, "confirm_name_mismatch", "Type the account's name exactly to confirm.")

    # QA F3 / R2-08: busy = any run that writes into this account: its own runs, the
    # scheduled scrape_all fan-out and other all-account runs, and per-account sweeps that
    # include it. Checked before the pg_dump and again after it; meanwhile the account is
    # marked as being removed, so nothing new starts for it (job_monitor.begin_removal).
    import backend.job_monitor as jm

    def _busy():
        runs = jm.runs_touching(target["id"])
        if runs:
            logger.info("account %s removal refused, busy: %s", target["id"], runs)
            return _error(409, "account_busy",
                          "This account has background work running; try again when it finishes.",
                          runs=sorted(set(runs)))
        return None

    busy = _busy()
    if busy is not None:
        return busy
    jm.begin_removal(target["id"])
    try:
        try:
            backup = backup_before_remove(target)
        except Exception as e:
            logger.error("pre-removal backup failed for account %s: %s", target["id"], e)
            return _error(503, "backup_failed",
                          "The database backup failed, so nothing was removed. See the server log.")
        busy = _busy()
        if busy is not None:
            return busy
        deleted = admin.delete_account_rows(db, target["id"])
    finally:
        jm.end_removal(target["id"])
    acct_auth._clear(target["id"])
    acct_auth._touched.pop(str(target["id"]), None)
    logger.warning("account removed: %s (%s) after backup %s: %s",
                   target["id"], target["name"], backup, deleted)
    return {"deleted": deleted, "backup": backup, "account": {"id": str(target["id"]), "name": target["name"]}}


# ── POST /accounts/{id}/api-token ────────────────────────────────────────────

@router.post("/{account_id}/api-token")
def issue_api_token(account_id: str, db: Session = Depends(get_db)):
    """The caller's own account only (403 `own_account_only` for any other, the owner included: to get another account's token, switch to it first). Returns `{token, account, expires_in}`: a signed API token for scripts, sent as the `X-Account-Token` header together with the install key. Valid for a year; setting, changing or removing the account's PIN, or removing the account, invalidates it."""
    accounts = _accounts(db)
    caller = _caller(accounts)
    target = _target_or_404(accounts, account_id)
    if caller is None or target["id"] != caller["id"]:
        raise HTTPException(status_code=403, detail="own_account_only")
    return {"token": acct_auth.make_api_token(db, target), "account": acct_auth.public_account(target),
            "expires_in": acct_auth.TOKEN_MAX_AGE}


# ── GET /accounts/{id}/export ────────────────────────────────────────────────

@router.get("/{account_id}/export")
def export_account(account_id: str, db: Session = Depends(get_db)):
    """Owner: any account; member: itself (else 403). A zip streamed as an attachment: `manifest.json`, `account.json` (no PIN hash), one JSON array per table of the account's rows (persona, resumes, cover_letters, searches, companies, jobs incl. cached pages, job_locations, applications, interviews, tracer_links, tracer_click_events, activity_log, llm_call_log, job_runs, scrape_log), `settings.json` (own rows; the owner's are the install values; secrets redacted) and `section_policies.json`. Résumés and letters come as JSON only: their PDFs are rendered on demand and never stored."""
    accounts = _accounts(db)
    caller = _caller(accounts)
    target = _target_or_404(accounts, account_id)
    if not (_is_owner(caller) or target["id"] == caller["id"]):
        raise HTTPException(status_code=403, detail="owner_only")
    owner = acct_auth._owner_of(accounts)
    buf = admin.build_export(db, target["id"], owner["id"] if owner else None)
    slug = re.sub(r"[^A-Za-z0-9]+", "-", target["name"]).strip("-")[:30] or "account"

    def _chunks():
        try:
            while True:
                chunk = buf.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            buf.close()

    from datetime import datetime
    filename = f"jobnavigator-{slug}-{datetime.now().strftime('%Y%m%d')}.zip"
    return StreamingResponse(_chunks(), media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})
