"""Gmail per account (MT-19): whose refresh token, connect/callback state, expired marker.

Install secrets: `GMAIL_CLIENT_ID` / `GMAIL_CLIENT_SECRET` in .env (the owner's Google Cloud
OAuth app; every account consents through it). Per account:

- `gmail_refresh_token` (settings section 'gmail', own_always, redacted by GET /api/settings,
  never copied to a new account, redacted in exports). The owner's lives in the install row,
  a member's in its own row.
- Token resolution (`resolve_token`): the account's stored token; for the OWNER only, when
  none is stored, `GMAIL_REFRESH_TOKEN` from .env (the pre-MT-19 setup keeps working, no
  secret is copied into the DB). A member never falls back to the env token.
- `gmail_token_status` (hidden setting, never served or exported): "expired:<fingerprint>"
  once Google answers `invalid_grant` for that token. The marker only applies while the
  same token is in use, so a reconnect or a new .env token clears it by itself. While
  expired the poller does not call Google for that account.

Connect: `make_state()` signs {account, nonce, expiry, redirect_uri} with a key derived from
the install's `account_cookie_secret` (purpose-bound, so a state can never pass as a
jn_account cookie); the nonce is single use and lives in memory (a backend restart during
consent → "state_unknown", the user clicks Connect again).
"""
import hashlib
import hmac
import json
import logging
import secrets
import time
import urllib.parse
import uuid
from typing import Optional

from backend.accounts.settings import get_setting, member_account, put_setting

logger = logging.getLogger("jobnavigator.gmail")

SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_KEY = "gmail_refresh_token"
STATUS_KEY = "gmail_token_status"
STATE_TTL = 600  # seconds between "Connect Gmail" and Google's redirect back
CALLBACK_PATH = "/api/gmail/callback"

_pending_nonces: dict[str, float] = {}


def _now() -> float:
    return time.time()


# ── Install secrets (read from gmail_client so the existing test patches still apply) ──

def client_config() -> tuple[str, str]:
    from backend.email_monitor import gmail_client
    return (gmail_client.GMAIL_CLIENT_ID or "").strip(), (gmail_client.GMAIL_CLIENT_SECRET or "").strip()


def is_configured() -> bool:
    cid, secret = client_config()
    return bool(cid and secret)


def _env_token() -> str:
    from backend.email_monitor import gmail_client
    return (gmail_client.GMAIL_REFRESH_TOKEN or "").strip()


# ── Token and status per account ─────────────────────────────────────────────

def resolve_token(db) -> tuple[str, Optional[str]]:
    """(refresh token, source) for the current account: source 'account' (stored, in-app
    connect), 'env' (owner only: .env), or None (no token)."""
    stored = (get_setting(db, TOKEN_KEY) or "").strip()
    if stored:
        return stored, "account"
    if member_account(db) is None:
        env = _env_token()
        if env:
            return env, "env"
    return "", None


def fingerprint(token: str) -> str:
    return hashlib.sha256((token or "").encode()).hexdigest()[:16]


def _target(db) -> Optional[uuid.UUID]:
    """Row a write for the current account goes to (None = install = the owner)."""
    return member_account(db)


def is_expired(db, token: str) -> bool:
    return (get_setting(db, STATUS_KEY) or "") == f"expired:{fingerprint(token)}"


def mark_expired(db, token: str) -> None:
    put_setting(db, STATUS_KEY, f"expired:{fingerprint(token)}", account_id=_target(db),
                description="Gmail token state (auto-managed)")
    db.commit()


def clear_status(db, account_id=None, commit: bool = True) -> None:
    """Drop the expired marker (writes only when one is set)."""
    from backend.accounts.settings import _account_row, install_row
    row = install_row(db, STATUS_KEY) if account_id is None else _account_row(db, account_id, STATUS_KEY)
    if row is not None and row.value:
        row.value = ""
        if commit:
            db.commit()


def status(db) -> dict:
    """GET /api/gmail/status for the current account."""
    token, source = resolve_token(db)
    if not token:
        state = "none"
    elif is_expired(db, token):
        state = "expired"
    else:
        state = "connected"
    return {"status": state, "source": source, "configured": is_configured()}


def store_token(db, account_id: Optional[uuid.UUID], token: str) -> None:
    """Save a connected account's refresh token (None = the owner's install row)."""
    put_setting(db, TOKEN_KEY, token, account_id=account_id,
                description="Gmail OAuth refresh token (connected in Settings)")
    clear_status(db, account_id, commit=False)
    db.commit()


def connected_member_ids(db) -> set:
    """Members with a stored token that is not marked expired (system read)."""
    from backend.accounts.context import unscoped
    from backend.models.db import Setting
    with unscoped():
        rows = db.query(Setting.account_id, Setting.key, Setting.value).filter(
            Setting.account_id.isnot(None), Setting.key.in_([TOKEN_KEY, STATUS_KEY])).all()
    tokens, marks = {}, {}
    for acc, key, value in rows:
        (tokens if key == TOKEN_KEY else marks)[acc] = (value or "").strip()
    return {acc for acc, tok in tokens.items()
            if tok and marks.get(acc, "") != f"expired:{fingerprint(tok)}"}


