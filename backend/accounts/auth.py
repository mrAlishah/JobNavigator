"""Per-browser account sessions, PINs and account selection (MT-10).

Three layers, checked by the HTTP middleware in `backend.main` in this order:

1. Install password: `dashboard_api_key`, sent as the `X-API-Key` header or the
   `jn_session` cookie (unchanged since before multi-tenancy).
2. Account: which account the request acts for.
   * `X-Account-Token: <signed API token>` header (API clients, the extension, scripts).
     The token is issued once the account is chosen, after its PIN when it has one:
     `POST /api/auth/verify-pin` (the extension) or `POST /api/accounts/{id}/api-token`
     (Settings › Your account, the caller's own account only). A present token that is
     tampered, expired, of the wrong kind, for a removed account or issued before the
     account's PIN changed -> 401 `{"detail": "invalid_account_token"}` (the extension
     then forgets it). QA F1 / R2-01: a bare `X-Account: <id>` header is ignored; an
     account id is public (the picker lists it), so it never selects an account.
   * else the signed `jn_account` cookie, issued by `POST /api/auth/switch`. Each
     browser holds its own; the server keeps no "current account". The dashboard
     uses only this cookie, never an account header.
   * else, with exactly one account and NO `jn_account` cookie at all, the owner (a
     single-user install never sees a picker).
   * else 401 `{"detail": "account_required"}` (plus header `X-JN-Auth: account`), which
     the UI turns into the account picker. QA F3 / R2-03: a present but tampered, expired
     or stale cookie (for example the browser of a removed member) is answered with
     `account_required` even when one account remains, never with the owner, and the
     middleware clears it in that response; the picker then lists just the owner (one
     click when the owner has no PIN).
3. PIN, checked by `POST /api/auth/switch` only (see `check_switch`).

Cookie `jn_account` = `<payload>.<sig>`, both base64url without padding:
  payload = JSON {"a": account id, "p": PIN fingerprint, "e": expiry (epoch s),
                  "u": owner unlock expiry (epoch s, 0 = locked)}
  sig     = HMAC-SHA256(server secret, payload)
The server secret is the install setting `account_cookie_secret` (generated on first use,
section 'system' = owner only, never returned by GET /api/settings, not writable through
PATCH). "p" is the first 16 hex chars of sha256(pin_hash) (empty without a PIN), so
setting, changing or removing an account's PIN invalidates every cookie issued for it.
"u" is set by `POST /api/auth/verify-owner-pin` (15 minutes); the middleware exposes it
as `request.state.owner_unlocked_until` for the owner-PIN unlock of MT-14. New cookies
also carry "k": "cookie" (cookies issued before QA F1 have no "k" and still count as
cookies).

Owner unlock is revocable (QA F3 / R2-06): an unlocked cookie also carries "n", a random
unlock id the server keeps in an in-memory allowlist (`_unlocks`, id -> expiry). "u" only
counts while "n" is on the list. Lock (`/api/auth/owner-lock`), switching away and logout
drop that browser's id (so a copied cookie value dies with it); any change to the owner's
PIN (set, change, remove) and a backend restart drop every id. A cookie with "u" but no
live "n" (including cookies issued before F3) is simply locked.

API token (`X-Account-Token`) = the same `<payload>.<sig>` shape signed with the same
secret: payload = {"k": "api", "a": account id, "p": PIN fingerprint, "i": issued at,
"e": expiry = i + 1 year}. "k" keeps the two apart: a cookie value is never accepted as
a token and a token never as a cookie. A token carries no owner unlock. Like the cookie,
setting, changing or removing the account's PIN invalidates every token issued for it.

PIN hashes: `scrypt$<n>$<r>$<p>$<salt hex>$<hash hex>` (hashlib.scrypt, 16-byte random
salt per hash; no bcrypt/argon2 in requirements.txt).

Wrong PINs (QA F3 / R2-02, R2-04, R2-05; in-memory, per process, 429 with Retry-After):
* Counters are kept per (account, client), the client being the caller's IP (Caddy's
  X-Forwarded-For, see main `_auth_client_ip`). 5 wrong tries from one client -> that
  client is cooling down for that account. The cooldown escalates for repeated failures:
  30 s, 60 s, 120 s, ... capped at 15 min. Failures are forgotten 15 min after the last
  one, and the escalation starts again at 30 s after 1 h without a failure; a correct PIN
  resets that client's counter.
* Each account also has a global ceiling over every client: 30 failures within 15 min ->
  the account cools down for everyone (escalating the same way). One client can never
  reach it alone (its own escalation allows at most 25 tries in any 15 min), so a member
  cannot lock the owner out of their own account from one device; only a guess spread
  over many addresses trips it, and then it bounds the guessing rate.
* Attempts are reserved before the PIN is verified (`_reserve`, under a lock): the
  in-flight attempts count against the limit, so parallel requests get no more than 5
  verifications per client per window, then are settled (`_settle`) afterwards.
* A wrong PIN against a PIN'd member also tests the owner's PIN (the owner PIN covers
  member PINs). That owner guess is charged once per request to the owner's own
  (owner, client) counter, whichever member was targeted, so spreading guesses over
  several members does not multiply them; while that counter is full or cooling the
  owner PIN does not cover members for that client.
"""
import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
import uuid
import weakref
from collections import deque
from typing import Optional

