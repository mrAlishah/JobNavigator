"""MT-10 · Install password -> account -> PIN; the per-browser `jn_account` cookie.

`client` (r4_support) sends the install key as X-API-Key and, by default, a signed
X-Account-Token for the owner (r4_support.ACT_AS); the browser-path tests drop it and go
through POST /api/auth/switch. QA F1 (R2-01): the token tests and the bare-X-Account tests.
"""
import base64
import json
import time
import uuid

import pytest

from backend.accounts import auth as A
from backend.accounts.context import use_account
from backend.models.db import Account, Job, Setting
from backend.tests.r4_support import API_KEY, client  # noqa: F401 (fixture)


@pytest.fixture
def owner(test_db):
    return test_db.query(Account).filter(Account.role == "owner").one()


def _member(db, name="Partner", pin=None):
    m = Account(name=name, role="member", color="#2e9e6b",
                pin_hash=A.hash_pin(pin) if pin else None)
    db.add(m)
    db.commit()
    return m


def _set_pin(db, acc, pin):
    acc.pin_hash = A.hash_pin(pin) if pin else None
    db.commit()


@pytest.fixture
def browser(client):
    """The browser path: install key, no account token."""
    client.headers.pop("X-Test-Act-As", None)
    client.cookies.clear()
    return client


def _switch(c, account_id, pin=None):
    body = {"account_id": str(account_id)}
    if pin is not None:
        body["pin"] = pin
    return c.post("/api/auth/switch", json=body)


def _me(c):
    return c.get("/api/auth/me")


def _job(db, account_id, title):
    with use_account(account_id):
        db.add(Job(external_id=f"ext-{title}", content_hash=f"h-{title}", company="Co",
                   title=title, url=f"https://x.example/{title}", source="test", status="new"))
        db.commit()


# ── PIN hashing ──────────────────────────────────────────────────────────────

def test_pin_hash_is_salted_scrypt_and_verifies():
    h1, h2 = A.hash_pin("1234"), A.hash_pin("1234")
    assert h1.startswith("scrypt$") and h1 != h2           # per-hash salt
    assert "1234" not in h1
    assert A.verify_pin("1234", h1) and A.verify_pin("1234", h2)
    assert not A.verify_pin("1235", h1)
    assert not A.verify_pin("1234", None)
    assert not A.verify_pin("12345", h1) and not A.verify_pin("12a4", h1)
    with pytest.raises(ValueError):
        A.hash_pin("123")


# ── one account: no picker ───────────────────────────────────────────────────

def test_single_account_acts_as_owner_without_cookie(browser, test_db, owner):
    _job(test_db, owner.id, "mine")
    r = _me(browser)
    assert r.status_code == 200, r.text
    assert r.json()["account"] == {"id": str(owner.id), "name": owner.name, "color": owner.color,
                                   "role": "owner", "has_pin": False}
    assert r.json()["accounts_count"] == 1
    assert browser.get("/api/jobs").status_code == 200
    assert "jn_account" not in browser.cookies


def test_single_account_bad_cookie_gets_the_picker(browser, owner):
    """QA F3 / R2-03: only a browser with no jn_account cookie at all is the one-account
    owner; a bad one gets account_required (and is cleared)."""
    browser.cookies.set("jn_account", "garbage.value")
    r = _me(browser)
    assert (r.status_code, r.json()) == (401, {"detail": "account_required"})
    assert r.headers.get("set-cookie", "").startswith("jn_account=")


def test_jn_session_cookie_alone_still_works_with_one_account(client, owner):
    client.headers.pop("X-Test-Act-As", None)
    client.headers.pop("X-API-Key", None)
    assert client.get("/api/jobs").status_code == 401                 # no key at all
    client.cookies.set("jn_session", API_KEY)
    assert client.get("/api/jobs").status_code == 200
    assert _me(client).json()["account"]["id"] == str(owner.id)


# ── two accounts: selection required ─────────────────────────────────────────

