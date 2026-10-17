"""QA F8 · Telegram, Gmail and CLI integration hardening.

R5-03 chat-id uniqueness on PATCH, R5-04 link-code budgets + relink notice, R5-05 own-bot
secret stored only after Telegram accepts, R5-06 Gmail state bound to the browser, R5-07
non-ASCII webhook secret → 401, T5-02 link-code lock, R5-08 Claude Code home per member,
R5-09 refresh CLI --account, R5-10 removal forgets in-memory/tmp state. The T4 attack tests
(scratchpad t4/: test_PROVE_R5_03/04/06/07) are ported here inverted. No live Telegram,
Google or LLM: every HTTP call goes to a recorder.
"""
import sys
import threading

import pytest

import backend.email_monitor.gmail_auth as ga
import backend.notifier.telegram as tg
from backend.accounts.context import current_account, unscoped, use_account
from backend.models.db import Account, Setting
from backend.tests.r4_support import client, make_job, set_setting  # noqa: F401 (fixture)
from backend.tests.test_mt_telegram import (  # noqa: F401 (fixtures)
    HOUSEHOLD, SECRET, accounts, calls, _fresh_codes, _hook, _own, _own_bot, _start)


def _chat_rows(db, value):
    with unscoped():
        db.expire_all()
        return db.query(Setting).filter(Setting.key == "telegram_chat_id", Setting.value == value).all()


def _own_value(db, account_id, key):
    with unscoped():
        db.expire_all()
        return db.query(Setting.value).filter(Setting.account_id == account_id, Setting.key == key).scalar()


def _texts(calls):
    return [(str(body["chat_id"]), body["text"]) for url, body in calls if url.endswith("/sendMessage")]


# ── R5-03: telegram_chat_id written through PATCH /api/settings ──────────────

def test_member_cannot_take_the_owners_chat_via_patch(client, test_db, accounts, calls):
    """T4 test_PROVE_R5_03_member_can_poison_owner_chat_via_patch, inverted."""
    a, b = accounts
    set_setting(test_db, "telegram_webhook_secret", SECRET)
    set_setting(test_db, "telegram_chat_id", "111")
    client.headers["X-Test-Act-As"] = str(b)
    r = client.patch("/api/settings", json={"telegram_chat_id": "111"})
    assert r.status_code == 409 and r.json()["detail"] == "chat_in_use"
    assert len(_chat_rows(test_db, "111")) == 1
    assert tg.household_chat_account("111") == a
    assert _own_value(test_db, b, "telegram_chat_id") is None


def test_owner_cannot_take_a_members_chat_via_patch(client, test_db, accounts):
    a, b = accounts
    _own(test_db, b, "telegram_chat_id", "222")
    r = client.patch("/api/settings", json={"telegram_chat_id": "222"})
    assert r.status_code == 409 and r.json()["detail"] == "chat_in_use"
    assert tg.household_chat_account("222") == b


def test_free_own_and_cleared_chat_ids_are_accepted(client, test_db, accounts):
    a, b = accounts
    set_setting(test_db, "telegram_chat_id", "111")
    client.headers["X-Test-Act-As"] = str(b)
    assert client.patch("/api/settings", json={"telegram_chat_id": "222"}).status_code == 200
    assert client.patch("/api/settings", json={"telegram_chat_id": "222"}).status_code == 200   # its own
    assert client.patch("/api/settings", json={"telegram_chat_id": ""}).status_code == 200
    assert _own_value(test_db, b, "telegram_chat_id") == ""
    client.headers["X-Test-Act-As"] = str(a)
    assert client.patch("/api/settings", json={"telegram_chat_id": "111"}).status_code == 200    # owner, unchanged


def test_member_on_its_own_bot_may_use_the_same_chat_but_not_move_it_to_the_household_bot(client, test_db, accounts):
    a, b = accounts
    set_setting(test_db, "telegram_chat_id", "111")
    client.headers["X-Test-Act-As"] = str(b)
    r = client.patch("/api/settings", json={"telegram_bot_mode": "own", "telegram_bot_token": "member-token",
                                            "telegram_chat_id": "111"})
    assert r.status_code == 200, r.text                    # its own bot routes separately
    assert tg.household_chat_account("111") == a
    r = client.patch("/api/settings", json={"telegram_bot_mode": "household"})
    assert r.status_code == 409 and r.json()["detail"] == "chat_in_use"
    assert _own_value(test_db, b, "telegram_bot_mode") == "own"
    assert tg.household_chat_account("111") == a


# ── R5-04: link-code brute force, memory bound, relink notice ────────────────

