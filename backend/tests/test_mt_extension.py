"""MT-18 · Extension: POST /api/auth/verify-pin and account routing of the persona calls.

`client` (r4_support) sends the install key and, by default, a token for the owner
(r4_support.ACT_AS). QA F1: verify-pin issues the account's X-Account-Token, which the
extension stores and sends instead of a bare account id.
"""
import pytest

from backend.accounts import auth as A
from backend.accounts.context import use_account
from backend.models.db import Account, Persona
from backend.tests.r4_support import client  # noqa: F401 (fixture)

TOKEN = "X-Account-Token"


@pytest.fixture
def owner(test_db):
    return test_db.query(Account).filter(Account.role == "owner").one()


def _member(db, name="Partner", pin=None):
    m = Account(name=name, role="member", color="#2e9e6b", pin_hash=A.hash_pin(pin) if pin else None)
    db.add(m)
    db.commit()
    return m


def _verify(c, account_id, pin=None):
    body = {"account_id": str(account_id)}
    if pin is not None:
        body["pin"] = pin
    return c.post("/api/auth/verify-pin", json=body)


def test_verify_pin_ok_wrong_and_missing(client, test_db):
    m = _member(test_db, pin="4321")
    r = _verify(client, m.id)
    assert (r.status_code, r.json()) == (401, {"detail": "pin_required"})     # a missing PIN is not a miss
    r = _verify(client, m.id, "1111")
    assert (r.status_code, r.json()) == (401, {"detail": "wrong_pin", "tries_left": 4})
    r = _verify(client, m.id, "4321")
    assert r.status_code == 200
    assert r.json()["ok"] is True and r.json()["via"] == "own_pin" and r.json()["account"]["id"] == str(m.id)


def test_verify_pin_account_without_pin_and_unknown(client, test_db):
    m = _member(test_db)
    assert _verify(client, m.id).json()["via"] == "no_pin"
    assert _verify(client, "00000000-0000-0000-0000-000000000000").status_code == 404
    assert client.post("/api/auth/verify-pin", json={"account_id": "nope"}).status_code == 404


def test_verify_pin_needs_the_install_key(client, test_db):
    m = _member(test_db, pin="4321")
    client.headers.pop("X-API-Key")
    assert _verify(client, m.id, "4321").status_code == 401


def test_verify_pin_works_without_an_account_selected(client, test_db):
    """The extension verifies before it has stored an account: no token, two accounts."""
    m = _member(test_db, pin="4321")
    client.headers.pop("X-Test-Act-As", None)
    client.cookies.clear()
    assert client.get("/api/jobs").json() == {"detail": "account_required"}
    assert _verify(client, m.id, "4321").status_code == 200


def test_verify_pin_cooldown_after_five_wrong(client, test_db, monkeypatch):
    m = _member(test_db, pin="4321")
    clock = [1000.0]
    monkeypatch.setattr(A, "_mono", lambda: clock[0])
    for left in (4, 3, 2, 1):
        assert _verify(client, m.id, "0000").json() == {"detail": "wrong_pin", "tries_left": left}
    r = _verify(client, m.id, "0000")
    assert r.status_code == 429 and r.json() == {"detail": "pin_cooldown"} and r.headers["Retry-After"] == "30"
    assert _verify(client, m.id, "4321").status_code == 429          # even the right PIN waits
    # the counters are shared with /api/auth/switch
    assert client.post("/api/auth/switch", json={"account_id": str(m.id), "pin": "4321"}).status_code == 429
    clock[0] += 31
    assert _verify(client, m.id, "4321").status_code == 200


def test_verify_pin_owner_pin_covers_a_member(client, test_db, owner):
    m = _member(test_db, pin="4321")
    owner.pin_hash = A.hash_pin("9999")
    test_db.commit()
    r = _verify(client, m.id, "9999")
    assert r.status_code == 200 and r.json()["via"] == "owner_pin"
    assert _verify(client, owner.id, "4321").json()["detail"] == "wrong_pin"   # not the other way round


def test_verify_pin_sets_no_cookie_and_changes_nothing(client, test_db, owner):
    m = _member(test_db, pin="4321")
    client.cookies.clear()
    r = _verify(client, m.id, "4321")
    assert r.status_code == 200
    assert "set-cookie" not in r.headers
    assert "jn_account" not in client.cookies
    test_db.expire_all()
    assert test_db.query(Account).filter(Account.id == m.id).one().last_active_at is None


