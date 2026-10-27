"""Telemetry for the app: the choice (level in effect, whether JN_TELEMETRY locks it, whether the
owner has been asked) and the relay for browser events, which the backend checks again and
forwards to Umami only at `full`. The choice itself is the owner-only setting `telemetry_level`
(PATCH /api/settings)."""
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from backend.models.db import get_db

router = APIRouter(prefix="/telemetry", tags=["telemetry"])


@router.get("")
def telemetry_state(db: Session = Depends(get_db)):
    from backend.accounts.settings import is_owner_caller
    from backend.telemetry import level as L
    return {
        "level": L.effective_level(db),
        "locked": L.env_level() is not None,
        "asked": L.chosen_level(db) != L.UNSET,
        "owner": is_owner_caller(db),
    }


class BrowserEvent(BaseModel):
    name: str
    data: Optional[dict] = None
    url: Optional[str] = None


@router.post("/event", status_code=202)
async def browser_event(ev: BrowserEvent):
    """One browser event (or `page_view`); dropped unless the install is at `full`."""
    from backend.telemetry.umami import relay
    return {"sent": await relay(ev.name, ev.data, ev.url)}