def test_two_accounts_require_a_selection(browser, test_db, owner):
    m = _member(test_db)
    r = browser.get("/api/jobs")
    assert r.status_code == 401
    assert r.json() == {"detail": "account_required"}
    assert r.headers.get("X-JN-Auth") == "account"
    # the picker and public paths still answer
    assert browser.get("/health").status_code == 200
    accounts = browser.get("/api/auth/accounts")
    assert accounts.status_code == 200
    assert accounts.json() == [
        {"id": str(owner.id), "name": owner.name, "color": owner.color, "role": "owner", "has_pin": False},
        {"id": str(m.id), "name": "Partner", "color": "#2e9e6b", "role": "member", "has_pin": False},
    ]
    # the install password still comes first
    browser.headers.pop("X-API-Key")
    assert browser.get("/api/auth/accounts").status_code == 401
    assert browser.get("/api/jobs").json() == {"detail": "API key required"}


def test_switch_without_pin_sets_a_per_browser_cookie(browser, test_db, owner):
    m = _member(test_db)
    _job(test_db, owner.id, "owner-job")
    _job(test_db, m.id, "member-job")
    r = _switch(browser, m.id)
    assert r.status_code == 200, r.text
    assert r.json()["account"]["id"] == str(m.id) and r.json()["via"] == "no_pin"
    cookie = r.headers["set-cookie"]
    assert "jn_account=" in cookie and "HttpOnly" in cookie and "SameSite=strict" in cookie.replace("Strict", "strict")
    assert "Max-Age=2592000" in cookie
    assert _me(browser).json()["account"]["id"] == str(m.id)
    listing = browser.get("/api/jobs").text
    assert "owner-job" not in listing
    # a second browser (fresh cookie jar) has not picked anything
    browser.cookies.clear()
    assert browser.get("/api/jobs").json() == {"detail": "account_required"}
    # ...and the owner (no PIN yet) is allowed without one
    assert _switch(browser, owner.id).json()["via"] == "no_pin"
    assert _me(browser).json()["account"]["role"] == "owner"
    test_db.expire_all()
    assert test_db.query(Account).filter(Account.id == m.id).one().last_active_at is not None


def test_switch_to_unknown_account_is_404(browser, test_db):
    _member(test_db)
    assert _switch(browser, uuid.uuid4()).status_code == 404
    assert browser.post("/api/auth/switch", json={"account_id": "nope"}).status_code == 404


def test_member_pin_required_wrong_and_right(browser, test_db):
    m = _member(test_db, pin="4321")
    r = _switch(browser, m.id)
    assert (r.status_code, r.json()) == (401, {"detail": "pin_required"})
    r = _switch(browser, m.id, "1111")
    assert (r.status_code, r.json()) == (401, {"detail": "wrong_pin", "tries_left": 4})
    assert "jn_account" not in browser.cookies
    r = _switch(browser, m.id, "4321")
    assert r.status_code == 200 and r.json()["via"] == "own_pin"
    acc = _me(browser).json()["account"]
    assert (acc["id"], acc["has_pin"]) == (str(m.id), True)


def test_owner_pin_required_once_set(browser, test_db, owner):
    _member(test_db)
    _set_pin(test_db, owner, "9999")
    assert _switch(browser, owner.id).json() == {"detail": "pin_required"}
    assert _switch(browser, owner.id, "1234").status_code == 401
    assert _switch(browser, owner.id, "9999").json()["via"] == "own_pin"


def test_owner_pin_covers_a_member_pin(browser, test_db, owner):
    m = _member(test_db, pin="4321")
    _set_pin(test_db, owner, "9999")
    r = _switch(browser, m.id, "9999")
    assert r.status_code == 200 and r.json()["via"] == "owner_pin"
    assert _me(browser).json()["account"]["id"] == str(m.id)
    # a member PIN does not open the owner
    assert _switch(browser, owner.id, "4321").json()["detail"] == "wrong_pin"


def test_member_without_pin_needs_none_even_when_owner_has_one(browser, test_db, owner):
    m = _member(test_db)
    _set_pin(test_db, owner, "9999")
    assert _switch(browser, m.id).status_code == 200


