"""MT-17 · Telegram per account.

A is the owner (household bot = TELEGRAM_BOT_TOKEN, chat = install row), B a member.
No live Telegram: every HTTP call goes to a recorder (`calls`).
"""
import uuid

import pytest

import backend.notifier.telegram as tg
from backend.accounts.context import unscoped, use_account
from backend.models.db import Account, Application, Job, Setting
from backend.tests.r4_support import client, make_job, set_setting  # noqa: F401 (fixture)

HOUSEHOLD = "household-token"
SECRET = "s3cret"


@pytest.fixture
def accounts(test_db):
    a = test_db.query(Account).filter(Account.role == "owner").one()
    b = Account(name="Partner", role="member")
    test_db.add(b)
    test_db.commit()
    return a.id, b.id


@pytest.fixture(autouse=True)
def _fresh_codes():
    tg.reset_link_state()
    yield
    tg.reset_link_state()


@pytest.fixture
def calls(monkeypatch):
    """Every Telegram API call: (method url, json)."""
    from unittest.mock import AsyncMock, MagicMock
    import httpx
    sent = []

    async def fake_post(url, json=None, **kw):
        sent.append((url, json))
        resp = MagicMock(status_code=200)
        resp.json = MagicMock(return_value={"ok": True})
        return resp

    c = MagicMock()
    c.post = AsyncMock(side_effect=fake_post)
    c.__aenter__ = AsyncMock(return_value=c)
    c.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: c)
    monkeypatch.setattr(tg, "TELEGRAM_BOT_TOKEN", HOUSEHOLD)
    monkeypatch.setattr(tg, "BASE_URL", f"https://api.telegram.org/bot{HOUSEHOLD}")
    import backend.main as main_mod
    monkeypatch.setattr(main_mod, "TELEGRAM_BOT_TOKEN", HOUSEHOLD)
    return sent


def _own(db, account_id, key, value):
    row = db.query(Setting).filter(Setting.key == key, Setting.account_id == account_id).first()
    if row:
        row.value = value
    else:
        db.add(Setting(key=key, value=value, account_id=account_id))
    db.commit()


def _own_bot(db, b, token="member-token", secret="member-secret"):
    _own(db, b, "telegram_bot_mode", "own")
    _own(db, b, "telegram_bot_token", token)
    if secret is not None:
        _own(db, b, tg.OWN_WEBHOOK_SECRET_KEY, secret)


def _bots(calls):
    """The bot token of each sendMessage call, with its chat id."""
    out = []
    for url, body in calls:
        if url.endswith("/sendMessage"):
            out.append((url.split("/bot", 1)[1].split("/", 1)[0], str(body["chat_id"])))
    return out


# ── sending ──────────────────────────────────────────────────────────────────

def test_owner_resolves_to_the_household_bot_and_the_install_chat(test_db, accounts):
    a, b = accounts
    set_setting(test_db, "telegram_chat_id", "111")
    set_setting(test_db, "telegram_bot_mode", "own")        # ignored for the owner
    set_setting(test_db, "telegram_bot_token", "x")
    with use_account(a):
        assert tg.resolve_target() == ("111", None)


def test_member_on_the_household_bot_gets_its_own_chat(test_db, accounts):
    a, b = accounts
    set_setting(test_db, "telegram_chat_id", "111")
    _own(test_db, b, "telegram_chat_id", "222")
    with use_account(b):
        assert tg.resolve_target() == ("222", None)


def test_member_with_its_own_bot(test_db, accounts):
    a, b = accounts
    _own(test_db, b, "telegram_chat_id", "222")
    _own_bot(test_db, b)
    with use_account(b):
        assert tg.resolve_target() == ("222", "member-token")


def test_member_own_mode_without_a_token_falls_back_to_the_household_bot(test_db, accounts):
    a, b = accounts
    _own(test_db, b, "telegram_chat_id", "222")
    _own(test_db, b, "telegram_bot_mode", "own")
    with use_account(b):
        assert tg.resolve_target() == ("222", None)


def test_member_without_a_chat_never_gets_the_owners(test_db, accounts):
    a, b = accounts
    set_setting(test_db, "telegram_chat_id", "111")
    with use_account(b):
        assert tg.resolve_target() == ("", None)