def test_a_code_is_voided_after_its_wrong_guess_budget_across_chats(client, test_db, accounts, calls):
    """T4 test_PROVE_R5_04_link_code_lockout_is_per_chat_only, inverted: fresh chats no
    longer give fresh guesses at a live code."""
    a, b = accounts
    set_setting(test_db, "telegram_webhook_secret", SECRET)
    code = tg.create_link_code(b)["code"]
    wrong = f"{(int(code) + 1) % 10 ** 6:06d}"
    chat = 5000
    for _ in range(tg.LINK_CODE_MAX_WRONG):
        _hook(client, _start(chat, wrong))
        chat += 1                                           # a new chat for every guess
    assert code not in tg._link_codes
    _hook(client, _start(9999, f"/start {code}"))           # the right code, now void
    assert "not valid" in _texts(calls)[-1][1]
    assert _own_value(test_db, b, "telegram_chat_id") is None


def test_global_cap_stops_every_link_until_the_window_ages(client, test_db, accounts, calls, monkeypatch):
    a, b = accounts
    set_setting(test_db, "telegram_webhook_secret", SECRET)
    for chat in range(7000, 7000 + tg.LINK_GLOBAL_MAX_FAILURES):
        _hook(client, _start(chat, "000000"))
    code = tg.create_link_code(b)["code"]
    _hook(client, _start(8888, code))
    assert "Too many wrong codes" in _texts(calls)[-1][1]
    assert code in tg._link_codes                           # not spent by the refused try
    real = tg._now()
    monkeypatch.setattr(tg, "_now", lambda: real + tg.LINK_FAILURE_WINDOW + 1)
    code = tg.create_link_code(b)["code"]
    _hook(client, _start(8888, code))
    assert _texts(calls)[-1][1] == "Linked to Partner."


def test_global_cap_voids_live_codes(test_db, accounts):
    a, b = accounts
    code_a = tg.create_link_code(a)["code"]
    code_b = tg.create_link_code(b)["code"]
    for i in range(tg.LINK_GLOBAL_MAX_FAILURES):
        tg._record_failure(str(100 + i), bot_account=b)     # own-bot guesses count globally too
    assert code_a not in tg._link_codes and code_b not in tg._link_codes


def test_own_bot_guesses_count_only_against_that_members_code(test_db, accounts):
    a, b = accounts
    code_a = tg.create_link_code(a)["code"]
    code_b = tg.create_link_code(b)["code"]
    for i in range(tg.LINK_CODE_MAX_WRONG):
        tg._record_failure(str(100 + i), bot_account=b)
    assert code_b not in tg._link_codes and code_a in tg._link_codes
    code_b = tg.create_link_code(b)["code"]
    for i in range(tg.LINK_CODE_MAX_WRONG - 1):
        tg._record_failure(str(200 + i))                    # household: every code
    assert tg._link_codes[code_a][2] == tg.LINK_CODE_MAX_WRONG - 1
    assert tg._link_codes[code_b][2] == tg.LINK_CODE_MAX_WRONG - 1


def test_link_failures_are_pruned_and_bounded(client, test_db, accounts, calls, monkeypatch):
    """T4 test_PROVE_R5_04_link_failures_grow_unbounded, inverted."""
    a, b = accounts
    set_setting(test_db, "telegram_webhook_secret", SECRET)
    for chat in range(6000, 6030):
        _hook(client, _start(chat, "111111"))
    assert len(tg._link_failures) == 30
    real = tg._now()
    monkeypatch.setattr(tg, "_now", lambda: real + tg.LINK_FAILURE_WINDOW + 1)
    tg.create_link_code(b)                                  # any store access prunes
    assert tg._link_failures == {} and tg._link_global_failures == []
    monkeypatch.setattr(tg, "LINK_GLOBAL_MAX_FAILURES", 10 ** 6)
    for chat in range(1000):
        tg._record_failure(str(chat))
    assert len(tg._link_failures) == tg.LINK_MAX_TRACKED_CHATS


def test_relinking_to_another_chat_says_so_and_tells_the_old_chat(client, test_db, accounts, calls):
    a, b = accounts
    set_setting(test_db, "telegram_webhook_secret", SECRET)
    _own(test_db, b, "telegram_chat_id", "222")
    code = tg.create_link_code(b)["code"]
    _hook(client, _start(333, f"/start {code}"))
    texts = _texts(calls)
    assert texts[-1] == ("333", "Linked to Partner. Replaced previous chat.")
    assert texts[-2][0] == "222" and "no longer linked" in texts[-2][1] and "Partner" in texts[-2][1]
    assert _own_value(test_db, b, "telegram_chat_id") == "333"