def test_five_wrong_pins_cool_the_account_down_for_30s(browser, test_db, monkeypatch):
    m = _member(test_db, pin="4321")
    other = _member(test_db, name="Other", pin="5555")
    clock = [1000.0]
    monkeypatch.setattr(A, "_mono", lambda: clock[0])
    for left in (4, 3, 2, 1):
        assert _switch(browser, m.id, "0000").json() == {"detail": "wrong_pin", "tries_left": left}
    r = _switch(browser, m.id, "0000")
    assert r.status_code == 429 and r.json() == {"detail": "pin_cooldown"}
    assert r.headers["Retry-After"] == "30"
    clock[0] += 10
    r = _switch(browser, m.id, "4321")                       # even the right PIN waits
    assert r.status_code == 429 and r.headers["Retry-After"] == "20"
    # per target account: another account is unaffected
    assert _switch(browser, other.id, "5555").status_code == 200
    clock[0] += 21
    assert _switch(browser, m.id, "4321").status_code == 200
    # the counter starts over after a success
    assert _switch(browser, m.id, "0000").json()["tries_left"] == 4


def test_wrong_pins_against_a_member_also_count_against_the_owner_pin(browser, test_db, owner, monkeypatch):
    m = _member(test_db, pin="4321")
    _set_pin(test_db, owner, "9999")
    clock = [1000.0]
    monkeypatch.setattr(A, "_mono", lambda: clock[0])
    for _ in range(5):
        _switch(browser, m.id, "0000")
    assert _switch(browser, owner.id, "9999").status_code == 429


# ── the cookie ───────────────────────────────────────────────────────────────

def _forge(value, **changes):
    body, sig = value.split(".")
    payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    payload.update(changes)
    new_body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":"), sort_keys=True)
                                        .encode()).rstrip(b"=").decode()
    return f"{new_body}.{sig}"


def test_tampered_cookie_is_rejected(browser, test_db, owner):
    m = _member(test_db)
    assert _switch(browser, m.id).status_code == 200
    good = browser.cookies.get("jn_account")
    for bad in (_forge(good, a=str(owner.id)),           # payload changed, old signature
                good[:-2] + ("AA" if not good.endswith("AA") else "BB"),   # signature changed
                good.split(".")[0],                       # no signature
                "x" + good):
        browser.cookies.clear()
        browser.cookies.set("jn_account", bad)
        r = browser.get("/api/jobs")
        assert (r.status_code, r.json()) == (401, {"detail": "account_required"}), bad
    browser.cookies.clear()
    browser.cookies.set("jn_account", good)
    assert _me(browser).json()["account"]["id"] == str(m.id)


def test_cookie_is_signed_with_the_install_secret(browser, test_db, owner):
    m = _member(test_db)
    _switch(browser, m.id)
    secret = test_db.query(Setting).filter(Setting.key == "account_cookie_secret",
                                           Setting.account_id.is_(None)).one().value
    assert len(secret) == 64
    payload = A.read_cookie(secret.encode(), browser.cookies.get("jn_account"))
    assert payload["a"] == str(m.id) and payload["u"] == 0
    # a cookie signed with another key is not accepted
    forged = A.sign_cookie(b"other-key", {**payload})
    browser.cookies.clear()
    browser.cookies.set("jn_account", forged)
    assert browser.get("/api/jobs").status_code == 401
    # never served, not writable
    settings = browser.get("/api/settings", headers={"X-Test-Act-As": str(owner.id)}).json()
    assert "account_cookie_secret" not in settings
    r = browser.patch("/api/settings", json={"account_cookie_secret": "x"},
                      headers={"X-Test-Act-As": str(owner.id)})
    assert r.status_code == 400


def test_expired_cookie_is_rejected(browser, test_db, monkeypatch):
    m = _member(test_db)
    _switch(browser, m.id)
    later = time.time() + A.COOKIE_MAX_AGE + 5
    monkeypatch.setattr(A, "_now", lambda: later)
    assert browser.get("/api/jobs").json() == {"detail": "account_required"}