# ── Connect state ────────────────────────────────────────────────────────────

class StateError(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def _state_key(db) -> bytes:
    from backend.accounts.auth import cookie_secret
    return hmac.new(cookie_secret(db), b"jobnavigator gmail oauth state", hashlib.sha256).digest()


def new_state(db, account_id, redirect_uri: str) -> tuple:
    """(signed state, its nonce). /connect puts the nonce into the BROWSER_COOKIE of the
    browser that asked, and the callback requires it there (QA F8 / R5-06)."""
    from backend.accounts.auth import sign_cookie
    now = _now()
    for n, exp in list(_pending_nonces.items()):
        if exp < now:
            _pending_nonces.pop(n, None)
    nonce = secrets.token_urlsafe(16)
    exp = int(now + STATE_TTL)
    _pending_nonces[nonce] = exp
    state = sign_cookie(_state_key(db), {"k": "gmail", "a": str(account_id), "n": nonce,
                                         "e": exp, "r": redirect_uri})
    return state, nonce


def make_state(db, account_id, redirect_uri: str) -> str:
    return new_state(db, account_id, redirect_uri)[0]


# ── Browser binding (QA F8 / R5-06) ──────────────────────────────────────────
#
# The state names the account, but the callback is public: a consent URL minted by
# member B and opened in the owner's browser stored the OWNER's Google token on B. So
# /connect also sets a short-lived httpOnly cookie holding the state's nonce in the browser
# that asked, and the callback refuses a state whose nonce that browser does not hold.
# SameSite=Lax because Google's redirect back is a cross-site top-level GET (a Strict
# cookie would not be sent); the path limits it to the callback. When the browser also
# sends its jn_account cookie (SameSite=Strict, so usually not on Google's redirect), that
# account must be the state's account.
BROWSER_COOKIE = "jn_gmail_state"


def set_browser_cookie(response, nonce: str, secure: bool = False) -> None:
    response.set_cookie(key=BROWSER_COOKIE, value=nonce, max_age=STATE_TTL, path=CALLBACK_PATH,
                        httponly=True, samesite="lax", secure=secure)


def clear_browser_cookie(response) -> None:
    response.delete_cookie(key=BROWSER_COOKIE, path=CALLBACK_PATH, httponly=True, samesite="lax")


def check_browser(db, payload: dict, browser_nonce: Optional[str], account_cookie: Optional[str]) -> None:
    """StateError('browser_mismatch') unless this browser started the flow (it holds the
    state's nonce); StateError('account_mismatch') when its jn_account cookie is present
    and does not resolve to the state's account."""
    expected = str(payload.get("n") or "")
    given = browser_nonce if isinstance(browser_nonce, str) else ""
    if not expected or not given or not hmac.compare_digest(
            given.encode("utf-8", "surrogatepass"), expected.encode("utf-8", "surrogatepass")):
        raise StateError("browser_mismatch")
    if account_cookie:
        from backend.accounts import auth as acct_auth
        acc, _payload = acct_auth.account_from_cookie(db, acct_auth.load_accounts(db), account_cookie)
        if acc is None or str(acc["id"]) != str(payload.get("a")):
            raise StateError("account_mismatch")


def read_state(db, state: Optional[str]) -> dict:
    """The payload of a valid, unexpired, unused state (the nonce is consumed); else
    StateError('invalid_state' | 'state_expired' | 'state_unknown')."""
    from backend.accounts.auth import _b64, _unb64
    if not state or not isinstance(state, str) or state.count(".") != 1:
        raise StateError("invalid_state")
    body, sig = state.split(".")
    expected = _b64(hmac.new(_state_key(db), body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        raise StateError("invalid_state")
    try:
        payload = json.loads(_unb64(body))
    except Exception:
        raise StateError("invalid_state")
    if (not isinstance(payload, dict) or payload.get("k") != "gmail"
            or not isinstance(payload.get("e"), (int, float)) or not payload.get("a")
            or not payload.get("n") or not payload.get("r")):
        raise StateError("invalid_state")
    if payload["e"] < _now():
        _pending_nonces.pop(payload["n"], None)
        raise StateError("state_expired")
    if _pending_nonces.pop(payload["n"], None) is None:
        raise StateError("state_unknown")
    return payload


def consent_url(client_id: str, redirect_uri: str, state: str) -> str:
    return AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
    })


def base_url(request) -> str:
    """The browser-facing origin: forwarded proto/host when a proxy sets them, else the
    request's own (Caddy passes the original Host, so localhost works as is)."""
    proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip() or request.url.scheme
    host = ((request.headers.get("x-forwarded-host") or "").split(",")[0].strip()
            or request.headers.get("host") or request.url.netloc)
    return f"{proto}://{host}"


def redirect_uri(request) -> str:
    return base_url(request) + CALLBACK_PATH


def reset_pending() -> None:
    _pending_nonces.clear()
