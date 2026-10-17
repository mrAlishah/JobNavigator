"""Telegram bot — alerts, inline buttons, daily digest, email response alerts.

Per account (MT-17):
- Sending: the chat is always the current account's own `telegram_chat_id` (own_always);
  the bot is the account's own (`telegram_bot_mode` = own + `telegram_bot_token`, members
  only) or else the household bot (TELEGRAM_BOT_TOKEN, the owner's). No chat id = skipped.
- System alerts (scrape health) go to the owner's chat on the household bot only
  (`send_system_alert`).
- Linking: Settings asks for a 6-digit code (`create_link_code`, in memory, 10 min); the
  user sends `/start <code>` (or the bare code) to the bot and the webhook stores that chat
  as the account's chat id.
- Webhook: the household bot posts to /api/telegram/webhook; the chat id names the account
  (`household_chat_account`) and inline buttons act only on that account's jobs. A member's
  own bot posts to /api/telegram/webhook/<account_id> with its own secret.
"""
import html
import json
import logging
import re
import secrets
import threading
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone, timedelta

import httpx

from backend.config import TELEGRAM_BOT_TOKEN
from backend.models.db import SessionLocal, Job, Application
from backend.accounts.settings import get_install_setting, get_setting, put_setting

logger = logging.getLogger("jobnavigator.telegram")

BASE_URL = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

OWN_BOT = "own"
OWN_WEBHOOK_SECRET_KEY = "telegram_own_webhook_secret"
NOT_LINKED_TEXT = ("This chat is not linked to a JobNavigator account. "
                   "Open Settings › Notifications, press Link Telegram and send the code here.")


def _api(token=None) -> str:
    """Bot API base URL: the household bot (BASE_URL) unless an own-bot token is given."""
    return f"https://api.telegram.org/bot{token}" if token else BASE_URL


# Highest numeric value in jobs.cv_scores, compared against :threshold.
# cv_scores is Column(JSON) -> native `json` in Postgres, and the jsonb_*
# functions have no `json` overload, so every reference carries an explicit
# cast. seed.py casts the same column for the same reason.
STRONG_MATCH_SQL = (
    "(SELECT COALESCE(MAX(v::numeric), 0) FROM jsonb_each_text(CASE WHEN jsonb_typeof(cv_scores::jsonb) = 'object' THEN cv_scores::jsonb ELSE '{}'::jsonb END) AS t(k, v) WHERE v ~ '^[0-9]+(\\.[0-9]+)?$') >= :threshold"
)


def _get_chat_id() -> str:
    """Read chat_id from settings table."""
    db = SessionLocal()
    try:
        return get_setting(db, "telegram_chat_id") or ""
    finally:
        db.close()


def _is_enabled() -> bool:
    """Check if Telegram is enabled in settings."""
    db = SessionLocal()
    try:
        value = get_setting(db, "telegram_enabled")
        return value.lower() == "true" if value is not None else True
    finally:
        db.close()


def _own_bot_token():
    """The current account's own bot token, or None = the household bot. Only a member
    with `telegram_bot_mode` = own and a token has one; the owner always uses the
    household bot (TELEGRAM_BOT_TOKEN from .env)."""
    from backend.accounts.settings import member_account
    db = SessionLocal()
    try:
        if member_account(db) is None:
            return None
        if (get_setting(db, "telegram_bot_mode") or "").strip().lower() != OWN_BOT:
            return None
        return (get_setting(db, "telegram_bot_token") or "").strip() or None
    finally:
        db.close()


def resolve_target():
    """(chat_id, bot token or None = household) for the current account; chat_id "" =
    nothing to send to."""
    chat_id = _get_chat_id()
    if not chat_id:
        logger.debug("Telegram: no chat id for this account; message skipped")
        return "", None
    return chat_id, _own_bot_token()


def _bot(token) -> dict:
    """kwargs for _send_message: nothing for the household bot (callers and tests that
    stub _send_message(chat_id, text) keep working), else the own bot's token."""
    return {"token": token} if token else {}


async def send_system_alert(text: str) -> bool:
    """Install-level alert (scrape health): the owner's chat on the household bot only,
    whoever is running. Install values = the owner's switch and chat."""
    from backend.accounts.context import unscoped
    with unscoped():
        if not _is_enabled():
            return False
        chat_id = _get_chat_id()
    if not chat_id:
        logger.debug("Telegram: no owner chat id; system alert skipped")
        return False
    return await _send_message(chat_id, text)