def test_cookie_dies_with_a_pin_change_or_the_account(browser, test_db):
    m = _member(test_db)
    other = _member(test_db, name="Other")
    _switch(browser, m.id)
    _set_pin(test_db, m, "1234")
    assert browser.get("/api/jobs").json() == {"detail": "account_required"}
    browser.cookies.clear()
    _switch(browser, other.id)
    test_db.delete(other)
    test_db.commit()
    assert browser.get("/api/jobs").json() == {"detail": "account_required"}


# ── the API path: X-Account-Token (QA F1 / R2-01) ────────────────────────────

TOKEN = "X-Account-Token"


def _token(c, account_id, pin=None):
    """The extension's way to a token: POST /api/auth/verify-pin."""
    body = {"account_id": str(account_id)}
    if pin is not None:
        body["pin"] = pin
    r = c.post("/api/auth/verify-pin", json=body)
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _me_with(c, token):
    return c.get("/api/auth/me", headers={TOKEN: token})


def test_token_selects_the_account(browser, test_db, owner):
    m = _member(test_db, pin="4321")
    tm, to = _token(browser, m.id, "4321"), _token(browser, owner.id)
    assert _me_with(browser, tm).json()["account"]["id"] == str(m.id)
    assert _me_with(browser, to).json()["account"]["id"] == str(owner.id)
    # a present token decides even when the browser holds a cookie for another account
    _switch(browser, owner.id)
    assert _me(browser).json()["account"]["role"] == "owner"
    assert _me_with(browser, tm).json()["account"]["id"] == str(m.id)
    # a token carries no owner unlock
    assert _me_with(browser, tm).json()["owner_unlocked_until"] is None


def test_token_payload_kind_issued_and_one_year_expiry(browser, test_db):
    m = _member(test_db)
    before = int(time.time())
    tok = _token(browser, m.id)
    secret = test_db.query(Setting).filter(Setting.key == "account_cookie_secret",
                                           Setting.account_id.is_(None)).one().value
    p = A.read_cookie(secret.encode(), tok)
    assert p["k"] == "api" and p["a"] == str(m.id) and p["p"] == ""
    assert before <= p["i"] <= int(time.time())
    assert p["e"] == p["i"] + A.TOKEN_MAX_AGE == p["i"] + 365 * 24 * 3600
    assert "u" not in p


def test_bare_x_account_is_ignored(browser, test_db, owner):
    """A bare account id never selects an account: it acts as if it were absent."""
    m = _member(test_db, pin="4321")
    _set_pin(test_db, owner, "9999")
    # no cookie: the picker, never the named account
    r = browser.get("/api/auth/me", headers={"X-Account": str(owner.id)})
    assert (r.status_code, r.json()) == (401, {"detail": "account_required"})
    # with the member's cookie: still the member
    assert _switch(browser, m.id, "4321").status_code == 200
    me = browser.get("/api/auth/me", headers={"X-Account": str(owner.id)}).json()
    assert me["account"]["id"] == str(m.id)
    # a junk id is no longer a 401 either: it is simply not read
    assert browser.get("/api/jobs", headers={"X-Account": "not-a-uuid"}).status_code == 200


def test_bare_x_account_with_one_account_is_the_owner_path(browser, test_db, owner):
    r = browser.get("/api/auth/me", headers={"X-Account": str(uuid.uuid4())})
    assert r.status_code == 200 and r.json()["account"]["id"] == str(owner.id)