COOKIE_NAME = "jn_account"
COOKIE_MAX_AGE = 60 * 60 * 24 * 30          # same lifetime as jn_session
SECRET_KEY = "account_cookie_secret"
OWNER_UNLOCK_SECONDS = 15 * 60

TOKEN_HEADER = "X-Account-Token"
TOKEN_MAX_AGE = 60 * 60 * 24 * 365           # API token: one year
KIND_COOKIE = "cookie"
KIND_API = "api"

PIN_MAX_FAILURES = 5                 # per (account, client) before a cooldown
PIN_COOLDOWN_SECONDS = 30.0          # the first cooldown; doubles per consecutive cooldown
PIN_COOLDOWN_MAX_SECONDS = 15 * 60.0
PIN_FAIL_WINDOW_SECONDS = 15 * 60.0  # failures older than this are forgotten
PIN_STRIKE_RESET_SECONDS = 60 * 60.0  # no failure for this long -> the escalation restarts
PIN_GLOBAL_MAX_FAILURES = 30         # per account over every client, within the window
PIN_MAX_BUCKETS = 1024               # memory bound for the per-client counters

ACCOUNT_REQUIRED = "account_required"
INVALID_ACCOUNT_TOKEN = "invalid_account_token"
AUTH_HEADER = "X-JN-Auth"                    # value "account" on an account_required 401
# QA F7 / R6-01: a dashboard tab sends the account it was loaded for on every write;
# a write whose expected account is not the request's account (another tab switched
# this browser) is refused with 409 `account_changed`. Every account-bound response
# names its account in ACCOUNT_HEADER, which is how a tab learns which account it shows.
EXPECTED_ACCOUNT_HEADER = "X-Expected-Account"
ACCOUNT_HEADER = "X-JN-Account"
ACCOUNT_CHANGED = "account_changed"

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2 ** 14, 8, 1


def _now() -> float:
    return time.time()


def _mono() -> float:
    return time.monotonic()


# ── PIN hashing ──────────────────────────────────────────────────────────────

def valid_pin_format(pin) -> bool:
    return isinstance(pin, str) and len(pin) == 4 and pin.isascii() and pin.isdigit()


