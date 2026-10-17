"""MT-19 · Gmail per account.

A is the owner, B a member. No live Google: the token endpoint and the Gmail API are a
recorder (`google`). Covers the connect URL and its signed state, the callback (valid,
tampered, expired, reused, Google error, failed exchange), per-account storage, disconnect,
status (incl. expired), the owner's .env fallback (members never get it), and the poller:
each account polls with its own token and keeps its own processed ids; an invalid_grant
marks only that account expired.
"""
import json
from urllib.parse import parse_qs, urlparse

import pytest

import backend.email_monitor.gmail_auth as ga
import backend.email_monitor.gmail_client as gc
from backend.accounts.context import unscoped, use_account
from backend.models.db import Account, JobRun, Setting
from backend.tests.r4_support import client  # noqa: F401 (fixture)


@pytest.fixture
def accounts(test_db):
    a = test_db.query(Account).filter(Account.role == "owner").one()
    b = Account(name="Partner", role="member")
    test_db.add(b)
    test_db.commit()
    return a.id, b.id


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    ga.reset_pending()
    monkeypatch.setattr(gc, "GMAIL_CLIENT_ID", "cid.apps.googleusercontent.com")
    monkeypatch.setattr(gc, "GMAIL_CLIENT_SECRET", "csecret")
    monkeypatch.setattr(gc, "GMAIL_REFRESH_TOKEN", "")
    import backend.job_monitor as jm
    jm._running.clear()
    yield
    ga.reset_pending()
    jm._running.clear()


class _Resp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


@pytest.fixture
def google(monkeypatch):
    """Fake Google. `refresh` maps refresh token → access token (None = invalid_grant);
    `mail` maps access token → message ids; `exchange` is the code-exchange answer."""
    from unittest.mock import AsyncMock, MagicMock
    import httpx
    g = {"refresh": {}, "mail": {}, "exchange": (200, {"refresh_token": "new-refresh"}),
         "posts": [], "gets": []}

    async def post(url, data=None, **kw):
        g["posts"].append((url, dict(data or {})))
        if data.get("grant_type") == "authorization_code":
            return _Resp(*g["exchange"])
        at = g["refresh"].get(data.get("refresh_token"))
        if at is None:
            return _Resp(400, {"error": "invalid_grant", "error_description": "Token has been expired or revoked."})
        return _Resp(200, {"access_token": at})

    async def get(url, params=None, headers=None, **kw):
        at = (headers or {}).get("Authorization", "").replace("Bearer ", "")
        g["gets"].append((url, at))
        if url.endswith("/users/me/messages"):
            return _Resp(200, {"messages": [{"id": m} for m in g["mail"].get(at, [])]})
        return _Resp(200, {"payload": {"headers": [{"name": "From", "value": "r@corp.com"},
                                                   {"name": "Subject", "value": "Out of office"}]}})

    c = MagicMock()
    c.post = AsyncMock(side_effect=post)
    c.get = AsyncMock(side_effect=get)
    c.__aenter__ = AsyncMock(return_value=c)
    c.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: c)
    # every message is an auto-reply: processed, no LLM, no application change
    monkeypatch.setattr(gc, "classify_email", lambda s, b: {"classification": "auto_reply", "confidence": 1.0})
    return g


def _row(db, key, account_id=None):
    with unscoped():
        db.expire_all()
        q = db.query(Setting).filter(Setting.key == key)
        q = q.filter(Setting.account_id.is_(None)) if account_id is None else q.filter(Setting.account_id == account_id)
        return q.first()


def _put(db, key, value, account_id=None):
    from backend.accounts.settings import put_setting
    put_setting(db, key, value, account_id=account_id)
    db.commit()