def test_invalid_token_is_401_and_never_falls_back(browser, test_db, owner, monkeypatch):
    m = _member(test_db)
    good = _token(browser, m.id)
    secret = test_db.query(Setting).filter(Setting.key == "account_cookie_secret",
                                           Setting.account_id.is_(None)).one().value
    payload = A.read_cookie(secret.encode(), good)
    bad = {
        "garbage": "garbage",
        "no_signature": good.split(".")[0],
        "signature_changed": good[:-2] + ("AA" if not good.endswith("AA") else "BB"),
        "other_secret": A.sign_cookie(b"other-key", payload),
        "unknown_account": A.sign_cookie(secret.encode(), {**payload, "a": str(uuid.uuid4())}),
        "expired": A.sign_cookie(secret.encode(), {**payload, "e": int(time.time()) - 1}),
        "non_ascii": b"a.\xe9\xe9",                       # latin-1 bytes on the wire
    }
    _switch(browser, owner.id)                               # a valid cookie is present too
    for name, value in bad.items():
        r = browser.get("/api/jobs", headers={TOKEN: value})
        assert (r.status_code, r.json()) == (401, {"detail": "invalid_account_token"}), name
    assert browser.get("/api/jobs", headers={TOKEN: good}).status_code == 200
    # the one-account owner fallback does not apply to a bad token either
    test_db.delete(m)
    test_db.commit()
    r = browser.get("/api/jobs", headers={TOKEN: good})
    assert (r.status_code, r.json()) == (401, {"detail": "invalid_account_token"})


def test_token_for_a_cannot_act_as_b(browser, test_db, owner):
    a = _member(test_db, name="A")
    b = _member(test_db, name="B", pin="5555")
    ta = _token(browser, a.id)
    for target in (b.id, owner.id):
        r = _me_with(browser, _forge(ta, a=str(target)))
        assert (r.status_code, r.json()) == (401, {"detail": "invalid_account_token"})
    assert _me_with(browser, ta).json()["account"]["id"] == str(a.id)


def test_pin_change_invalidates_the_token(browser, test_db, owner):
    m = _member(test_db)
    t0 = _token(browser, m.id)                               # issued without a PIN
    _set_pin(test_db, m, "1234")                             # PIN set
    assert _me_with(browser, t0).status_code == 401
    t1 = _token(browser, m.id, "1234")
    _set_pin(test_db, m, "4321")                             # PIN changed
    assert _me_with(browser, t1).json() == {"detail": "invalid_account_token"}
    t2 = _token(browser, m.id, "4321")
    _set_pin(test_db, m, None)                               # PIN removed
    assert _me_with(browser, t2).status_code == 401
    assert _me_with(browser, _token(browser, m.id)).status_code == 200


def test_owner_pin_reset_through_the_api_invalidates_the_members_token(browser, test_db, owner):
    m = _member(test_db, pin="4321")
    tm = _token(browser, m.id, "4321")
    browser.headers["X-Test-Act-As"] = str(owner.id)
    assert browser.patch(f"/api/accounts/{m.id}", json={"pin": "1111"}).status_code == 200
    browser.headers.pop("X-Test-Act-As")
    assert _me_with(browser, tm).json() == {"detail": "invalid_account_token"}


def test_cookie_value_is_not_a_token_and_a_token_is_not_a_cookie(browser, test_db, owner):
    m = _member(test_db)
    assert _switch(browser, m.id).status_code == 200
    cookie = browser.cookies.get("jn_account")
    browser.cookies.clear()
    r = _me_with(browser, cookie)
    assert (r.status_code, r.json()) == (401, {"detail": "invalid_account_token"})
    token = _token(browser, m.id)
    browser.cookies.set("jn_account", token)
    assert _me(browser).json() == {"detail": "account_required"}
    # a cookie issued before QA F1 (no "k") still works as a cookie, never as a token
    secret = test_db.query(Setting).filter(Setting.key == "account_cookie_secret",
                                           Setting.account_id.is_(None)).one().value
    legacy = A.sign_cookie(secret.encode(), {"a": str(m.id), "p": "", "e": int(time.time()) + 60, "u": 0})
    browser.cookies.clear()
    browser.cookies.set("jn_account", legacy)
    assert _me(browser).json()["account"]["id"] == str(m.id)
    browser.cookies.clear()
    assert _me_with(browser, legacy).status_code == 401