@pytest.mark.asyncio
async def test_digest_goes_to_each_accounts_own_chat_and_bot(test_db, accounts, calls, monkeypatch):
    from backend.scheduler import send_daily_digest
    a, b = accounts
    set_setting(test_db, "telegram_enabled", "true")
    set_setting(test_db, "telegram_chat_id", "111")
    _own(test_db, b, "telegram_enabled", "true")
    _own(test_db, b, "telegram_chat_id", "222")
    _own_bot(test_db, b)
    monkeypatch.setattr(tg, "STRONG_MATCH_SQL", "COALESCE(best_cv_score, 0) >= :threshold")
    await send_daily_digest()
    assert _bots(calls) == [(HOUSEHOLD, "111"), ("member-token", "222")]


@pytest.mark.asyncio
async def test_digest_skips_an_account_without_a_chat(test_db, accounts, calls, monkeypatch):
    from backend.scheduler import send_daily_digest
    a, b = accounts
    set_setting(test_db, "telegram_enabled", "true")
    set_setting(test_db, "telegram_chat_id", "111")
    _own(test_db, b, "telegram_enabled", "true")        # on, but no chat
    monkeypatch.setattr(tg, "STRONG_MATCH_SQL", "COALESCE(best_cv_score, 0) >= :threshold")
    await send_daily_digest()
    assert _bots(calls) == [(HOUSEHOLD, "111")]


@pytest.mark.asyncio
async def test_job_alert_uses_the_members_bot(test_db, accounts, calls):
    a, b = accounts
    _own(test_db, b, "telegram_enabled", "true")
    _own(test_db, b, "telegram_chat_id", "222")
    _own_bot(test_db, b)
    with use_account(b):
        job = make_job(test_db)
        await tg.send_job_alert({"job": job, "best_score": 90})
    assert _bots(calls) == [("member-token", "222")]
    assert f"save_{job.id}" in calls[0][1]["reply_markup"]


@pytest.mark.asyncio
async def test_system_alert_goes_to_the_owner_only(test_db, accounts, calls):
    a, b = accounts
    set_setting(test_db, "telegram_enabled", "true")
    set_setting(test_db, "telegram_chat_id", "111")
    _own(test_db, b, "telegram_enabled", "true")
    _own(test_db, b, "telegram_chat_id", "222")
    _own_bot(test_db, b)
    with use_account(b):                     # even when a member's code path raises it
        assert await tg.send_system_alert("health") is True
    assert _bots(calls) == [(HOUSEHOLD, "111")]


@pytest.mark.asyncio
async def test_system_alert_respects_the_owners_switch(test_db, accounts, calls):
    set_setting(test_db, "telegram_enabled", "false")
    set_setting(test_db, "telegram_chat_id", "111")
    assert await tg.send_system_alert("health") is False
    assert calls == []


@pytest.mark.asyncio
async def test_scrape_health_alert_is_a_system_alert(test_db, accounts, calls):
    from datetime import datetime, timezone
    from backend.models.db import ScrapeLog
    from backend.scheduler import check_scrape_health
    a, b = accounts
    set_setting(test_db, "telegram_enabled", "true")
    set_setting(test_db, "telegram_chat_id", "111")
    _own(test_db, b, "telegram_enabled", "true")
    _own(test_db, b, "telegram_chat_id", "222")
    for _ in range(3):
        test_db.add(ScrapeLog(source="indeed", jobs_found=0, new_jobs=0, error="x",
                              ran_at=datetime.now(timezone.utc)))
    test_db.commit()
    with use_account(a):
        await check_scrape_health()
    assert _bots(calls) == [(HOUSEHOLD, "111")]


def test_test_message_endpoint_sends_to_the_callers_chat(client, test_db, accounts, calls):
    import time
    a, b = accounts
    set_setting(test_db, "telegram_chat_id", "111")
    _own(test_db, b, "telegram_chat_id", "222")
    client.headers["X-Test-Act-As"] = str(b)
    assert client.post("/api/telegram/test").status_code == 202
    for _ in range(100):
        if calls:
            break
        time.sleep(0.02)
    assert _bots(calls) == [(HOUSEHOLD, "222")]


# ── link codes ───────────────────────────────────────────────────────────────

def test_link_code_endpoint_issues_a_code_for_the_caller(client, test_db, accounts):
    a, b = accounts
    client.headers["X-Test-Act-As"] = str(b)
    r = client.post("/api/telegram/link-code")
    assert r.status_code == 200
    body = r.json()
    assert len(body["code"]) == 6 and body["code"].isdigit()
    assert body["account"] == "Partner" and body["bot"] == "household" and body["ttl_seconds"] == 600
    assert tg._link_codes[body["code"]][0] == b
    second = client.post("/api/telegram/link-code").json()["code"]     # replaces the first
    assert list(tg._link_codes) == [second]