def test_owner_relink_also_says_so_and_same_chat_relink_is_quiet(client, test_db, accounts, calls):
    a, b = accounts
    set_setting(test_db, "telegram_webhook_secret", SECRET)
    set_setting(test_db, "telegram_chat_id", "111")
    owner_name = test_db.query(Account.name).filter(Account.id == a).scalar()
    _hook(client, _start(444, tg.create_link_code(a)["code"]))
    assert _texts(calls)[-1] == ("444", f"Linked to {owner_name}. Replaced previous chat.")
    assert _texts(calls)[-2][0] == "111"
    n = len(_texts(calls))
    _hook(client, _start(444, tg.create_link_code(a)["code"]))
    assert _texts(calls)[n:] == [("444", f"Linked to {owner_name}.")]


def test_first_link_has_no_replaced_note(client, test_db, accounts, calls):
    a, b = accounts
    set_setting(test_db, "telegram_webhook_secret", SECRET)
    _hook(client, _start(333, tg.create_link_code(b)["code"]))
    assert _texts(calls) == [("333", "Linked to Partner.")]


# ── T5-02: the link-code store under concurrency ─────────────────────────────

def test_link_code_store_survives_concurrent_threads(test_db, accounts):
    """T5 race #7 (323 'dictionary changed size during iteration' at a 1 µs switch
    interval), now 0: every access holds `_link_lock`."""
    a, b = accounts
    errors = []
    old = sys.getswitchinterval()

    def worker(acc, n):
        try:
            for i in range(n):
                code = tg.create_link_code(acc)["code"]
                if i % 3 == 0:
                    tg.consume_link_code(code)
                if i % 5 == 0:
                    tg._record_failure(f"{acc}-{i}")
                    tg._locked_out(f"{acc}-{i}")
        except Exception as e:  # noqa: BLE001
            errors.append(repr(e))

    sys.setswitchinterval(1e-6)
    try:
        threads = [threading.Thread(target=worker, args=(acc, 400))
                   for acc in (a, b, a, b, a, b)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        sys.setswitchinterval(old)
    assert errors == []
    per_account = {}
    for entry in tg._link_codes.values():
        per_account[entry[0]] = per_account.get(entry[0], 0) + 1
    assert all(n <= 1 for n in per_account.values())


# ── R5-05: own-bot webhook secret stored only after Telegram accepts ─────────

@pytest.mark.parametrize("answer", ["refused", "network"])
def test_failed_own_bot_registration_keeps_the_old_secret(client, test_db, accounts, calls, monkeypatch, answer):
    a, b = accounts
    _own_bot(test_db, b, secret="old-secret")

    async def fake_register(url, token=None, secret=None):
        if answer == "network":
            return {"ok": False, "error": "ConnectError"}
        return {"ok": False, "error_code": 400, "description": "Bad Request: bad webhook"}
    monkeypatch.setattr(tg, "register_webhook", fake_register)
    client.headers["X-Test-Act-As"] = str(b)
    r = client.post("/api/telegram/register-webhook", json={"public_url": "https://x.example"})
    assert r.status_code == 200 and r.json()["ok"] is False
    assert _own_value(test_db, b, tg.OWN_WEBHOOK_SECRET_KEY) == "old-secret"
    client.headers.pop("X-Test-Act-As")
    assert _hook(client, {}, f"/api/telegram/webhook/{b}", secret="old-secret").status_code == 200


def test_accepted_own_bot_registration_stores_the_secret_it_sent(client, test_db, accounts, calls):
    a, b = accounts
    _own_bot(test_db, b, secret="old-secret")
    client.headers["X-Test-Act-As"] = str(b)
    r = client.post("/api/telegram/register-webhook", json={"public_url": "https://x.example"})
    assert r.status_code == 200 and r.json()["ok"] is True
    sent = calls[-1][1]["secret_token"]
    assert sent != "old-secret" and _own_value(test_db, b, tg.OWN_WEBHOOK_SECRET_KEY) == sent


# ── R5-07: non-ASCII webhook secret header ───────────────────────────────────

@pytest.mark.parametrize("which", ["household", "member"])
def test_non_ascii_webhook_secret_is_401_not_500(client, test_db, accounts, which):
    """T4 test_PROVE_R5_07_non_ascii_secret_token_500, inverted (both webhooks)."""
    a, b = accounts
    set_setting(test_db, "telegram_webhook_secret", SECRET)
    _own_bot(test_db, b)
    path = "/api/telegram/webhook" if which == "household" else f"/api/telegram/webhook/{b}"
    r = client.post(path, json={}, headers={"X-Telegram-Bot-Api-Secret-Token": "s\xe9cret".encode("latin-1")})
    assert r.status_code == 401


# ── R5-06: Gmail OAuth state bound to the browser that started it ────────────

from backend.tests.test_mt_gmail import _env, google, _connect, _callback, _row, _put  # noqa: E402,F401


def test_state_minted_for_b_cannot_be_finished_in_the_owners_browser(client, test_db, accounts, google):
    """T4 test_PROVE_R5_06_state_minted_for_B_binds_owners_consent_to_B, inverted."""
    a, b = accounts
    _put(test_db, "gmail_refresh_token", "owner-existing")
    _url, q = _connect(client, account=b)
    client.cookies.clear()                                  # the owner's browser: no nonce cookie
    r = _callback(client, code="owners-consent-code", state=q["state"])
    assert "reason=browser_mismatch" in r.headers["location"]
    assert google["posts"] == []                            # nothing exchanged
    assert _row(test_db, "gmail_refresh_token", b) is None
    assert _row(test_db, "gmail_refresh_token").value == "owner-existing"


def test_connect_sets_a_short_lived_httponly_nonce_cookie(client, test_db, accounts, google):
    a, b = accounts
    client.headers["X-Test-Act-As"] = str(b)
    r = client.get("/api/gmail/connect")
    set_cookie = [c for c in r.headers.get_list("set-cookie") if c.startswith(f"{ga.BROWSER_COOKIE}=")][0]
    low = set_cookie.lower()
    assert "httponly" in low and "samesite=lax" in low and f"max-age={ga.STATE_TTL}" in low
    assert f"path={ga.CALLBACK_PATH}" in low
    from urllib.parse import parse_qs, urlparse
    state = parse_qs(urlparse(r.json()["url"]).query)["state"][0]
    nonce = set_cookie.split(";", 1)[0].split("=", 1)[1]
    assert ga.read_state(test_db, state)["n"] == nonce


def test_the_browser_that_started_it_connects_and_the_cookie_is_cleared(client, test_db, accounts, google):
    a, b = accounts
    _url, q = _connect(client, account=b)
    r = _callback(client, code="c", state=q["state"])
    assert r.headers["location"].endswith("gmail=connected#gmail")
    assert _row(test_db, "gmail_refresh_token", b).value == "new-refresh"
    cleared = [c for c in r.headers.get_list("set-cookie") if c.startswith(f"{ga.BROWSER_COOKIE}=")]
    assert cleared and "max-age=0" in cleared[0].lower()


def test_a_nonce_from_another_flow_is_refused(client, test_db, accounts, google):
    a, b = accounts
    _url, q_b = _connect(client, account=b)
    _url, q_a = _connect(client, account=a)                 # this browser now holds A's nonce
    r = _callback(client, code="c", state=q_b["state"])
    assert "reason=browser_mismatch" in r.headers["location"]
    assert google["posts"] == []


def _account_cookie(db, account_id):
    from backend.accounts import auth as A
    return A.make_cookie_value(db, A._find(A.load_accounts(db), account_id))


def test_jn_account_cookie_of_another_account_is_refused(client, test_db, accounts, google):
    a, b = accounts
    _url, q = _connect(client, account=b)
    client.cookies.set("jn_account", _account_cookie(test_db, a))
    r = _callback(client, code="c", state=q["state"])
    assert "reason=account_mismatch" in r.headers["location"]
    assert google["posts"] == [] and _row(test_db, "gmail_refresh_token", b) is None


def test_jn_account_cookie_of_the_states_account_is_accepted(client, test_db, accounts, google):
    a, b = accounts
    _url, q = _connect(client, account=b)
    client.cookies.set("jn_account", _account_cookie(test_db, b))
    r = _callback(client, code="c", state=q["state"])
    assert r.headers["location"].endswith("gmail=connected#gmail")


# ── R5-08: Claude Code HOME / CLAUDE_CONFIG_DIR per member ───────────────────

from backend.tests.test_mt_credentials import _capture_run_cli, _models_own, cli_roots  # noqa: E402,F401
import backend.analyzer.llm_client as L  # noqa: E402


@pytest.mark.asyncio
async def test_claude_code_member_own_gets_its_own_home(test_db, accounts, cli_roots, monkeypatch):
    import os
    a, b = accounts
    monkeypatch.setenv("HOME", "/root")
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    _models_own(test_db, b)
    _own(test_db, b, "claude_code_oauth_token", "member-token")
    calls = _capture_run_cli(monkeypatch)
    with use_account(b):
        await L._call_claude_code("p", "s", "", 100)
    home = str(cli_roots / "claude" / "accounts" / str(b))
    assert calls[0]["env"]["HOME"] == home
    assert calls[0]["env"]["CLAUDE_CONFIG_DIR"] == os.path.join(home, ".claude")
    assert os.path.isdir(os.path.join(home, ".claude"))


@pytest.mark.asyncio
@pytest.mark.parametrize("who", ["owner", "inherit"])
async def test_claude_code_owner_and_inherit_keep_the_default_home(test_db, accounts, cli_roots, monkeypatch, who):
    a, b = accounts
    monkeypatch.setenv("HOME", "/root")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "env-owner-token")
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    calls = _capture_run_cli(monkeypatch)
    with use_account(a if who == "owner" else b):
        await L._call_claude_code("p", "s", "", 100)
    assert calls[0]["env"]["HOME"] == "/root" and "CLAUDE_CONFIG_DIR" not in calls[0]["env"]
    assert not (cli_roots / "claude").exists()