def test_token_works_with_the_session_cookie_as_install_password(client, test_db):
    m = _member(test_db)
    tok = _token(client, m.id)
    client.headers.pop("X-API-Key")
    client.headers.pop("X-Test-Act-As", None)
    client.cookies.set("jn_session", API_KEY)
    assert _me_with(client, tok).json()["account"]["id"] == str(m.id)
    # the install password still comes first
    client.cookies.clear()
    assert _me_with(client, tok).json() == {"detail": "API key required"}


def test_api_token_endpoint_own_account_only(browser, test_db, owner):
    m = _member(test_db, pin="4321")
    _switch(browser, m.id, "4321")
    r = browser.post(f"/api/accounts/{m.id}/api-token")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["account"]["id"] == str(m.id) and body["expires_in"] == A.TOKEN_MAX_AGE
    browser.cookies.clear()
    assert _me_with(browser, body["token"]).json()["account"]["id"] == str(m.id)
    # a member cannot mint the owner's
    _switch(browser, m.id, "4321")
    assert browser.post(f"/api/accounts/{owner.id}/api-token").json() == {"detail": "own_account_only"}
    # ...and the owner cannot mint a member's (switch to it first)
    browser.cookies.clear()
    _switch(browser, owner.id)
    r = browser.post(f"/api/accounts/{m.id}/api-token")
    assert (r.status_code, r.json()) == (403, {"detail": "own_account_only"})
    assert browser.post(f"/api/accounts/{uuid.uuid4()}/api-token").status_code == 404
    own = browser.post(f"/api/accounts/{owner.id}/api-token").json()["token"]
    browser.cookies.clear()
    assert _me_with(browser, own).json()["account"]["role"] == "owner"


def test_api_token_endpoint_with_one_account(browser, owner):
    r = browser.post(f"/api/accounts/{owner.id}/api-token")
    assert r.status_code == 200 and _me_with(browser, r.json()["token"]).json()["account"]["id"] == str(owner.id)


# ── member restrictions ──────────────────────────────────────────────────────

INSTALL_JOBS = [("POST", "/api/db/backup", None), ("POST", "/api/h1b/refresh", None)]
# MT-16: the LinkedIn session refresh and its PIN drop act on the caller's own session files
ACCOUNT_JOBS = [("POST", "/api/linkedin/session/refresh", None),
                ("POST", "/api/linkedin/session/pin", {"pin": "123456"})]


@pytest.fixture
def no_launch(monkeypatch, tmp_path):
    calls = []

    def _launch(job_type, *a, **k):
        calls.append(job_type)
        return "run-1"
    import backend.main as main_mod
    from backend.api import routes_linkedin
    from backend.accounts import credentials
    monkeypatch.setattr(main_mod, "launch_background", _launch)
    monkeypatch.setattr(routes_linkedin, "launch_background", _launch)
    monkeypatch.setattr(credentials, "LI_PIN_OWNER", str(tmp_path / "li_pin.txt"))
    return calls


@pytest.mark.parametrize("method,path,body", INSTALL_JOBS)
def test_member_cannot_start_install_level_jobs(browser, test_db, no_launch, method, path, body):
    m = _member(test_db)
    _switch(browser, m.id)
    r = browser.request(method, path, json=body)
    assert (r.status_code, r.json()) == (403, {"detail": "owner_only"})
    # also through the API path
    r = browser.request(method, path, json=body, headers={"X-Test-Act-As": str(m.id)})
    assert r.status_code == 403
    assert no_launch == []


@pytest.mark.parametrize("method,path,body", INSTALL_JOBS + ACCOUNT_JOBS)
def test_owner_can_start_install_level_jobs(client, test_db, no_launch, method, path, body):
    _member(test_db)
    r = client.request(method, path, json=body)
    assert r.status_code in (200, 202), r.text


@pytest.mark.parametrize("method,path,body", ACCOUNT_JOBS)
def test_member_can_start_its_own_session_jobs(browser, test_db, no_launch, method, path, body):
    m = _member(test_db)
    _switch(browser, m.id)
    r = browser.request(method, path, json=body)
    assert r.status_code in (200, 202), r.text