def test_link_code_reports_the_own_bot(client, test_db, accounts):
    a, b = accounts
    _own_bot(test_db, b)
    client.headers["X-Test-Act-As"] = str(b)
    assert client.post("/api/telegram/link-code").json()["bot"] == "own"


def _start(chat, text, secret=SECRET):
    return {"update_id": 1, "message": {"message_id": 5, "chat": {"id": chat}, "text": text}}


def _hook(client, update, path="/api/telegram/webhook", secret=SECRET):
    return client.post(path, json=update, headers={"X-Telegram-Bot-Api-Secret-Token": secret})


def test_start_code_links_the_members_chat(client, test_db, accounts, calls):
    a, b = accounts
    set_setting(test_db, "telegram_webhook_secret", SECRET)
    code = tg.create_link_code(b)["code"]
    assert _hook(client, _start(333, f"/start {code}")).status_code == 200
    with unscoped():
        test_db.expire_all()
        rows = {r.key: r.value for r in test_db.query(Setting).filter(Setting.account_id == b)}
    assert rows["telegram_chat_id"] == "333"
    assert rows["telegram_enabled"] == "true"           # switched on with the link
    assert calls[-1][1]["text"] == "Linked to Partner." and str(calls[-1][1]["chat_id"]) == "333"
    assert code not in tg._link_codes                   # single use


def test_bare_code_links_the_owners_chat_to_the_install_row(client, test_db, accounts, calls):
    a, b = accounts
    set_setting(test_db, "telegram_webhook_secret", SECRET)
    set_setting(test_db, "telegram_enabled", "false")
    code = tg.create_link_code(a)["code"]
    _hook(client, _start(444, code))
    test_db.expire_all()
    row = test_db.query(Setting).filter(Setting.key == "telegram_chat_id", Setting.account_id.is_(None)).one()
    assert row.value == "444"
    # the owner's own switch is left as it was
    assert test_db.query(Setting).filter(Setting.key == "telegram_enabled",
                                         Setting.account_id.is_(None)).one().value == "false"


def test_expired_code_is_refused(client, test_db, accounts, calls, monkeypatch):
    a, b = accounts
    set_setting(test_db, "telegram_webhook_secret", SECRET)
    code = tg.create_link_code(b)["code"]
    real = tg._now()
    monkeypatch.setattr(tg, "_now", lambda: real + tg.LINK_CODE_TTL + 1)
    _hook(client, _start(333, f"/start {code}"))
    with unscoped():
        assert test_db.query(Setting).filter(Setting.account_id == b,
                                             Setting.key == "telegram_chat_id").first() is None
    assert "not valid or has expired" in calls[-1][1]["text"]


def test_wrong_code_is_refused_and_repeated_guesses_lock_the_chat(client, test_db, accounts, calls):
    a, b = accounts
    set_setting(test_db, "telegram_webhook_secret", SECRET)
    code = tg.create_link_code(b)["code"]
    wrong = f"{(int(code) + 1) % 10 ** 6:06d}"
    for _ in range(tg.LINK_MAX_FAILURES):
        _hook(client, _start(555, wrong))
    assert "not valid" in calls[-1][1]["text"]
    _hook(client, _start(555, code))                    # the right code, too late
    assert "Too many wrong codes" in calls[-1][1]["text"]
    with unscoped():
        assert test_db.query(Setting).filter(Setting.account_id == b,
                                             Setting.key == "telegram_chat_id").first() is None
    assert code in tg._link_codes                       # not used up by the locked chat


def test_a_chat_linked_to_another_account_is_not_relinked(client, test_db, accounts, calls):
    a, b = accounts
    set_setting(test_db, "telegram_webhook_secret", SECRET)
    set_setting(test_db, "telegram_chat_id", "111")
    owner_name = test_db.query(Account.name).filter(Account.id == a).scalar()
    code = tg.create_link_code(b)["code"]
    _hook(client, _start(111, code))
    assert f"already linked to {owner_name}" in calls[-1][1]["text"]
    with unscoped():
        assert test_db.query(Setting).filter(Setting.account_id == b,
                                             Setting.key == "telegram_chat_id").first() is None


def test_unlinked_chat_gets_not_linked_and_linked_chat_gets_no_reply(client, test_db, accounts, calls):
    a, b = accounts
    set_setting(test_db, "telegram_webhook_secret", SECRET)
    set_setting(test_db, "telegram_chat_id", "111")
    _hook(client, _start(999, "hello"))
    assert calls[-1][1]["text"] == tg.NOT_LINKED_TEXT and str(calls[-1][1]["chat_id"]) == "999"
    n = len(calls)
    _hook(client, _start(111, "hello"))
    assert len(calls) == n