def test_removal_deletes_the_claude_home(test_db, accounts, cli_roots):
    from backend.accounts.admin import delete_account_rows
    a, b = accounts
    (cli_roots / "claude" / "accounts" / str(b) / ".claude" / "projects").mkdir(parents=True)
    delete_account_rows(test_db, b)
    assert not (cli_roots / "claude" / "accounts" / str(b)).exists()


# ── R5-09: refresh CLI --account ─────────────────────────────────────────────

def test_refresh_cli_runs_under_the_named_account(test_db, accounts, monkeypatch, capsys):
    from backend import refresh_linkedin_session as rls
    a, b = accounts
    seen = []
    before = current_account.get()
    monkeypatch.setattr(rls, "_run", lambda: seen.append(current_account.get()) or 0)
    assert rls.main(["--account", "partner"]) == 0
    assert rls.main(["--account", str(b)]) == 0
    assert rls.main([]) == 0
    assert seen == [b, b, before]
    out = capsys.readouterr().out
    assert "Refreshing the LinkedIn session of Partner." in out
    assert "inherits the owner's" not in out                # linkedin defaults to own
    from backend.models.db import SectionPolicy
    test_db.add(SectionPolicy(account_id=b, section="linkedin", mode="inherit"))
    test_db.commit()
    assert rls.main(["--account", "Partner"]) == 0
    assert "inherits the owner's" in capsys.readouterr().out