def test_member_owner_only_settings_write_is_still_403(browser, test_db):
    m = _member(test_db)
    _switch(browser, m.id)
    r = browser.patch("/api/settings", json={"proxy_url": "socks5://x"})
    assert r.status_code == 403


# ── owner unlock ─────────────────────────────────────────────────────────────

def test_verify_owner_pin_unlocks_for_15_minutes(browser, test_db, owner):
    m = _member(test_db)
    _set_pin(test_db, owner, "9999")
    _switch(browser, m.id)
    assert _me(browser).json()["owner_unlocked_until"] is None
    r = browser.post("/api/auth/verify-owner-pin", json={"pin": "1111"})
    assert (r.status_code, r.json()) == (401, {"detail": "wrong_pin", "tries_left": 4})
    assert browser.post("/api/auth/verify-owner-pin", json={}).json() == {"detail": "pin_required"}
    before = time.time()
    r = browser.post("/api/auth/verify-owner-pin", json={"pin": "9999"})
    assert r.status_code == 200 and r.json()["ok"] is True
    me = _me(browser).json()
    assert me["account"]["id"] == str(m.id)                  # still the member
    from datetime import datetime
    until = datetime.fromisoformat(me["owner_unlocked_until"]).timestamp()
    assert before + 15 * 60 - 2 <= until <= time.time() + 15 * 60 + 1
    assert me["owner_unlocked_until"] == r.json()["unlocked_until"]


def test_verify_owner_pin_without_an_owner_pin_is_ok(browser, test_db):
    m = _member(test_db)
    _switch(browser, m.id)
    r = browser.post("/api/auth/verify-owner-pin", json={})
    assert r.status_code == 200 and r.json()["unlocked_until"]


def test_no_pins_anywhere_create_switch_unlock(browser, test_db, owner):
    """QA F2 / D3: an owner without a PIN adds a PIN-less member; both open instantly from
    the picker, /auth/me says the owner has no PIN, and the member's owner unlock is a plain
    Unlock (no PIN in the body)."""
    assert not owner.pin_hash
    r = browser.post("/api/accounts", json={"name": "Alex"})          # one account: acts as owner
    assert r.status_code == 201, r.text
    member_id = r.json()["account"]["id"]
    assert r.json()["account"]["has_pin"] is False
    picker = {a["id"]: a for a in browser.get("/api/auth/accounts").json()}
    assert picker[str(owner.id)]["has_pin"] is False and picker[member_id]["has_pin"] is False
    browser.cookies.clear()
    r = _switch(browser, member_id)
    assert r.status_code == 200 and r.json()["via"] == "no_pin"
    me = _me(browser).json()
    assert me["account"]["id"] == member_id and me["owner_has_pin"] is False
    assert browser.patch("/api/settings", json={"proxy_url": "socks5://x"}).status_code == 403
    r = browser.post("/api/auth/verify-owner-pin", json={})
    assert r.status_code == 200 and r.json()["unlocked_until"]
    assert _me(browser).json()["owner_unlocked_until"]
    r = _switch(browser, owner.id)                                     # owner: instant too
    assert r.status_code == 200 and r.json()["via"] == "no_pin"
    assert _me(browser).json()["account"]["role"] == "owner"


def test_me_reports_owner_has_pin(browser, test_db, owner):
    m = _member(test_db)
    _set_pin(test_db, owner, "9999")
    _switch(browser, m.id)
    assert _me(browser).json()["owner_has_pin"] is True


def test_verify_owner_pin_needs_an_account(browser, test_db, owner):
    _member(test_db)
    _set_pin(test_db, owner, "9999")
    r = browser.post("/api/auth/verify-owner-pin", json={"pin": "9999"})
    assert (r.status_code, r.json()) == (401, {"detail": "account_required"})


def test_logout_clears_both_cookies(browser, test_db):
    m = _member(test_db)
    _switch(browser, m.id)
    r = browser.post("/api/auth/logout")
    cookies = r.headers.get_list("set-cookie")
    assert any(c.startswith("jn_session=") for c in cookies)
    assert any(c.startswith("jn_account=") for c in cookies)