# ── webhook routing of inline buttons ────────────────────────────────────────

def _press(chat, data, cid="cb-1"):
    return {"update_id": 2, "callback_query": {
        "id": cid, "data": data, "from": {"id": chat},
        "message": {"message_id": 7, "chat": {"id": chat}}}}


def _answers(calls):
    return [body["text"] for url, body in calls if url.endswith("/answerCallbackQuery")]


def _status(db, job_id):
    with unscoped():
        db.expire_all()
        return db.query(Job.status).filter(Job.id == job_id).scalar()


def test_button_acts_for_the_chats_account(client, test_db, accounts, calls):
    a, b = accounts
    set_setting(test_db, "telegram_webhook_secret", SECRET)
    _own(test_db, b, "telegram_chat_id", "222")
    with use_account(b):
        job_id = make_job(test_db).id
    _hook(client, _press(222, f"applied_{job_id}"))
    assert _answers(calls) == ["Marked as applied!"]
    assert _status(test_db, job_id) == "applied"
    with unscoped():
        assert test_db.query(Application).filter(Application.job_id == job_id).one().account_id == b


def test_button_for_another_accounts_job_is_refused(client, test_db, accounts, calls):
    """The MT-6 leftover: the install secret no longer lets a chat act on any job."""
    a, b = accounts
    set_setting(test_db, "telegram_webhook_secret", SECRET)
    set_setting(test_db, "telegram_chat_id", "111")
    _own(test_db, b, "telegram_chat_id", "222")
    with use_account(b):
        job_id = make_job(test_db).id
    _hook(client, _press(111, f"skip_{job_id}"))            # owner's chat, member's job
    assert _answers(calls) == ["That job is not in this chat's account."]
    assert _status(test_db, job_id) == "new"


def test_button_from_an_unlinked_chat_does_nothing(client, test_db, accounts, calls):
    a, b = accounts
    set_setting(test_db, "telegram_webhook_secret", SECRET)
    job_id = make_job(test_db).id
    _hook(client, _press(999, f"save_{job_id}"))
    assert _answers(calls) == ["This chat is not linked to a JobNavigator account."]
    assert _status(test_db, job_id) == "new"


def test_owner_chat_still_acts_on_owner_jobs(client, test_db, accounts, calls):
    a, b = accounts
    set_setting(test_db, "telegram_webhook_secret", SECRET)
    set_setting(test_db, "telegram_chat_id", "111")
    job_id = make_job(test_db).id
    _hook(client, _press(111, f"save_{job_id}"))
    assert _answers(calls) == ["Job saved!"]
    assert _status(test_db, job_id) == "saved"


def test_a_member_on_its_own_bot_is_not_routed_on_the_household_bot(client, test_db, accounts, calls):
    a, b = accounts
    set_setting(test_db, "telegram_webhook_secret", SECRET)
    _own(test_db, b, "telegram_chat_id", "222")
    _own_bot(test_db, b)
    with use_account(b):
        job_id = make_job(test_db).id
    _hook(client, _press(222, f"save_{job_id}"))
    assert _answers(calls) == ["This chat is not linked to a JobNavigator account."]
    assert _status(test_db, job_id) == "new"


def test_ambiguous_chat_acts_on_nothing(client, test_db, accounts, calls):
    a, b = accounts
    set_setting(test_db, "telegram_webhook_secret", SECRET)
    set_setting(test_db, "telegram_chat_id", "111")
    _own(test_db, b, "telegram_chat_id", "111")           # typed in by hand
    job_id = make_job(test_db).id
    _hook(client, _press(111, f"save_{job_id}"))
    assert _status(test_db, job_id) == "new"


# ── a member's own bot ───────────────────────────────────────────────────────

def test_own_bot_webhook_checks_its_own_secret(client, test_db, accounts, calls):
    a, b = accounts
    set_setting(test_db, "telegram_webhook_secret", SECRET)
    _own_bot(test_db, b)
    path = f"/api/telegram/webhook/{b}"
    assert _hook(client, {}, path, secret=SECRET).status_code == 401         # household secret
    assert _hook(client, {}, path, secret="member-secret").status_code == 200
    assert _hook(client, {}, f"/api/telegram/webhook/{a}", secret=SECRET).status_code == 404
    assert _hook(client, {}, f"/api/telegram/webhook/{uuid.uuid4()}", secret="x").status_code == 404
    assert _hook(client, {}, "/api/telegram/webhook/not-a-uuid", secret="x").status_code == 404