def test_refresh_cli_unknown_account(test_db, accounts, monkeypatch, capsys):
    from backend import refresh_linkedin_session as rls
    monkeypatch.setattr(rls, "_run", lambda: pytest.fail("must not run"))
    assert rls.main(["--account", "nobody"]) == 1
    assert "No single account" in capsys.readouterr().err


# ── R5-10: removal forgets in-memory and /tmp state ──────────────────────────

def test_removal_forgets_pin_file_codes_states_progress_and_counters(test_db, accounts, tmp_path, monkeypatch):
    from backend import refresh_linkedin_session as rls
    from backend.accounts import auth as A
    from backend.accounts import credentials as C
    from backend.accounts.admin import delete_account_rows
    from backend.scraper.sources.linkedin_extension import _linkedin_import_progress
    a, b = accounts
    monkeypatch.setattr(C, "LI_PIN_OWNER", str(tmp_path / "li_pin.txt"))
    monkeypatch.setattr(C, "LI_ACCOUNTS_ROOT", str(tmp_path / "li"))
    pin = tmp_path / f"li_pin_{b}.txt"
    pin.write_text("123456")
    owner_pin = tmp_path / "li_pin.txt"
    owner_pin.write_text("654321")
    code_a = tg.create_link_code(a)["code"]
    tg.create_link_code(b)
    rls._MEMBER_STATES[b] = {"phase": "awaiting_pin", "detail": ""}
    with use_account(b):
        _linkedin_import_progress["status"] = "running"
    with use_account(a):
        _linkedin_import_progress["status"] = "done"
    A._pin_buckets[(str(b), "1.2.3.4")] = A._Bucket(0.0)
    A._touched[str(b)] = 1.0
    try:
        delete_account_rows(test_db, b)
        assert not pin.exists() and owner_pin.exists()
        assert [e[0] for e in tg._link_codes.values()] == [a] and code_a in tg._link_codes
        assert b not in rls._MEMBER_STATES
        assert str(b) not in _linkedin_import_progress._by_account
        with use_account(a):
            assert _linkedin_import_progress["status"] == "done"
        assert not any(k[0] == str(b) for k in A._pin_buckets)
        assert str(b) not in A._touched
    finally:
        rls._MEMBER_STATES.clear()
        _linkedin_import_progress.reset_all()