def hash_pin(pin: str) -> str:
    if not valid_pin_format(pin):
        raise ValueError("a PIN is exactly 4 digits")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(pin.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_pin(pin, stored: Optional[str]) -> bool:
    if not stored or not valid_pin_format(pin):
        return False
    try:
        algo, n, r, p, salt_hex, hash_hex = stored.split("$")
        if algo != "scrypt":
            return False
        digest = hashlib.scrypt(pin.encode(), salt=bytes.fromhex(salt_hex),
                                n=int(n), r=int(r), p=int(p), dklen=len(hash_hex) // 2)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), hash_hex)


def pin_fingerprint(pin_hash: Optional[str]) -> str:
    return hashlib.sha256(pin_hash.encode()).hexdigest()[:16] if pin_hash else ""


# ── Accounts (plain rows, ≤5 per install) ────────────────────────────────────

def load_accounts(db) -> list:
    """Every account as a dict, owner first then by creation. Account is not
    AccountScoped, so this sees every account under any context."""
    from backend.models.db import Account
    rows = (db.query(Account.id, Account.name, Account.color, Account.role, Account.pin_hash,
                     Account.created_at)
            .all())
    out = [{"id": r.id, "name": r.name, "color": r.color, "role": r.role, "pin_hash": r.pin_hash,
            "created_at": r.created_at} for r in rows]
    out.sort(key=lambda a: (a["role"] != "owner", a["created_at"] is None,
                            a["created_at"].timestamp() if a["created_at"] else 0, a["name"] or ""))
    return out


# QA F9 / T6-02: the per-request lookup (resolve_request) reads the account list from a
# short cache instead of one SELECT per request. Account create / rename / PIN change /
# delete invalidate it (mapper events on Account, plus explicit calls after bulk
# deletes); the TTL bounds any write the events cannot see (another worker, raw SQL).
ACCOUNTS_CACHE_TTL = 5.0
_accounts_cache: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()   # engine -> (monotonic time, [account dicts])
_accounts_cache_lock = threading.Lock()


def invalidate_accounts_cache() -> None:
    with _accounts_cache_lock:
        _accounts_cache.clear()


def cached_accounts(db) -> list:
    """load_accounts with a few seconds of caching, per engine. Callers get copies."""
    engine = _engine_of(db)
    now = time.monotonic()
    if engine is not None and ACCOUNTS_CACHE_TTL > 0:
        with _accounts_cache_lock:
            hit = _accounts_cache.get(engine)
        if hit is not None and now - hit[0] < ACCOUNTS_CACHE_TTL:
            return [dict(a) for a in hit[1]]
    accounts = load_accounts(db)
    if engine is not None and ACCOUNTS_CACHE_TTL > 0:
        with _accounts_cache_lock:
            _accounts_cache[engine] = (now, accounts)
    return [dict(a) for a in accounts]


def _register_cache_events() -> None:
    from sqlalchemy import event
    from backend.models.db import Account

    def _drop(_mapper, _connection, _target):
        invalidate_accounts_cache()

    for name in ("after_insert", "after_update", "after_delete"):
        event.listen(Account, name, _drop)


_register_cache_events()


def public_account(acc: dict) -> dict:
    """What the picker may show: no hashes, no data."""
    return {"id": str(acc["id"]), "name": acc["name"], "color": acc["color"],
            "role": acc["role"], "has_pin": bool(acc["pin_hash"])}


def _owner_of(accounts: list) -> Optional[dict]:
    return next((a for a in accounts if a["role"] == "owner"), None)


def _find(accounts: list, account_id) -> Optional[dict]:
    try:
        target = account_id if isinstance(account_id, uuid.UUID) else uuid.UUID(str(account_id))
    except (ValueError, TypeError, AttributeError):
        return None
    return next((a for a in accounts if a["id"] == target), None)


# ── Server secret ────────────────────────────────────────────────────────────

_secret_cache: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def _engine_of(db):
    try:
        bind = db.get_bind()
    except Exception:
        return None
    return getattr(bind, "engine", bind)


def cookie_secret(db) -> bytes:
    """The HMAC key, created (and committed) on first use; cached per engine."""
    engine = _engine_of(db)
    if engine is not None and engine in _secret_cache:
        return _secret_cache[engine]
    from backend.accounts.settings import get_install_setting, put_setting
    value = (get_install_setting(db, SECRET_KEY) or "").strip()
    if not value:
        value = secrets.token_hex(32)
        try:
            put_setting(db, SECRET_KEY, value, account_id=None,
                        description="HMAC key that signs the jn_account cookie (generated)")
            db.commit()
        except Exception:
            # Another request created it first (unique install key): use theirs.
            db.rollback()
            value = (get_install_setting(db, SECRET_KEY) or "").strip()
            if not value:
                raise
    key = value.encode()
    if engine is not None:
        _secret_cache[engine] = key
    return key


def forget_secret_cache() -> None:
    _secret_cache.clear()


# ── Cookie ───────────────────────────────────────────────────────────────────

def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign_cookie(secret: bytes, payload: dict) -> str:
    body = _b64(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    sig = _b64(hmac.new(secret, body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def read_cookie(secret: bytes, value: Optional[str]) -> Optional[dict]:
    """The payload of a well-signed, unexpired cookie, else None."""
    if not value or not isinstance(value, str) or value.count(".") != 1 or not value.isascii():
        return None
    body, sig = value.split(".")
    expected = _b64(hmac.new(secret, body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        payload = json.loads(_unb64(body))
    except Exception:
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("e"), (int, float)):
        return None
    if payload["e"] < _now():
        return None
    return payload


def make_cookie_value(db, account: dict, unlocked_until: float = 0,
                      unlock_id: Optional[str] = None) -> str:
    """An owner unlock ("u") is only written together with its live allowlist id ("n",
    from `grant_unlock`); without one the cookie is locked (QA F3 / R2-06)."""
    until = int(unlocked_until or 0) if unlock_id else 0
    payload = {"k": KIND_COOKIE, "a": str(account["id"]), "p": pin_fingerprint(account["pin_hash"]),
               "e": int(_now() + COOKIE_MAX_AGE), "u": until}
    if until:
        payload["n"] = unlock_id
    return sign_cookie(cookie_secret(db), payload)


def set_account_cookie(response, db, account: dict, unlocked_until: float = 0,
                       unlock_id: Optional[str] = None) -> None:
    """Issue `jn_account` for `account` (also for MT-11 when a PIN change re-signs the
    caller's own cookie). Same flags and lifetime as jn_session."""
    response.set_cookie(
        key=COOKIE_NAME,
        value=make_cookie_value(db, account, unlocked_until, unlock_id),
        httponly=True,
        samesite="strict",
        max_age=COOKIE_MAX_AGE,
        secure=False,  # set True when deployed over HTTPS (as jn_session)
    )


def clear_account_cookie(response) -> None:
    response.delete_cookie(key=COOKIE_NAME, samesite="strict", httponly=True)


# ── Owner unlock allowlist (QA F3 / R2-06) ───────────────────────────────────

_unlocks: dict = {}          # unlock id -> expiry (epoch s)
_unlocks_lock = threading.Lock()


def grant_unlock(until: float) -> str:
    """A fresh unlock id, live until `until` (or until revoked)."""
    uid = secrets.token_urlsafe(12)
    now = _now()
    with _unlocks_lock:
        for k in [k for k, exp in _unlocks.items() if exp <= now]:
            _unlocks.pop(k, None)
        _unlocks[uid] = float(until)
    return uid


def unlock_live(unlock_id) -> bool:
    if not unlock_id or not isinstance(unlock_id, str):
        return False
    with _unlocks_lock:
        exp = _unlocks.get(unlock_id)
        if exp is None:
            return False
        if exp <= _now():
            _unlocks.pop(unlock_id, None)
            return False
        return True


def revoke_unlock(unlock_id) -> None:
    if unlock_id:
        with _unlocks_lock:
            _unlocks.pop(unlock_id, None)


def revoke_all_unlocks() -> None:
    """Every owner unlock ends (the owner's PIN changed)."""
    with _unlocks_lock:
        _unlocks.clear()


def revoke_cookie_unlock(db, value: Optional[str]) -> None:
    """Drop the unlock id carried by this (well-signed) cookie value, if any: Lock,
    switching away, logout. A copy of the value then stops unlocking too."""
    if not value:
        return
    try:
        payload = read_cookie(cookie_secret(db), value)
    except Exception:
        return
    if payload:
        revoke_unlock(payload.get("n"))


def account_from_cookie(db, accounts: list, value: Optional[str]):
    """(account dict, payload) for a valid cookie of an existing account whose PIN has
    not changed since it was issued, else (None, None)."""
    if not value:
        return None, None
    payload = read_cookie(cookie_secret(db), value)
    if payload is None or payload.get("k", KIND_COOKIE) != KIND_COOKIE:
        return None, None
    acc = _find(accounts, payload.get("a"))
    if acc is None or payload.get("p", "") != pin_fingerprint(acc["pin_hash"]):
        return None, None
    return acc, payload


# ── API token (X-Account-Token) ──────────────────────────────────────────────

def make_api_token(db, account: dict) -> str:
    """A signed per-account API token (module docstring). Issue it only after the
    caller has proven the account: its PIN when it has one, or an existing session
    for that same account."""
    now = int(_now())
    payload = {"k": KIND_API, "a": str(account["id"]), "p": pin_fingerprint(account["pin_hash"]),
               "i": now, "e": now + TOKEN_MAX_AGE}
    return sign_cookie(cookie_secret(db), payload)


def account_from_token(db, accounts: list, value: Optional[str]) -> Optional[dict]:
    """The account of a well-signed, unexpired API token whose account still exists and
    whose PIN has not changed since it was issued, else None. A cookie value is not a
    token."""
    if not value:
        return None
    payload = read_cookie(cookie_secret(db), value.strip())
    if payload is None or payload.get("k") != KIND_API:
        return None
    acc = _find(accounts, payload.get("a"))
    if acc is None or payload.get("p", "") != pin_fingerprint(acc["pin_hash"]):
        return None
    return acc


# ── Request resolution ───────────────────────────────────────────────────────

class Resolution:
    __slots__ = ("account", "error", "unlocked_until", "via", "unlock_id", "clear_cookie")

    def __init__(self, account=None, error=None, unlocked_until=0, via=None, unlock_id=None,
                 clear_cookie=False):
        self.account = account
        self.error = error
        self.unlocked_until = unlocked_until
        self.via = via   # 'token' | 'cookie' | 'single' | None
        self.unlock_id = unlock_id          # the live owner-unlock id of the cookie (R2-06)
        self.clear_cookie = clear_cookie    # a stale jn_account was presented (R2-03)


def resolve_request(db, token: Optional[str], cookie: Optional[str]) -> Resolution:
    """Which account a request acts for (module docstring, layer 2). Runs after the
    install password has been checked (header or jn_session cookie).

    A present `X-Account-Token` decides alone: valid -> its account, anything else ->
    `invalid_account_token` (never a fallback to the cookie or the owner). A bare
    `X-Account` header is not read at all.

    QA F3 / R2-03: only a browser with no `jn_account` cookie at all gets the one-account
    owner; a present cookie that does not verify (removed account, changed PIN, other
    install secret, garbage) -> `account_required` with `clear_cookie`."""
    accounts = cached_accounts(db)   # QA F9 / T6-02
    if token:
        acc = account_from_token(db, accounts, token)
        if acc is None:
            return Resolution(error=INVALID_ACCOUNT_TOKEN)
        return Resolution(account=acc, via="token")
    acc, payload = account_from_cookie(db, accounts, cookie)
    if acc is not None:
        until = float(payload.get("u") or 0)
        unlock_id = payload.get("n") if until else None
        if until and not unlock_live(unlock_id):
            until, unlock_id = 0, None       # revoked, restarted or pre-F3: locked
        return Resolution(account=acc, unlocked_until=until, via="cookie", unlock_id=unlock_id)
    if not accounts:
        return Resolution(via=None)    # before the MT-1 migration: run as before (owner fallback)
    if cookie:
        return Resolution(error=ACCOUNT_REQUIRED, clear_cookie=True)
    if len(accounts) == 1:
        return Resolution(account=accounts[0], via="single")
    return Resolution(error=ACCOUNT_REQUIRED)


# ── last_active_at (MT-11) ───────────────────────────────────────────────────

TOUCH_INTERVAL_SECONDS = 60.0
_touched: dict = {}   # str(account id) -> monotonic time of the last write


def touch_last_active(db, account_id, force: bool = False) -> bool:
    """Stamp `accounts.last_active_at = now` for an authenticated request, at most once a
    minute per account and process (in-memory throttle, so the hot path is a dict lookup).
    `force` (a switch) always writes. Never raises; returns whether it wrote."""
    if account_id is None:
        return False
    key = str(account_id)
    now = _mono()
    last = _touched.get(key)
    if not force and last is not None and now - last < TOUCH_INTERVAL_SECONDS:
        return False
    _touched[key] = now
    try:
        from datetime import datetime, timezone
        from sqlalchemy import update
        from backend.models.db import Account
        t = Account.__table__
        db.execute(update(t).where(t.c.id == account_id).values(last_active_at=datetime.now(timezone.utc)))
        db.commit()
        return True
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return False


def forget_touches() -> None:
    _touched.clear()


# ── PIN attempts: per (account, client) counters, global ceiling (QA F3) ─────
#
# See the module docstring ("Wrong PINs"). Every bucket lives in `_pin_buckets`, keyed by
# (str(account id), client); the client "*" is the account's global ceiling. All reads
# and writes happen under `_pin_lock`, so a reservation and its settlement are atomic.

GLOBAL_CLIENT = "*"


class _Bucket:
    __slots__ = ("fails", "last_fail", "until", "strikes", "inflight", "window", "seen")

    def __init__(self, now: float):
        self.fails = 0           # wrong tries since the last cooldown/success (per client)
        self.last_fail = 0.0
        self.until = 0.0         # cooling down until (monotonic)
        self.strikes = 0         # consecutive cooldowns (escalation)
        self.inflight = 0        # reserved attempts not settled yet
        self.window = deque()    # global bucket: failure times within the window
        self.seen = now


_pin_buckets: dict = {}
_pin_lock = threading.Lock()


def reset_pin_cooldowns() -> None:
    with _pin_lock:
        _pin_buckets.clear()


def _cooldown_for(strikes: int) -> float:
    return min(PIN_COOLDOWN_MAX_SECONDS, PIN_COOLDOWN_SECONDS * (2 ** max(0, strikes - 1)))


def _age(b: "_Bucket", now: float) -> None:
    """Forget old failures (R2-05): the per-client count after the window, the strike
    count (escalation) after an hour without failures, global failures by timestamp."""
    if b.until and b.until <= now:
        b.until = 0.0
    if b.last_fail and now - b.last_fail > PIN_FAIL_WINDOW_SECONDS:
        b.fails = 0
    if b.last_fail and now - b.last_fail > PIN_STRIKE_RESET_SECONDS and not b.until:
        b.strikes = 0
    while b.window and now - b.window[0] > PIN_FAIL_WINDOW_SECONDS:
        b.window.popleft()


def _idle(b: "_Bucket", now: float) -> bool:
    return (b.inflight == 0 and not b.until and not b.fails and not b.window
            and (not b.strikes or now - b.last_fail > PIN_STRIKE_RESET_SECONDS))


def _prune(now: float) -> None:
    """Memory bound (R2-05): drop idle buckets; past PIN_MAX_BUCKETS drop the least
    recently seen buckets that have nothing in flight."""
    if len(_pin_buckets) <= 64:
        return
    for key in [k for k, b in _pin_buckets.items() if (_age(b, now) or _idle(b, now))]:
        _pin_buckets.pop(key, None)
    if len(_pin_buckets) > PIN_MAX_BUCKETS:
        spare = sorted((b.seen, k) for k, b in _pin_buckets.items()
                       if b.inflight == 0 and k[1] != GLOBAL_CLIENT)
        for _, key in spare[:len(_pin_buckets) - PIN_MAX_BUCKETS]:
            _pin_buckets.pop(key, None)


def _bucket(account_id, client: str, now: float) -> "_Bucket":
    key = (str(account_id), client)
    b = _pin_buckets.get(key)
    if b is None:
        b = _pin_buckets[key] = _Bucket(now)
    b.seen = now
    _age(b, now)
    return b


def _retry(seconds: float) -> int:
    return max(1, int(seconds + 0.999))


def _reserve(account_id, client: str) -> Optional[int]:
    """Reserve one attempt for `account_id` from `client` BEFORE the PIN is verified
    (R2-02). Returns None when reserved, else the Retry-After seconds (cooling down, or
    the attempts left are all in flight already)."""
    client = client or ""
    now = _mono()
    with _pin_lock:
        _prune(now)
        b = _bucket(account_id, client, now)
        g = _bucket(account_id, GLOBAL_CLIENT, now)
        if b.until > now or g.until > now:
            return _retry(max(b.until, g.until) - now)
        if b.fails + b.inflight >= PIN_MAX_FAILURES:
            return 1
        if len(g.window) + g.inflight >= PIN_GLOBAL_MAX_FAILURES:
            return 1
        b.inflight += 1
        g.inflight += 1
        return None


def _settle(account_id, client: str, ok: Optional[bool]):
    """Settle a reserved attempt. ok=True: a correct PIN (this client's counter resets);
    ok=False: a wrong PIN, counted; ok=None: nothing was guessed (released). Returns
    (tries_left, cooldown seconds or 0)."""
    client = client or ""
    now = _mono()
    with _pin_lock:
        b = _bucket(account_id, client, now)
        g = _bucket(account_id, GLOBAL_CLIENT, now)
        b.inflight = max(0, b.inflight - 1)
        g.inflight = max(0, g.inflight - 1)
        if ok is None:
            return PIN_MAX_FAILURES - b.fails, 0
        if ok:
            b.fails, b.strikes, b.until = 0, 0, 0.0
            return PIN_MAX_FAILURES, 0
        b.fails += 1
        b.last_fail = g.last_fail = now
        g.window.append(now)
        cooldown = 0.0
        if b.fails >= PIN_MAX_FAILURES:
            b.strikes += 1
            b.fails = 0
            b.until = now + _cooldown_for(b.strikes)
            cooldown = b.until - now
        if len(g.window) >= PIN_GLOBAL_MAX_FAILURES:
            g.strikes += 1
            g.window.clear()
            g.until = now + _cooldown_for(g.strikes)
            cooldown = max(cooldown, g.until - now)
        if cooldown:
            return 0, cooldown
        return PIN_MAX_FAILURES - b.fails, 0


def cooldown_left(account_id, client: str = "") -> float:
    """Seconds `client` must wait before trying `account_id` again (0 = may try)."""
    now = _mono()
    with _pin_lock:
        b = _pin_buckets.get((str(account_id), client or ""))
        g = _pin_buckets.get((str(account_id), GLOBAL_CLIENT))
        left = max(b.until if b else 0.0, g.until if g else 0.0) - now
    return max(0.0, left)


def _clear(account_id) -> None:
    """Forget every counter of `account_id` (its PIN was reset, or it was removed)."""
    key = str(account_id)
    with _pin_lock:
        for k in [k for k in _pin_buckets if k[0] == key]:
            _pin_buckets.pop(k, None)


class SwitchDenied(Exception):
    def __init__(self, status: int, detail: str, retry_after: Optional[int] = None, tries_left=None):
        self.status = status
        self.detail = detail
        self.retry_after = retry_after
        self.tries_left = tries_left
        super().__init__(detail)


def _reserve_or_deny(account_id, client: str) -> None:
    wait = _reserve(account_id, client)
    if wait is not None:
        raise SwitchDenied(429, "pin_cooldown", retry_after=wait)


def _wrong(left: int, cooldown: float):
    if left == 0:
        return SwitchDenied(429, "pin_cooldown", retry_after=_retry(cooldown))
    return SwitchDenied(401, "wrong_pin", tries_left=left)


def check_pin(account: dict, pin, client: str = "") -> None:
    """One account's own PIN with that account's counters (owner unlock, a PIN change's
    `current_pin`). Raises SwitchDenied; returns when the PIN is right. An account without
    a PIN passes."""
    if not account["pin_hash"]:
        return
    _reserve_or_deny(account["id"], client)
    if pin is None or pin == "":
        _settle(account["id"], client, None)
        raise SwitchDenied(401, "pin_required")
    ok = False
    try:
        ok = verify_pin(pin, account["pin_hash"])
    finally:
        left, cooldown = _settle(account["id"], client, ok)
    if not ok:
        raise _wrong(left, cooldown)


def check_switch(accounts: list, target: dict, pin, client: str = "") -> str:
    """Raise SwitchDenied unless `pin` lets the caller into `target`. Returns how:
    'no_pin' | 'own_pin' | 'owner_pin'.

    * owner target: its PIN when one is set; without one -> allowed (QA F2 / D3: every
      PIN is optional, the owner's too).
    * member target without a PIN -> allowed.
    * member target with a PIN -> its PIN, or the owner's PIN (when the owner has one and
      this client may still guess the owner's PIN: one owner attempt reserved per
      request, charged to (owner, client) whichever member is the target)."""
    if not target["pin_hash"]:
        return "no_pin"
    if target["role"] == "owner":
        check_pin(target, pin, client)
        return "own_pin"
    owner = _owner_of(accounts)
    _reserve_or_deny(target["id"], client)
    if pin is None or pin == "":
        _settle(target["id"], client, None)
        raise SwitchDenied(401, "pin_required")
    covering = False
    if owner is not None and owner["pin_hash"]:
        covering = _reserve(owner["id"], client) is None
    own_ok = owner_ok = False
    try:
        own_ok = verify_pin(pin, target["pin_hash"])
        if not own_ok and covering:
            owner_ok = verify_pin(pin, owner["pin_hash"])
    finally:
        left, cooldown = _settle(target["id"], client, own_ok or owner_ok)
        if covering:
            # A request that never tested the owner's PIN releases its owner slot.
            _settle(owner["id"], client, True if owner_ok else (None if own_ok else False))
    if own_ok:
        return "own_pin"
    if owner_ok:
        return "owner_pin"
    raise _wrong(left, cooldown)


def check_owner_pin(accounts: list, pin, client: str = "") -> None:
    """For the 15-minute owner unlock: the owner's PIN, with the owner's counters for
    this client. An owner without a PIN -> allowed with no PIN (QA F2 / D3: a plain
    "Unlock", so the owner sections are only a privacy latch on such an install)."""
    owner = _owner_of(accounts)
    if owner is None:
        raise SwitchDenied(404, "no_owner")
    check_pin(owner, pin, client)


# ── Route guard ──────────────────────────────────────────────────────────────

def require_owner():
    """FastAPI dependency: 403 for a member (install-level jobs: backup, H-1B refresh,
    LinkedIn session). Owner, system code and no-context callers pass."""
    from fastapi import HTTPException
    from backend.accounts.context import current_account, is_owner_unlocked, is_unscoped, owner_account_id
    if is_unscoped() or is_owner_unlocked():   # MT-14: the owner entered their PIN here
        return
    acc = current_account.get()
    if acc is None:
        return
    owner = owner_account_id()
    if owner is not None and acc != owner:
        raise HTTPException(status_code=403, detail="owner_only")