async def _send_message(chat_id: str, text: str, reply_markup: dict = None, parse_mode: str = "HTML",
                        token: str = None) -> bool:
    """Send a Telegram message; returns True on HTTP 200, False on any failure (missing config, network error, non-200).
    `token` = an own bot's token; None = the household bot."""
    if not (token or TELEGRAM_BOT_TOKEN) or not chat_id:
        logger.warning("Telegram not configured (missing token or chat_id)")
        return False

    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
    }
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(f"{_api(token)}/sendMessage", json=payload)
            if resp.status_code != 200:
                logger.error(f"Telegram send failed: {resp.text}")
                return False
            return True
    except Exception as e:
        logger.error(f"Telegram send error: {e}")
        return False


def _get_webhook_secret() -> str:
    """Read the webhook secret from settings. Returns empty string if missing."""
    db = SessionLocal()
    try:
        return (get_install_setting(db, "telegram_webhook_secret") or "").strip()
    finally:
        db.close()


async def register_webhook(public_url: str, token: str = None, secret: str = None) -> dict:
    """Register the Telegram webhook; `public_url` must be the externally reachable https URL
    ending at `/api/telegram/webhook` (Telegram refuses http). Returns the Telegram API response.
    With `token` + `secret`: a member's own bot (URL ends at /api/telegram/webhook/<account>)."""
    if token is None:
        if not TELEGRAM_BOT_TOKEN:
            return {"ok": False, "error": "TELEGRAM_BOT_TOKEN not set"}
        secret = _get_webhook_secret()
        if not secret:
            return {"ok": False, "error": "telegram_webhook_secret not configured"}
    payload = {
        "url": public_url,
        "secret_token": secret,
        "allowed_updates": ["callback_query", "message"],
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(f"{_api(token)}/setWebhook", json=payload)
            return resp.json()
    except Exception as e:
        logger.error(f"setWebhook failed: {e}")
        return {"ok": False, "error": str(e)}


def rotate_webhook_secret() -> str:
    """Regenerate the webhook secret and return the new value; callers should re-register the
    webhook with Telegram after rotation so the next callback carries the new header value."""
    import secrets as _secrets
    new_value = _secrets.token_urlsafe(32)
    db = SessionLocal()
    try:
        # The bot's secret is an install value (owner-only section), whoever rotates it.
        put_setting(db, "telegram_webhook_secret", new_value, account_id=None,
                    description="Auto-generated secret token validated on every /api/telegram/webhook call.")
        db.commit()
    finally:
        db.close()
    return new_value


def _format_salary(salary_min, salary_max):
    """Format salary range for display."""
    if not salary_min and not salary_max:
        return "Not listed"
    if salary_min and salary_max and salary_min != salary_max:
        return f"${salary_min:,}–${salary_max:,}"
    if salary_min:
        return f"${salary_min:,}"
    return f"${salary_max:,}"


def _format_h1b(verdict, lca_count=None, approval_rate=None):
    """Format H-1B verdict for display."""
    icons = {"likely": "✅", "possible": "⚠️", "unlikely": "🚫", "unknown": "❓"}
    icon = icons.get(verdict, "❓")
    parts = [f"{icon} H-1B: {(verdict or 'Unknown').title()}"]
    if lca_count:
        parts.append(f"({lca_count} LCAs")
        if approval_rate:
            parts.append(f", {approval_rate:.0f}% approval)")
        else:
            parts.append(")")
    return " ".join(parts)


async def send_job_alert(data: dict):
    """Send new job alert with inline buttons."""
    if not _is_enabled():
        return

    chat_id, token = resolve_target()
    if not chat_id:
        return

    job = data.get("job")
    if not job:
        return

    best_score = data.get("best_score", 0)

    salary_str = _format_salary(job.salary_min, job.salary_max)
    h1b_str = _format_h1b(
        job.h1b_verdict,
        job.h1b_company_lca_count,
        job.h1b_company_approval_rate,
    )
    jd_flag_str = f"🚫 JD Flag: {job.h1b_jd_snippet[:60]}" if job.h1b_jd_flag else ""

    location_str = job.location or "Unknown"
    if job.remote:
        location_str += " (Remote)"

    scores = job.cv_scores or {}
    scores_str = "  ".join(f"{k}:{v}" for k, v in scores.items()) if scores else "N/A"

    text = (
        f"🆕 <b>New Job — {best_score}% fit</b>\n\n"
        f"🏢 {job.company} · {job.title}\n"
        f"📍 {location_str}\n"
        f"💰 {salary_str}\n"
        f"{h1b_str}\n"
        f"📋 Resumes: {scores_str}\n"
    )

    if jd_flag_str:
        text += f"{jd_flag_str}\n"

    job_id = str(job.id)
    reply_markup = {
        "inline_keyboard": [
            [
                {"text": "🔗 Open", "url": job.url or "https://example.com"},
                {"text": "💾 Save", "callback_data": f"save_{job_id}"},
                {"text": "❌ Skip", "callback_data": f"skip_{job_id}"},
                {"text": "✅ Applied", "callback_data": f"applied_{job_id}"},
            ]
        ]
    }

    await _send_message(chat_id, text, reply_markup, **_bot(token))


async def send_email_alert(data: dict):
    """Send email response alert."""
    if not _is_enabled():
        return

    chat_id, token = resolve_target()
    if not chat_id:
        return

    company = data.get("company", "Unknown")
    response_type = data.get("type", "Unknown")
    role = data.get("role", "")
    snippet = data.get("snippet", "")

    type_icons = {
        "positive": "🟢 Positive",
        "rejection": "🔴 Rejection",
        "auto_reply": "⚪ Auto-reply",
        "ambiguous": "🟡 Ambiguous",
    }
    type_str = type_icons.get(response_type, f"❓ {response_type}")

    text = (
        f"📬 <b>Response — {company}</b>\n"
        f"Type: {type_str}  |  Role: {role}\n"
        f"<i>'{snippet[:200]}'</i>\n"
    )

    await _send_message(chat_id, text, **_bot(token))


async def send_digest():
    """Send daily digest. Returns True when a message went out, False when it was skipped
    (Telegram off, or this account has no chat id) - QA F9 / T3-02."""
    if not _is_enabled():
        return False

    chat_id, token = resolve_target()
    if not chat_id:
        return False

    db = SessionLocal()
    try:
        today = datetime.now(timezone.utc).date()
        yesterday = today - timedelta(days=1)
        yesterday_start = datetime.combine(yesterday, datetime.min.time()).replace(tzinfo=timezone.utc)

        # Count genuinely-new jobs: discovered in the window AND still untriaged
        # (status='new') — excludes ones already saved/skipped/applied.
        new_jobs = db.query(Job).filter(
            Job.discovered_at >= yesterday_start,
            Job.status == "new",
        ).count()

        # Strong matches (score >= threshold)
        threshold = int(get_setting(db, "fit_score_threshold", "60"))

        from sqlalchemy import text as sa_text
        strong = db.query(Job).filter(
            Job.discovered_at >= yesterday_start,
        ).filter(sa_text(STRONG_MATCH_SQL).bindparams(threshold=threshold)).count()

        active_statuses = ["applied", "interview"]
        active_apps = db.query(Application).filter(Application.status.in_(active_statuses)).count()

        responses = db.query(Application).filter(
            Application.last_email_received >= yesterday_start,
        ).count()

        text = (
            f"📊 <b>JobNavigator Daily — {today.strftime('%b %d')}</b>\n"
            f"🆕 New jobs: {new_jobs}  |  ⭐ Strong matches: {strong}\n"
            f"📬 Responses: {responses}  |  📋 Active apps: {active_apps}\n"
        )

        await _send_message(chat_id, text, **_bot(token))

        from backend.activity import log_activity
        log_activity("telegram", f"Daily digest sent: {new_jobs} new jobs, {strong} strong matches", db=db)
        db.commit()
        return True

    finally:
        db.close()


async def send_test_message():
    """Send a test Telegram message."""
    chat_id, token = resolve_target()
    if not chat_id:
        return {"error": "No chat_id configured"}

    text = "🧪 <b>JobNavigator Test</b>\nTelegram integration is working!"
    await _send_message(chat_id, text, **_bot(token))

    from backend.activity import log_activity
    log_activity("telegram", "Test message sent")

    return {"message": "Test sent"}


# ── Linking a chat to an account (MT-17) ─────────────────────────────────────

LINK_CODE_TTL = 600            # seconds a code stays valid
LINK_MAX_FAILURES = 5          # wrong codes per chat ...
LINK_FAILURE_WINDOW = 600      # ... within this many seconds, then the chat is ignored
# QA F8 / R5-04: a new group chat is a new chat id, so the per-chat limit alone does not
# bound guessing. Every live code also has a budget: each wrong code the bot it can be
# redeemed on receives (household: every household code; own bot: that member's code)
# counts against it, and after LINK_CODE_MAX_WRONG the code is void (ask for a new one).
# Over LINK_GLOBAL_MAX_FAILURES wrong codes in the window from all chats together, every
# live code is voided and no code is accepted until the window has aged.
LINK_CODE_MAX_WRONG = 20
LINK_GLOBAL_MAX_FAILURES = 50
LINK_MAX_TRACKED_CHATS = 256   # memory bound of the per-chat counters
_CODE_RE = re.compile(r"^(?:/start(?:@\w+)?\s+)?(\d{6})$")
# code -> [account id, expires at epoch seconds, wrong guesses seen]; one live code per
# account. In memory: a restart forgets pending codes (the user asks for a new one).
_link_codes: dict = {}
_link_failures: dict = {}      # chat id -> [epoch seconds of wrong codes]
_link_global_failures: list = []   # epoch seconds of every wrong code (all chats)
# QA F8 / T5-02: the link-code route runs in the threadpool while the webhook consumes on
# the event loop; every read and write of the three stores above happens under this lock.
_link_lock = threading.Lock()

GLOBAL_LOCKOUT = "global"


def _now() -> float:
    return time.time()


def _purge_codes(now: float) -> None:
    """Drop expired codes and aged failure counters (caller holds `_link_lock`)."""
    for code in [c for c, entry in _link_codes.items() if entry[1] <= now]:
        _link_codes.pop(code, None)
    for chat in [c for c, ts in _link_failures.items()
                 if not ts or now - ts[-1] >= LINK_FAILURE_WINDOW]:
        _link_failures.pop(chat, None)
    _link_global_failures[:] = [t for t in _link_global_failures if now - t < LINK_FAILURE_WINDOW]


def create_link_code(account_id) -> dict:
    """A fresh 6-digit code for `account_id` (replaces its previous one)."""
    with _link_lock:
        now = _now()
        _purge_codes(now)
        for code in [c for c, entry in _link_codes.items() if entry[0] == account_id]:
            _link_codes.pop(code, None)
        while True:
            code = f"{secrets.randbelow(10 ** 6):06d}"
            if code not in _link_codes:
                break
        expires = now + LINK_CODE_TTL
        _link_codes[code] = [account_id, expires, 0]
    return {"code": code, "ttl_seconds": LINK_CODE_TTL,
            "expires_at": datetime.fromtimestamp(expires, timezone.utc).isoformat()}


def consume_link_code(code: str, account_id=None):
    """The account a valid code belongs to (the code is used up), else None. With
    `account_id` (a member's own bot) only that account's code is accepted."""
    with _link_lock:
        _purge_codes(_now())
        entry = _link_codes.get(code)
        if entry is None or (account_id is not None and entry[0] != account_id):
            return None
        _link_codes.pop(code, None)
        return entry[0]


def forget_link_codes(account_id) -> None:
    """Drop `account_id`'s live code (the account was removed, R5-10)."""
    with _link_lock:
        for code in [c for c, entry in _link_codes.items() if str(entry[0]) == str(account_id)]:
            _link_codes.pop(code, None)


def reset_link_state() -> None:
    """Tests: forget every code and counter."""
    with _link_lock:
        _link_codes.clear()
        _link_failures.clear()
        _link_global_failures.clear()


def _locked_out(chat_id: str):
    """False, True (this chat sent too many wrong codes) or GLOBAL_LOCKOUT."""
    with _link_lock:
        now = _now()
        _purge_codes(now)
        if len(_link_global_failures) >= LINK_GLOBAL_MAX_FAILURES:
            return GLOBAL_LOCKOUT
        recent = [t for t in _link_failures.get(chat_id, []) if now - t < LINK_FAILURE_WINDOW]
        if recent:
            _link_failures[chat_id] = recent
        else:
            _link_failures.pop(chat_id, None)
        return len(recent) >= LINK_MAX_FAILURES


def _record_failure(chat_id: str, bot_account=None) -> None:
    """A wrong code from `chat_id` on the household bot (`bot_account` None) or a member's
    own bot: counts against the chat, every code redeemable on that bot, and the global cap."""
    with _link_lock:
        now = _now()
        _purge_codes(now)
        _link_failures.setdefault(chat_id, []).append(now)
        if len(_link_failures) > LINK_MAX_TRACKED_CHATS:
            oldest = sorted(_link_failures, key=lambda c: _link_failures[c][-1])
            for chat in oldest[:len(_link_failures) - LINK_MAX_TRACKED_CHATS]:
                _link_failures.pop(chat, None)
        _link_global_failures.append(now)
        if len(_link_global_failures) >= LINK_GLOBAL_MAX_FAILURES:
            if _link_codes:
                logger.warning("Telegram link codes: %d wrong codes in %ds from all chats; "
                               "every live code voided", len(_link_global_failures), LINK_FAILURE_WINDOW)
            _link_codes.clear()
            return
        for code, entry in list(_link_codes.items()):
            if bot_account is not None and entry[0] != bot_account:
                continue
            entry[2] += 1
            if entry[2] >= LINK_CODE_MAX_WRONG:
                logger.warning("Telegram link code of account %s voided after %d wrong codes",
                               entry[0], entry[2])
                _link_codes.pop(code, None)


# ── Which account a chat belongs to ──────────────────────────────────────────

def _account_value(db, account_id, key: str) -> str:
    """A member's own stored value for `key` ("" when it has none)."""
    from backend.models.db import Setting
    row = (db.query(Setting.value)
           .filter(Setting.key == key, Setting.account_id == account_id).first())
    return (row[0] or "").strip() if row else ""


def member_own_bot(db, account_id):
    """(token, webhook secret) of a member that sends through its own bot, else None."""
    from backend.models.db import Account
    acc = db.query(Account.role).filter(Account.id == account_id).first()
    if acc is None or acc[0] == "owner":
        return None
    if _account_value(db, account_id, "telegram_bot_mode").lower() != OWN_BOT:
        return None
    token = _account_value(db, account_id, "telegram_bot_token")
    if not token:
        return None
    return token, _account_value(db, account_id, OWN_WEBHOOK_SECRET_KEY)


def household_chat_accounts(db, chat_id) -> list:
    """Accounts whose chat id is `chat_id` and that use the household bot. The owner's chat
    is the install row; members' are their own rows. Members on their own bot are left out
    (their bot has its own webhook)."""
    from backend.models.db import Setting, get_owner_id
    if chat_id in (None, ""):
        return []
    owner = get_owner_id(db)
    rows = (db.query(Setting.account_id)
            .filter(Setting.key == "telegram_chat_id", Setting.value == str(chat_id)).all())
    out = []
    for (acc,) in rows:
        acc = acc or owner
        if acc is None or acc in out:
            continue
        if acc != owner and member_own_bot(db, acc) is not None:
            continue
        out.append(acc)
    return out


NOT_LINKED = object()          # a chat no account has linked


def household_chat_account(chat_id):
    """The one account a chat on the household bot belongs to, or NOT_LINKED (none, or
    the same chat linked to two accounts: ambiguous, so nothing is acted on)."""
    db = SessionLocal()
    try:
        found = household_chat_accounts(db, chat_id)
    finally:
        db.close()
    if len(found) > 1:
        logger.warning("Telegram chat %s is linked to %d accounts; ignored", chat_id, len(found))
    return found[0] if len(found) == 1 else NOT_LINKED


def own_bot_chat_account(account_id, chat_id):
    """`account_id` when `chat_id` is that member's own chat (its own bot), else NOT_LINKED."""
    db = SessionLocal()
    try:
        mine = _account_value(db, account_id, "telegram_chat_id")
    finally:
        db.close()
    return account_id if chat_id not in (None, "") and mine == str(chat_id) else NOT_LINKED


def chat_in_use_by(db, account_id, chat_id, on_own_bot: bool):
    """The name of another account the household bot already routes `chat_id` to, else
    None. The rule linking and PATCH /api/settings share (QA F8 / R5-03): a chat on the
    household bot belongs to one account. `on_own_bot`: `account_id` (a member) sends
    through its own bot, whose webhook routes separately, so nothing clashes."""
    from backend.models.db import Account
    if chat_id in (None, "") or on_own_bot:
        return None
    others = [a for a in household_chat_accounts(db, str(chat_id)) if a != account_id]
    if not others:
        return None
    other = db.query(Account.name).filter(Account.id == others[0]).first()
    return other[0] if other and other[0] else "another account"


def link_chat(account_id, chat_id) -> tuple:
    """Store `chat_id` as the account's chat. (True, account name, previous chat id or None)
    or (False, reason, None). A chat already linked to another account on the household
    bot is refused (routing must stay unambiguous). A member with no notification switch
    of its own gets it on. The previous chat is returned when this replaced a different one
    (R5-04: a relink is never silent)."""
    from backend.models.db import Account, get_owner_id
    db = SessionLocal()
    try:
        acc = db.query(Account).filter(Account.id == account_id).first()
        if acc is None:
            return False, "That code belongs to an account that no longer exists.", None
        owner = get_owner_id(db)
        is_owner = acc.id == owner
        on_own_bot = not is_owner and member_own_bot(db, acc.id) is not None
        other = chat_in_use_by(db, acc.id, chat_id, on_own_bot)
        if other:
            return False, (f"This chat is already linked to {other}. "
                           "Clear its Chat ID in Settings first."), None
        target = None if is_owner else acc.id
        if is_owner:
            previous = (get_install_setting(db, "telegram_chat_id") or "").strip()
        else:
            previous = _account_value(db, acc.id, "telegram_chat_id")
        put_setting(db, "telegram_chat_id", str(chat_id), account_id=target)
        if not is_owner and not _account_value(db, acc.id, "telegram_enabled"):
            put_setting(db, "telegram_enabled", "true", account_id=acc.id)
        db.commit()
        replaced = previous if previous and previous != str(chat_id) else None
        logger.info("Telegram chat linked to account %s%s", acc.id,
                    " (replaced a previous chat)" if replaced else "")
        return True, acc.name, replaced
    finally:
        db.close()


REPLACED_TEXT = "Replaced previous chat."
UNLINKED_NOTICE = ("This chat is no longer linked to the JobNavigator account {name}: another "
                   "chat was linked with a code from Settings. If that was not you, open Settings › "
                   "Notifications and link this chat again.")


async def handle_message(message: dict, bot_account=None, token: str = None) -> str:
    """A text message to a bot: a link code links the chat, anything else from a chat no
    account has linked gets the "not linked" reply. `bot_account` = the member whose own bot
    received it (None = the household bot). Returns what was replied ("" = nothing)."""
    chat = message.get("chat") if isinstance(message, dict) else None
    chat_id = chat.get("id") if isinstance(chat, dict) else None
    if chat_id in (None, ""):
        return ""
    chat_id = str(chat_id)
    text = message.get("text")
    text = text.strip() if isinstance(text, str) else ""
    m = _CODE_RE.match(text)
    if m:
        if _locked_out(chat_id):
            reply = "Too many wrong codes. Wait ten minutes, then get a new code in Settings."
        else:
            account = consume_link_code(m.group(1), bot_account)
            if account is None:
                _record_failure(chat_id, bot_account)
                reply = ("That code is not valid or has expired. "
                         "Get a new one in Settings › Notifications › Link Telegram.")
            else:
                ok, detail, replaced = link_chat(account, chat_id)
                reply = f"Linked to {detail}." if ok else detail
                if ok and replaced:
                    reply += " " + REPLACED_TEXT
                    try:     # tell the old chat; best effort (it may have blocked the bot)
                        await _send_message(replaced, UNLINKED_NOTICE.format(name=html.escape(detail)),
                                            None, "HTML", **_bot(token))
                    except Exception as e:  # noqa: BLE001
                        logger.warning("Telegram: could not notify the replaced chat: %s", e)
    else:
        linked = (household_chat_account(chat_id) if bot_account is None
                  else own_bot_chat_account(bot_account, chat_id))
        if linked is not NOT_LINKED:
            return ""
        reply = NOT_LINKED_TEXT
    await _send_message(chat_id, reply, None, "HTML", **_bot(token))
    return reply


def update_chat_id(update_part: dict):
    """The chat a callback query came from (its message's chat, else the sender)."""
    if not isinstance(update_part, dict):
        return None
    msg = update_part.get("message")
    if isinstance(msg, dict) and isinstance(msg.get("chat"), dict):
        cid = msg["chat"].get("id")
        if cid not in (None, ""):
            return str(cid)
    sender = update_part.get("from")
    if isinstance(sender, dict) and sender.get("id") not in (None, ""):
        return str(sender["id"])
    return None


# ── Inline buttons ───────────────────────────────────────────────────────────

_MALFORMED = object()
_JOB_ACTIONS = ("save_", "skip_", "applied_")
_NO_CHAT = object()
# The account the webhook resolved from the chat a button was pressed in (MT-17); set only
# by the webhook (`acting_for_chat`). Direct callers (tests, MT-6) leave it unset.
_chat_account: ContextVar = ContextVar("jn_telegram_chat_account", default=_NO_CHAT)


@contextmanager
def acting_for_chat(account):
    """Inline buttons pressed inside this block may act only on `account`'s jobs
    (NOT_LINKED = on none)."""
    tok = _chat_account.set(account)
    try:
        yield
    finally:
        _chat_account.reset(tok)


def _callback_account(callback_data: str):
    """The account that owns the job a button names (MT-6). The webhook is public (no
    session, only the bot's webhook secret), so the job is looked up across accounts; the
    webhook then checks it against the chat's account (MT-17)."""
    for prefix in _JOB_ACTIONS:
        if callback_data.startswith(prefix):
            try:
                job_id = uuid.UUID(callback_data[len(prefix):])
            except ValueError:
                return _MALFORMED
            db = SessionLocal()
            try:
                row = (db.query(Job.account_id).execution_options(jn_unscoped=True)
                       .filter(Job.id == job_id).first())
                return row[0] if row else None
            finally:
                db.close()
    return None


async def handle_callback(callback_data: str, message_id: int = None):
    """Handle inline button callbacks from Telegram, acting for the account that owns the
    job. From the webhook the job must also belong to the chat's account."""
    from contextlib import nullcontext
    from backend.accounts.context import use_account
    callback_data = callback_data if isinstance(callback_data, str) else ""
    account = _callback_account(callback_data)
    if account is _MALFORMED:
        return "Unknown action"     # a job id that is not a UUID names no job
    chat_account = _chat_account.get()
    if chat_account is not _NO_CHAT and callback_data.startswith(_JOB_ACTIONS):
        if chat_account is NOT_LINKED:
            return "This chat is not linked to a JobNavigator account."
        if account is None or account != chat_account:
            logger.warning("Telegram button refused: job not in the chat's account")
            return "That job is not in this chat's account."
    with (use_account(account) if account is not None else nullcontext()):
        return await _handle_callback(callback_data, message_id)


async def _handle_callback(callback_data: str, message_id: int = None):
    db = SessionLocal()
    try:
        if callback_data.startswith("save_"):
            job_id = callback_data[5:]
            job = db.query(Job).filter(Job.id == job_id).first()
            if job:
                job.saved = True
                job.status = "saved"
                db.commit()
                return "Job saved!"

        elif callback_data.startswith("skip_"):
            job_id = callback_data[5:]
            job = db.query(Job).filter(Job.id == job_id).first()
            if job:
                job.status = "skip"
                db.commit()
                return "Job skipped."

        elif callback_data.startswith("applied_"):
            job_id = callback_data[8:]
            job = db.query(Job).filter(Job.id == job_id).first()
            if job:
                job.status = "applied"
                existing = db.query(Application).filter(Application.job_id == job.id).first()
                if not existing:
                    app = Application(job_id=job.id, status="applied",
                                     status_transitions=[{"from": None, "to": "applied", "at": datetime.now(timezone.utc).isoformat(), "source": "telegram"}])
                    db.add(app)
                db.commit()
                return "Marked as applied!"

        return "Unknown action"
    finally:
        db.close()