def _connect(client, account=None, headers=None):
    if account is not None:
        client.headers["X-Test-Act-As"] = str(account)
    r = client.get("/api/gmail/connect", headers=headers or {})
    assert r.status_code == 200, r.text
    url = r.json()["url"]
    return url, {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


def _callback(client, **params):
    client.headers.pop("X-API-Key", None)          # Google's redirect carries no key
    client.headers.pop("X-Test-Act-As", None)
    return client.get("/api/gmail/callback", params=params, follow_redirects=False)


# ── connect URL and state ────────────────────────────────────────────────────

def test_connect_url_asks_google_for_offline_read_only_consent(client, test_db, accounts):
    a, _b = accounts
    url, q = _connect(client)
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert q["client_id"] == "cid.apps.googleusercontent.com"
    assert q["scope"] == "https://www.googleapis.com/auth/gmail.readonly"
    assert q["access_type"] == "offline" and q["prompt"] == "consent" and q["response_type"] == "code"
    assert q["redirect_uri"] == "http://testserver/api/gmail/callback"
    assert "csecret" not in url
    payload = ga.read_state(test_db, q["state"])
    assert payload["a"] == str(a) and payload["r"] == q["redirect_uri"]
    assert payload["e"] > ga._now() and payload["e"] <= ga._now() + ga.STATE_TTL + 1


def test_connect_redirect_uri_follows_the_proxy_headers(client, test_db, accounts):
    _url, q = _connect(client, headers={"X-Forwarded-Proto": "https", "X-Forwarded-Host": "jobs.example.org"})
    assert q["redirect_uri"] == "https://jobs.example.org/api/gmail/callback"


def test_connect_state_is_bound_to_the_member(client, test_db, accounts):
    _a, b = accounts
    _url, q = _connect(client, account=b)
    assert ga.read_state(test_db, q["state"])["a"] == str(b)


def test_connect_without_the_env_client_is_503(client, test_db, accounts, monkeypatch):
    monkeypatch.setattr(gc, "GMAIL_CLIENT_SECRET", "")
    r = client.get("/api/gmail/connect")
    assert r.status_code == 503 and r.json()["detail"] == "gmail_not_configured"


def test_state_signature_is_purpose_bound(test_db, accounts):
    """A jn_account cookie signed with the install secret is not a valid state."""
    from backend.accounts.auth import cookie_secret, sign_cookie
    forged = sign_cookie(cookie_secret(test_db), {"k": "gmail", "a": str(accounts[1]), "n": "x",
                                                  "e": int(ga._now() + 60), "r": "http://x/cb"})
    with pytest.raises(ga.StateError) as e:
        ga.read_state(test_db, forged)
    assert e.value.reason == "invalid_state"


# ── callback ─────────────────────────────────────────────────────────────────

def test_callback_stores_the_members_token_in_its_own_row(client, test_db, accounts, google):
    a, b = accounts
    _put(test_db, "gmail_refresh_token", "owner-tok")
    _url, q = _connect(client, account=b)
    r = _callback(client, code="the-code", state=q["state"])
    assert r.status_code == 303
    assert r.headers["location"] == "/settings?gmail=connected#gmail"
    (url, data), = google["posts"]
    assert url == "https://oauth2.googleapis.com/token"
    assert data == {"code": "the-code", "client_id": "cid.apps.googleusercontent.com", "client_secret": "csecret",
                    "redirect_uri": "http://testserver/api/gmail/callback", "grant_type": "authorization_code"}
    assert _row(test_db, "gmail_refresh_token", b).value == "new-refresh"
    assert _row(test_db, "gmail_refresh_token").value == "owner-tok"      # owner untouched


def test_callback_stores_the_owners_token_in_the_install_row(client, test_db, accounts, google):
    a, b = accounts
    _url, q = _connect(client)
    r = _callback(client, code="c", state=q["state"])
    assert r.headers["location"] == "/settings?gmail=connected#gmail"
    assert _row(test_db, "gmail_refresh_token").value == "new-refresh"
    assert _row(test_db, "gmail_refresh_token", b) is None


def test_callback_clears_an_expired_marker(client, test_db, accounts, google):
    _a, b = accounts
    _put(test_db, "gmail_refresh_token", "old", b)
    _put(test_db, ga.STATUS_KEY, f"expired:{ga.fingerprint('old')}", b)
    _url, q = _connect(client, account=b)
    _callback(client, code="c", state=q["state"])
    assert _row(test_db, ga.STATUS_KEY, b).value == ""


def test_callback_with_a_tampered_state_stores_nothing(client, test_db, accounts, google):
    _a, b = accounts
    _url, q = _connect(client, account=b)
    body, sig = q["state"].split(".")
    tampered = body[:-2] + ("AA" if body[-2:] != "AA" else "BB") + "." + sig
    r = _callback(client, code="c", state=tampered)
    assert r.status_code == 303 and r.headers["location"] == "/settings?gmail=error&reason=invalid_state#gmail"
    assert google["posts"] == [] and _row(test_db, "gmail_refresh_token", b) is None
    r = _callback(client, code="c")
    assert r.headers["location"].endswith("reason=invalid_state#gmail")


def test_callback_with_an_expired_state_is_refused(client, test_db, accounts, google, monkeypatch):
    _a, b = accounts
    _url, q = _connect(client, account=b)
    later = ga._now() + ga.STATE_TTL + 5
    monkeypatch.setattr(ga, "_now", lambda: later)
    r = _callback(client, code="c", state=q["state"])
    assert r.headers["location"] == "/settings?gmail=error&reason=state_expired#gmail"
    assert google["posts"] == [] and _row(test_db, "gmail_refresh_token", b) is None


def test_callback_state_is_single_use(client, test_db, accounts, google):
    _a, b = accounts
    _url, q = _connect(client, account=b)
    assert _callback(client, code="c", state=q["state"]).headers["location"].endswith("gmail=connected#gmail")
    r = _callback(client, code="c2", state=q["state"])
    assert r.headers["location"] == "/settings?gmail=error&reason=state_unknown#gmail"
    assert len(google["posts"]) == 1


def test_callback_reports_googles_error(client, test_db, accounts, google):
    _a, b = accounts
    _url, q = _connect(client, account=b)
    r = _callback(client, error="access_denied", state=q["state"])
    assert r.headers["location"] == "/settings?gmail=error&reason=access_denied#gmail"
    assert google["posts"] == [] and _row(test_db, "gmail_refresh_token", b) is None


@pytest.mark.parametrize("answer,reason", [
    ((400, {"error": "invalid_grant"}), "exchange_failed"),
    ((200, {"access_token": "at-only"}), "no_refresh_token"),
])
def test_callback_failed_exchange_stores_nothing(client, test_db, accounts, google, answer, reason):
    _a, b = accounts
    google["exchange"] = answer
    _url, q = _connect(client, account=b)
    r = _callback(client, code="c", state=q["state"])
    assert r.headers["location"] == f"/settings?gmail=error&reason={reason}#gmail"
    assert _row(test_db, "gmail_refresh_token", b) is None


# ── status, disconnect, fallback ─────────────────────────────────────────────

def test_status_per_account(client, test_db, accounts):
    a, b = accounts
    assert client.get("/api/gmail/status").json() == {"status": "none", "source": None, "configured": True}
    _put(test_db, "gmail_refresh_token", "tok-b", b)
    client.headers["X-Test-Act-As"] = str(b)
    assert client.get("/api/gmail/status").json() == {"status": "connected", "source": "account", "configured": True}
    _put(test_db, ga.STATUS_KEY, f"expired:{ga.fingerprint('tok-b')}", b)
    assert client.get("/api/gmail/status").json()["status"] == "expired"
    # the marker belongs to that token only: a new token is connected again
    _put(test_db, "gmail_refresh_token", "tok-b2", b)
    assert client.get("/api/gmail/status").json()["status"] == "connected"
    client.headers["X-Test-Act-As"] = str(a)
    assert client.get("/api/gmail/status").json()["status"] == "none"


def test_owner_falls_back_to_the_env_token_members_never_do(client, test_db, accounts, monkeypatch):
    a, b = accounts
    monkeypatch.setattr(gc, "GMAIL_REFRESH_TOKEN", "env-tok")
    assert client.get("/api/gmail/status").json() == {"status": "connected", "source": "env", "configured": True}
    with use_account(a):
        assert ga.resolve_token(test_db) == ("env-tok", "env")
    with use_account(b):
        assert ga.resolve_token(test_db) == ("", None)
    client.headers["X-Test-Act-As"] = str(b)
    assert client.get("/api/gmail/status").json()["status"] == "none"
    # a stored owner token wins over .env
    _put(test_db, "gmail_refresh_token", "owner-tok")
    with use_account(a):
        assert ga.resolve_token(test_db) == ("owner-tok", "account")


def test_disconnect_clears_only_the_callers_token(client, test_db, accounts):
    a, b = accounts
    _put(test_db, "gmail_refresh_token", "owner-tok")
    _put(test_db, "gmail_refresh_token", "tok-b", b)
    client.headers["X-Test-Act-As"] = str(b)
    r = client.post("/api/gmail/disconnect")
    assert r.status_code == 200 and r.json()["status"] == "none"
    assert _row(test_db, "gmail_refresh_token", b).value == ""
    assert _row(test_db, "gmail_refresh_token").value == "owner-tok"


def test_owner_disconnect_falls_back_to_env(client, test_db, accounts, monkeypatch):
    monkeypatch.setattr(gc, "GMAIL_REFRESH_TOKEN", "env-tok")
    _put(test_db, "gmail_refresh_token", "owner-tok")
    r = client.post("/api/gmail/disconnect")
    assert r.json() == {"status": "connected", "source": "env", "configured": True}
    assert _row(test_db, "gmail_refresh_token").value == ""


def test_token_is_redacted_and_the_status_marker_never_served(client, test_db, accounts):
    _put(test_db, "gmail_refresh_token", "owner-tok")
    _put(test_db, ga.STATUS_KEY, "expired:abc")
    data = client.get("/api/settings").json()
    assert data["gmail_refresh_token"] == "•" * 6
    assert ga.STATUS_KEY not in data
    r = client.patch("/api/settings", json={ga.STATUS_KEY: ""})
    assert r.status_code == 400


def test_export_redacts_the_members_token(test_db, accounts):
    import io
    import zipfile
    from backend.accounts import admin
    a, b = accounts
    _put(test_db, "gmail_refresh_token", "tok-b", b)
    _put(test_db, ga.STATUS_KEY, "expired:x", b)
    buf = admin.build_export(test_db, b, a)
    raw = buf.read()
    buf.close()
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        text = z.read("settings.json").decode()
    assert "tok-b" not in text and ga.STATUS_KEY not in text


# ── poller ───────────────────────────────────────────────────────────────────

def test_gmail_account_ids_are_the_owner_plus_connected_members(test_db, accounts):
    from backend.accounts.background import gmail_account_ids
    a, b = accounts
    c = Account(name="Third", role="member")
    d = Account(name="Fourth", role="member")
    test_db.add_all([c, d])
    test_db.commit()
    assert gmail_account_ids() == [a]                       # nobody connected: the owner, as before
    _put(test_db, "gmail_refresh_token", "tok-b", b)
    _put(test_db, "gmail_refresh_token", "tok-c", c.id)
    _put(test_db, ga.STATUS_KEY, f"expired:{ga.fingerprint('tok-c')}", c.id)
    _put(test_db, "gmail_refresh_token", "", d.id)          # disconnected
    assert gmail_account_ids() == [a, b]


def _processed(db, account_id=None):
    row = _row(db, "gmail_processed_ids", account_id)
    return set(json.loads(row.value)) if row and row.value else set()


@pytest.mark.asyncio
async def test_each_account_polls_its_own_mailbox(test_db, accounts, google):
    from backend.scheduler import run_email_check
    a, b = accounts
    _put(test_db, "gmail_refresh_token", "tok-a")
    _put(test_db, "gmail_refresh_token", "tok-b", b)
    google["refresh"] = {"tok-a": "at-a", "tok-b": "at-b"}
    google["mail"] = {"at-a": ["a1", "a2"], "at-b": ["b1"]}

    await run_email_check()

    refreshed = [d["refresh_token"] for _u, d in google["posts"]]
    assert refreshed == ["tok-a", "tok-b"]
    assert {at for _u, at in google["gets"]} == {"at-a", "at-b"}
    assert _processed(test_db) == {"a1", "a2"}
    assert _processed(test_db, b) == {"b1"}
    with unscoped():
        test_db.expire_all()
        runs = {r.account_id: r.status for r in test_db.query(JobRun).filter(JobRun.job_type == "email_check").all()}
    assert runs == {a: "completed", b: "completed"}


@pytest.mark.asyncio
async def test_invalid_grant_marks_only_that_account_expired(test_db, accounts, google):
    from backend.scheduler import run_email_check
    a, b = accounts
    _put(test_db, "gmail_refresh_token", "revoked-a")
    _put(test_db, "gmail_refresh_token", "tok-b", b)
    google["refresh"] = {"tok-b": "at-b"}
    google["mail"] = {"at-b": ["b1"]}

    await run_email_check()

    assert _row(test_db, ga.STATUS_KEY).value == f"expired:{ga.fingerprint('revoked-a')}"
    assert _row(test_db, ga.STATUS_KEY, b) is None
    assert _processed(test_db, b) == {"b1"} and _processed(test_db) == set()
    with use_account(a):
        assert ga.status(test_db)["status"] == "expired"
    with use_account(b):
        assert ga.status(test_db)["status"] == "connected"

    # next tick: the expired owner is not sent to Google again, the member still is
    google["posts"].clear()
    await run_email_check()
    assert [d["refresh_token"] for _u, d in google["posts"]] == ["tok-b"]


@pytest.mark.asyncio
async def test_expired_member_is_not_polled(test_db, accounts, google):
    from backend.scheduler import run_email_check
    _a, b = accounts
    _put(test_db, "gmail_refresh_token", "tok-b", b)
    google["refresh"] = {}                                  # Google refuses tok-b
    await run_email_check()
    assert _row(test_db, ga.STATUS_KEY, b).value == f"expired:{ga.fingerprint('tok-b')}"
    google["posts"].clear()
    await run_email_check()
    assert google["posts"] == []                            # owner: no token, member: expired


@pytest.mark.asyncio
async def test_owner_polls_with_the_env_token_and_members_never_do(test_db, accounts, google, monkeypatch):
    from backend.scheduler import run_email_check
    a, b = accounts
    monkeypatch.setattr(gc, "GMAIL_REFRESH_TOKEN", "env-tok")
    google["refresh"] = {"env-tok": "at-env"}
    google["mail"] = {"at-env": ["e1"]}
    await run_email_check()
    assert [d["refresh_token"] for _u, d in google["posts"]] == ["env-tok"]
    assert _processed(test_db) == {"e1"}
    assert _processed(test_db, b) == set()
    # the member asked directly (manual "check now") still has no token
    google["posts"].clear()
    with use_account(b):
        assert await gc._get_access_token() == ""
    assert google["posts"] == []
