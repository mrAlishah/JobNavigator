"""Loading rows the current account owns (MT-5).

The MT-2 listener filters every ORM query by the current account, so a row of another
account is simply "not found". These helpers make that the one way a route resolves an
id it was handed in a path, query string or body: `get_owned_or_404` answers 404 for
another account's row, a missing row and a malformed id alike, so a body can never point
a new or updated row at someone else's job, résumé, letter or application.

`get_persona` replaces the old "Persona id 1" singleton: one Persona per account, created
on first access.
"""
import uuid
from typing import Optional

from fastapi import HTTPException

_NOT_FOUND = {
    "Job": "Job not found",
    "Resume": "Resume not found",
    "CoverLetter": "Cover letter not found",
    "Application": "Application not found",
    "Interview": "Interview not found",
    "Company": "Company not found",
    "Search": "Search not found",
}


def parse_uuid(value) -> Optional[uuid.UUID]:
    """`value` as a UUID, or None when it is not one."""
    if value is None or isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value).strip())
    except (ValueError, AttributeError, TypeError):
        return None


def get_owned(db, model, row_id):
    """The current account's `model` row with this id, or None (missing, malformed id, or
    another account's row). Always a SELECT, never an identity-map hit, so the account
    criteria apply."""
    parsed = parse_uuid(row_id)
    if parsed is None:
        return None
    return db.query(model).filter(model.id == parsed).first()


def get_owned_or_404(db, model, row_id, detail: Optional[str] = None):
    """`get_owned`, or HTTP 404."""
    row = get_owned(db, model, row_id)
    if row is None:
        raise HTTPException(status_code=404, detail=detail or _NOT_FOUND.get(model.__name__, "Not found"))
    return row


def owned_id_or_404(db, model, row_id, detail: Optional[str] = None):
    """For an optional foreign key taken from a request body: None/"" stays None, anything
    else must be an id the current account owns (404 otherwise). Returns the UUID."""
    if row_id is None or (isinstance(row_id, str) and not row_id.strip()):
        return None
    return get_owned_or_404(db, model, row_id, detail).id


# ── Persona: one per account ─────────────────────────────────────────────────

def _empty_persona_fields() -> dict:
    return dict(contact={}, work_auth={}, demographics={}, compensation={},
                preferences={}, resume_content={}, qa_bank=[])


def get_persona(db, create: bool = True):
    """The current account's Persona; with `create`, an empty one is added (and committed)
    on first access, as seed_persona used to do for id 1. Two first accesses racing hit
    UNIQUE(account_id): the loser re-reads the winner's row."""
    from sqlalchemy.exc import IntegrityError
    from backend.models.db import Persona

    p = db.query(Persona).order_by(Persona.id).first()
    if p is not None or not create:
        return p
    p = Persona(**_empty_persona_fields())
    db.add(p)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return db.query(Persona).order_by(Persona.id).first()
    db.refresh(p)
    return p