def test_own_bot_webhook_without_a_secret_is_503(client, test_db, accounts):
    a, b = accounts
    _own_bot(test_db, b, secret=None)
    assert _hook(client, {}, f"/api/telegram/webhook/{b}", secret="x").status_code == 503


def test_own_bot_buttons_act_only_in_the_members_chat_on_its_jobs(client, test_db, accounts, calls):
    a, b = accounts
    _own(test_db, b, "telegram_chat_id", "222")
    _own_bot(test_db, b)
    with use_account(b):
        mine = make_job(test_db).id
    owners = make_job(test_db).id
    path = f"/api/telegram/webhook/{b}"
    _hook(client, _press(222, f"skip_{mine}"), path, secret="member-secret")
    _hook(client, _press(222, f"skip_{owners}"), path, secret="member-secret")
    _hook(client, _press(999, f"skip_{mine}"), path, secret="member-secret")
    assert _answers(calls) == ["Job skipped.", "That job is not in this chat's account.",
                               "This chat is not linked to a JobNavigator account."]
    assert all(url.startswith("https://api.telegram.org/botmember-token/") for url, _ in calls)
    assert _status(test_db, owners) == "new"


def test_own_bot_links_only_its_own_accounts_code(client, test_db, accounts, calls):
    a, b = accounts
    _own_bot(test_db, b)
    path = f"/api/telegram/webhook/{b}"
    owner_code = tg.create_link_code(a)["code"]
    _hook(client, _start(777, owner_code), path, secret="member-secret")
    assert "not valid" in calls[-1][1]["text"] and owner_code in tg._link_codes
    code = tg.create_link_code(b)["code"]
    _hook(client, _start(777, f"/start {code}"), path, secret="member-secret")
    assert calls[-1][1]["text"] == "Linked to Partner."
    assert calls[-1][0].startswith("https://api.telegram.org/botmember-token/")
    with unscoped():
        test_db.expire_all()
        assert test_db.query(Setting.value).filter(Setting.account_id == b,
                                                   Setting.key == "telegram_chat_id").scalar() == "777"


def test_member_registers_its_own_bot(client, test_db, accounts, calls):
    a, b = accounts
    _own_bot(test_db, b, secret=None)
    client.headers["X-Test-Act-As"] = str(b)
    r = client.post("/api/telegram/register-webhook", json={"public_url": "https://x.example"})
    assert r.status_code == 200
    url, body = calls[-1]
    assert url == "https://api.telegram.org/botmember-token/setWebhook"
    assert body["url"] == f"https://x.example/api/telegram/webhook/{b}"
    with unscoped():
        test_db.expire_all()
        stored = test_db.query(Setting.value).filter(
            Setting.account_id == b, Setting.key == tg.OWN_WEBHOOK_SECRET_KEY).scalar()
    assert stored and body["secret_token"] == stored


def test_member_cannot_register_or_rotate_the_household_bot(client, test_db, accounts, calls):
    a, b = accounts
    set_setting(test_db, "telegram_webhook_secret", SECRET)
    client.headers["X-Test-Act-As"] = str(b)
    assert client.post("/api/telegram/register-webhook",
                       json={"public_url": "https://x.example"}).status_code == 403
    assert client.post("/api/telegram/rotate-webhook-secret").status_code == 403
    assert calls == []
    test_db.expire_all()
    assert test_db.query(Setting.value).filter(Setting.key == "telegram_webhook_secret",
                                               Setting.account_id.is_(None)).scalar() == SECRET


def test_owner_registers_the_household_bot_as_before(client, test_db, accounts, calls):
    set_setting(test_db, "telegram_webhook_secret", SECRET)
    r = client.post("/api/telegram/register-webhook", json={"public_url": "https://x.example"})
    assert r.status_code == 200
    url, body = calls[-1]
    assert url == f"https://api.telegram.org/bot{HOUSEHOLD}/setWebhook"
    assert body == {"url": "https://x.example/api/telegram/webhook", "secret_token": SECRET,
                    "allowed_updates": ["callback_query", "message"]}


def test_own_webhook_secret_and_bot_token_are_never_served(client, test_db, accounts):
    a, b = accounts
    _own_bot(test_db, b)
    client.headers["X-Test-Act-As"] = str(b)
    body = client.get("/api/settings").json()
    assert tg.OWN_WEBHOOK_SECRET_KEY not in body
    assert body["telegram_bot_token"] != "member-token" and body["telegram_bot_mode"] == "own"
