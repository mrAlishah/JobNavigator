"""LinkedIn session (Voyager cookie) status and refresh; the login is gated behind
LinkedIn's email-PIN checkpoint, so a refresh parks in `awaiting_pin` until the code is posted back here."""
import logging
import os
import time
from datetime import datetime, timezone

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from backend.job_monitor import launch_background, JobAlreadyRunningError

logger = logging.getLogger("jobnavigator.linkedin")

router = APIRouter(prefix="/linkedin", tags=["linkedin"])

# LinkedIn's li_at cookie runs about a year, but the practical session lifetime
# is far shorter; treat a jar older than this as due for a refresh.
_STALE_AFTER_DAYS = 21


class PinIn(BaseModel):
    pin: str


def _shared_from_owner() -> bool:
    """A member on 'inherit' LinkedIn uses the owner's session (QA F5, R5-01)."""
    from backend.accounts.credentials import linkedin_shared_from_owner
    return linkedin_shared_from_owner()


def _require_session_writer() -> None:
    """Refreshing (or feeding a PIN to) the owner's shared session is the owner's job: a
    member on 'inherit' gets 403 unless its browser holds a live owner unlock."""
    if not _shared_from_owner():
        return
    from backend.accounts.context import is_owner_unlocked
    if is_owner_unlocked():
        return
    raise HTTPException(status_code=403, detail="LinkedIn is shared from the account owner; only the owner can refresh the session")


# which session: mock (the extension import and the lookup) or main (LinkedIn Personal)
Which = Annotated[str, Query(pattern="^(mock|main)$")]


@router.get("/session")
def session_status(which: Which = "mock"):
    """One session's cookie-jar age, whose sign-in saved it, and the phase of any sign-in running. `shared` is true for a member on 'inherit' LinkedIn: the session shown is the owner's."""
    from backend import refresh_linkedin_session as rls

    shared = _shared_from_owner()
    path = rls.jar(which)   # the caller's cookie jar (MT-16; the owner's when shared)
    state = rls.state(which)
    exists = os.path.exists(path)
    age_days = None
    refreshed_at = None
    if exists:
        mtime = os.path.getmtime(path)
        refreshed_at = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
        age_days = round((time.time() - mtime) / 86400, 1)

    if not exists:
        status, summary = "missing", "No session yet — Sign in."
    elif age_days is not None and age_days >= _STALE_AFTER_DAYS:
        status, summary = "stale", f"Signed in {int(age_days)}d ago — likely expired."
    else:
        days = int(age_days)
        status, summary = "ok", ("Signed in today." if days == 0 else f"Signed in {days}d ago.")
    if exists and which == rls.MOCK and (rls.signed_in_as(which) or {}).get("account") != rls.MOCK:
        # saved before the main/mock split (3.2): the employer-link lookup will not use it
        status, summary = "stale", "Saved before the main/mock split — sign in again so the link lookup can use it."
    elif exists and which == rls.MOCK:
        # the lookup's own checks: the mock email must differ from the main one and match the sign-in
        from backend.models.db import SessionLocal
        from backend.scraper._shared.linkedin_lookup import mock_session_problem
        db = SessionLocal()
        try:
            problem = mock_session_problem(db)
        finally:
            db.close()
        if problem:
            status, summary = "stale", problem[0].upper() + problem[1:] + "."
    if shared:
        summary = "The owner's session · " + summary

    return {
        "exists": exists,
        "age_days": age_days,
        "refreshed_at": refreshed_at,
        "status": status,
        "summary": summary,
        "phase": state.get("phase", "idle"),
        "detail": state.get("detail", ""),
        "shared": shared,
        "which": which,
        # the owner's address stays the owner's: a member on 'inherit' sees only that it is signed in
        "signed_in_as": None if shared else (rls.signed_in_as(which) or {}).get("email"),
    }


@router.post("/session/refresh", status_code=202)
async def session_refresh(which: Which = "mock"):
    """Start one session's interactive login. Poll /session?which= until phase leaves 'running'. Signs in and saves the caller's own session (MT-16)."""
    from backend import refresh_linkedin_session as rls

    _require_session_writer()
    state = rls.state(which)
    if state.get("phase") in ("running", "awaiting_pin"):
        return {"run_id": None, "status": state["phase"]}
    try:
        run_id = launch_background("linkedin_session_refresh", rls.run_refresh,
                                   trigger="manual", scope_key=f"linkedin-{which}", func_args=(which,))
    except JobAlreadyRunningError:
        return {"run_id": None, "status": "running"}
    return {"run_id": run_id, "status": "running"}


@router.post("/session/pin")
def session_pin(body: PinIn, which: Which = "mock"):
    """Hand the emailed PIN to the waiting login (it polls the account's PIN file)."""
    from backend import refresh_linkedin_session as rls

    _require_session_writer()
    digits ="".join(c for c in (body.pin or "") if c.isdigit())
    if not digits:
        return {"ok": False, "detail": "Enter the digits from LinkedIn's email."}
    with open(rls.pin_file(which), "w") as f:
        f.write(digits)
    return {"ok": True}