def test_account_token_routes_autofill_and_persona_to_that_accounts_persona(client, test_db, owner):
    m = _member(test_db)
    with use_account(owner.id):
        p = test_db.query(Persona).filter(Persona.account_id == owner.id).first() or Persona(account_id=owner.id)
        p.contact = {"first_name": "Olga"}
        test_db.add(p)
        test_db.commit()
    with use_account(m.id):
        test_db.add(Persona(account_id=m.id, contact={"first_name": "Mia"}, qa_bank=[]))
        test_db.commit()

    # what the extension does: verify-pin once, then send the stored token
    client.headers.pop("X-Test-Act-As", None)
    tm = {TOKEN: _verify(client, m.id).json()["token"]}
    to = {TOKEN: _verify(client, owner.id).json()["token"]}
    cfg = client.get("/api/autofill/config", headers=tm)
    assert cfg.status_code == 200, cfg.text
    assert "Mia" in str(cfg.json()["answers"]) and "Olga" not in str(cfg.json()["answers"])
    own = client.get("/api/autofill/config", headers=to).json()
    assert "Olga" in str(own["answers"]) and "Mia" not in str(own["answers"])
    assert client.get("/api/persona", headers=tm).json()["contact"]["first_name"] == "Mia"

    # the Q&A bank save lands in the member's persona only
    r = client.post("/api/persona/qa-bank", json={"question": "Why us?", "answer": "Because."},
                    headers=tm)
    assert r.status_code in (200, 201), r.text
    test_db.expire_all()
    with use_account(m.id):
        mine = test_db.query(Persona).filter(Persona.account_id == m.id).one()
    with use_account(owner.id):
        theirs = test_db.query(Persona).filter(Persona.account_id == owner.id).one()
    assert any("Why us?" in str(x) for x in (mine.qa_bank or []))
    assert not any("Why us?" in str(x) for x in (theirs.qa_bank or []))

    # a removed account's token is invalid_account_token (the popup's "pick again")
    test_db.delete(m)
    test_db.commit()
    r = client.get("/api/autofill/config", headers=tm)
    assert (r.status_code, r.json()) == (401, {"detail": "invalid_account_token"})
    # and a bare X-Account (the pre-F1 extension) is ignored: one account left = the owner
    r = client.get("/api/persona", headers={"X-Account": str(m.id)})
    assert r.status_code == 200 and r.json()["contact"]["first_name"] == "Olga"


def test_verify_pin_issues_a_token_for_that_account(client, test_db, owner):
    """PIN-less accounts get one without a PIN (D3); a PIN'd one only after its PIN or the
    owner's; a failed check returns no token."""
    free = _member(test_db, name="Free")
    locked = _member(test_db, name="Locked", pin="4321")
    owner.pin_hash = A.hash_pin("9999")
    test_db.commit()
    client.headers.pop("X-Test-Act-As", None)
    r = _verify(client, free.id)
    assert r.status_code == 200 and r.json()["via"] == "no_pin"
    assert client.get("/api/auth/me", headers={TOKEN: r.json()["token"]}).json()["account"]["id"] == str(free.id)
    for bad in (None, "0000"):
        r = _verify(client, locked.id, bad)
        assert r.status_code == 401 and "token" not in r.json()
    for pin in ("4321", "9999"):                             # own PIN, or the owner's
        tok = _verify(client, locked.id, pin).json()["token"]
        assert client.get("/api/auth/me", headers={TOKEN: tok}).json()["account"]["id"] == str(locked.id)
    assert "token" not in _verify(client, owner.id).json()  # the owner's needs its PIN
    tok = _verify(client, owner.id, "9999").json()["token"]
    assert client.get("/api/auth/me", headers={TOKEN: tok}).json()["account"]["role"] == "owner"


def test_stale_token_on_verify_pin_and_accounts_is_401(client, test_db):
    """The popup calls these with the install key only; a stale token sent anyway is a
    clear 401 rather than a silent fallback."""
    m = _member(test_db)
    client.headers.pop("X-Test-Act-As", None)
    tok = _verify(client, m.id).json()["token"]
    m.pin_hash = A.hash_pin("1234")
    test_db.commit()
    r = client.get("/api/auth/accounts", headers={TOKEN: tok})
    assert (r.status_code, r.json()) == (401, {"detail": "invalid_account_token"})
    assert client.get("/api/auth/accounts").status_code == 200
