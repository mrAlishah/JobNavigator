"""QA F3 regression: PIN attempt accounting (R2-02/04/05), revocable owner unlock (R2-06),
non-ASCII auth values (R2-07), a removed member's stale cookie (R2-03) and the account
delete busy check (R2-08).

The R2-02..07 and R2-03 cases are the T4 attack tests (qa-findings/T4.md, scratchpad
test_t4_auth.py / test_t4_race.py, `test_PROVE_*`) ported and inverted: each attack must
now fail. The rest are unit tests of the new counters (aging, escalation, memory bound,
global ceiling), the unlock allowlist and the removal guard.
"""
import asyncio
import threading
import uuid
from collections import Counter
from datetime import datetime, timezone

import pytest

from backend.accounts import auth as A
from backend.accounts.context import use_account
from backend.models.db import Account
from backend.tests.r4_support import ACT_AS, API_KEY, client  # noqa: F401 (fixture)
from backend.tests.test_mt_auth import _me, _member, _set_pin, _switch, browser, owner  # noqa: F401

XFF = "X-Forwarded-For"


@pytest.fixture
def no_backup(monkeypatch):
    import backend.api.routes_accounts as ra
    monkeypatch.setattr(ra, "backup_before_remove", lambda acc: "pre_remove_fake.sql")


@pytest.fixture
def clock(monkeypatch):
    c = [1000.0]
    monkeypatch.setattr(A, "_mono", lambda: c[0])
    return c


@pytest.fixture(autouse=True)
def _clean_monitor():
    import backend.job_monitor as jm
    A.revoke_all_unlocks()
    yield
    jm._removing.clear()
    jm._fanouts.clear()
    A.revoke_all_unlocks()


def _as_cookie(c, value, method, path, **kw):
    """One request from a browser holding exactly jn_session + this jn_account value (an
    explicit Cookie header wins over the client's jar, so replays never clash with
    server-set cookies). The response's own Set-Cookie is in `r.cookies`."""
    headers = dict(kw.pop("headers", None) or {})
    headers["Cookie"] = f"jn_session={API_KEY}; jn_account={value}"
    c.headers.pop("X-API-Key", None)
    c.headers.pop(ACT_AS, None)
    return c.request(method, path, headers=headers, **kw)


def _me_as(c, value):
    return _as_cookie(c, value, "GET", "/api/auth/me")


def _switch_as(c, value, account_id, pin=None):
    body = {"account_id": str(account_id)}
    if pin is not None:
        body["pin"] = pin
    r = _as_cookie(c, value, "POST", "/api/auth/switch", json=body)
    return r, r.cookies.get("jn_account")


def _unlock_as(c, value, pin):
    r = _as_cookie(c, value, "POST", "/api/auth/verify-owner-pin", json={"pin": pin})
    assert r.status_code == 200, r.text
    return r.cookies.get("jn_account")


def _owner_patch(c, owner_id, body):
    """The owner, from another device (its API token)."""
    return c.patch(f"/api/accounts/{owner_id}", json=body,
                   headers={"X-API-Key": API_KEY, ACT_AS: str(owner_id)})


# ── R2-02: atomic attempt reservation ───────────────────────────────────────

def _count_verifies(monkeypatch, hashes):
    seen = Counter()
    lock = threading.Lock()
    orig = A.verify_pin

    def counting(pin, stored):
        with lock:
            seen[hashes.get(stored, "other")] += 1
        return orig(pin, stored)
    monkeypatch.setattr(A, "verify_pin", counting)
    return seen


def _burst(fn, args_list):
    start = threading.Barrier(len(args_list))
    results = []

    def worker(args):
        start.wait()
        try:
            fn(*args)
            results.append("accepted")
        except A.SwitchDenied as e:
            results.append(e.detail)

    threads = [threading.Thread(target=worker, args=(a,)) for a in args_list]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return Counter(results)


def test_40_concurrent_guesses_evaluate_at_most_5(test_db, owner, monkeypatch):
    """T4 test_PROVE_R2_02_concurrent_guesses_exceed_the_5_try_cap, inverted (was 32/40
    reaching wrong_pin and 67 scrypt verifications)."""
    member = _member(test_db, name="Member", pin="4321")
    _set_pin(test_db, owner, "9999")
    accounts = A.load_accounts(test_db)
    target = A._find(accounts, member.id)
    owner_d = A._owner_of(accounts)
    seen = _count_verifies(monkeypatch, {target["pin_hash"]: "member", owner_d["pin_hash"]: "owner"})
    A.reset_pin_cooldowns()
    tally = _burst(lambda: A.check_switch(accounts, target, "0000", client="10.0.0.9"), [()] * 40)
    assert seen["member"] <= A.PIN_MAX_FAILURES
    assert seen["owner"] <= A.PIN_MAX_FAILURES
    assert tally["wrong_pin"] + tally["accepted"] <= A.PIN_MAX_FAILURES
    assert tally["accepted"] == 0 and tally["pin_cooldown"] >= 40 - A.PIN_MAX_FAILURES
    # nothing is left reserved: the client now waits out a real cooldown
    assert A.cooldown_left(member.id, "10.0.0.9") > 0
    assert all(b.inflight == 0 for b in A._pin_buckets.values())


def test_owner_guesses_do_not_multiply_across_member_targets(test_db, owner, monkeypatch):
    """T4 test_PROVE_R2_02_owner_guesses_multiply_across_member_targets, inverted (was 15
    owner-PIN verifications in one window from 4 members x 10)."""
    _set_pin(test_db, owner, "9999")
    members = [_member(test_db, name=f"M{i}", pin="4321") for i in range(4)]
    accounts = A.load_accounts(test_db)
    targets = [A._find(accounts, m.id) for m in members]
    seen = _count_verifies(monkeypatch, {A._owner_of(accounts)["pin_hash"]: "owner"})
    A.reset_pin_cooldowns()
    _burst(lambda t: A.check_switch(accounts, t, "0000", client="10.0.0.9"),
           [(t,) for t in targets for _ in range(10)])
    assert seen["owner"] <= A.PIN_MAX_FAILURES


def test_one_owner_charge_per_request(test_db, owner, clock):
    """A wrong guess at a PIN'd member costs the owner's (owner, client) counter exactly one."""
    _set_pin(test_db, owner, "9999")
    m1, m2 = _member(test_db, name="A", pin="1111"), _member(test_db, name="B", pin="2222")
    accounts = A.load_accounts(test_db)
    for t in (m1, m2):
        with pytest.raises(A.SwitchDenied):
            A.check_switch(accounts, A._find(accounts, t.id), "0000", client="c")
    assert A._pin_buckets[(str(owner.id), "c")].fails == 2
    # the owner PIN still covers a member and resets that client's owner counter
    assert A.check_switch(accounts, A._find(accounts, m1.id), "9999", client="c") == "owner_pin"
    assert A._pin_buckets[(str(owner.id), "c")].fails == 0


def test_own_pin_success_does_not_charge_the_owner(test_db, owner):
    _set_pin(test_db, owner, "9999")
    m = _member(test_db, pin="4321")
    accounts = A.load_accounts(test_db)
    assert A.check_switch(accounts, A._find(accounts, m.id), "4321", client="c") == "own_pin"
    b = A._pin_buckets.get((str(owner.id), "c"))
    assert b is None or (b.fails == 0 and b.inflight == 0)


# ── R2-04: no lockout of the owner from another device ──────────────────────

def test_member_cannot_keep_the_owner_locked_out(client, test_db, owner, clock):
    """T4 test_PROVE_R2_04_member_keeps_owner_locked_out, inverted (was 429 for the owner's
    right PIN in 4/4 windows). The member guesses from its own device; the owner signs in
    from theirs."""
    _set_pin(test_db, owner, "9999")
    m = _member(test_db, name="Member")
    attacker = {ACT_AS: str(m.id), XFF: "192.168.1.50"}
    for _ in range(4):
        for _ in range(5):
            client.post("/api/auth/verify-owner-pin", json={"pin": "0000"}, headers=attacker)
        r = client.post("/api/auth/switch", json={"account_id": str(owner.id), "pin": "9999"},
                        headers={XFF: "192.168.1.10"})
        assert r.status_code == 200, r.text
        clock[0] += 31
    # the attacker's own device is the one cooling down, and longer each time
    r = client.post("/api/auth/verify-owner-pin", json={"pin": "0000"}, headers=attacker)
    assert r.status_code == 429 and int(r.headers["Retry-After"]) > 30


def test_member_guesses_via_a_pinned_member_do_not_lock_the_owner(client, test_db, owner, clock):
    _set_pin(test_db, owner, "9999")
    pm = _member(test_db, name="Pinned", pin="4321")
    for _ in range(5):
        client.post("/api/auth/verify-pin", json={"account_id": str(pm.id), "pin": "0000"},
                    headers={XFF: "192.168.1.50"})
    r = client.post("/api/auth/switch", json={"account_id": str(owner.id), "pin": "9999"},
                    headers={XFF: "192.168.1.10"})
    assert r.status_code == 200
    # from the attacker's device the owner's PIN no longer covers (owner counter full)
    r = client.post("/api/auth/switch", json={"account_id": str(pm.id), "pin": "9999"},
                    headers={XFF: "192.168.1.50"})
    assert r.status_code == 429


def test_one_client_can_never_reach_the_global_ceiling(test_db, owner, clock):
    """The global per-account ceiling (30 / 15 min) is above what one client's escalating
    cooldowns allow in any 15 minutes (25), so one device cannot trip it."""
    _set_pin(test_db, owner, "9999")
    acc = A._find(A.load_accounts(test_db), owner.id)
    start = clock[0]
    while clock[0] - start < 4 * 3600:
        try:
            A.check_pin(acc, "0000", client="attacker")
        except A.SwitchDenied as e:
            if e.status == 429:
                clock[0] += e.retry_after
        g = A._pin_buckets[(str(owner.id), A.GLOBAL_CLIENT)]
        assert not g.until, "one client tripped the global ceiling"
    A.check_pin(acc, "9999", client="owner-laptop")      # never locked for another device


def test_many_clients_trip_the_global_ceiling(test_db, owner, clock):
    _set_pin(test_db, owner, "9999")
    acc = A._find(A.load_accounts(test_db), owner.id)
    for i in range(A.PIN_GLOBAL_MAX_FAILURES):
        with pytest.raises(A.SwitchDenied):
            A.check_pin(acc, "0000", client=f"10.0.{i}.1")
    with pytest.raises(A.SwitchDenied) as e:
        A.check_pin(acc, "9999", client="fresh-device")
    assert e.value.status == 429 and e.value.retry_after == 30
    clock[0] += 31
    A.check_pin(acc, "9999", client="fresh-device")


# ── R2-05: aging, escalation, memory bound ──────────────────────────────────

def test_failures_age_out(client, test_db, owner, clock):
    """T4 test_PROVE_R2_05 part 1, inverted (was: one typo a month after 4 typos -> 429)."""
    _set_pin(test_db, owner, "9999")
    _member(test_db, name="Member")
    for _ in range(4):
        client.post("/api/auth/verify-owner-pin", json={"pin": "0000"})
    clock[0] += 16 * 60
    r = client.post("/api/auth/verify-owner-pin", json={"pin": "0001"})
    assert r.status_code == 401 and r.json() == {"detail": "wrong_pin", "tries_left": 4}


def test_cooldowns_escalate_and_cap_then_reset(client, test_db, owner, clock):
    """T4 test_PROVE_R2_05 part 2, inverted (was a flat 30 s Retry-After forever)."""
    _set_pin(test_db, owner, "9999")
    _member(test_db, name="Member")
    retry = []
    for _ in range(7):
        for _ in range(5):
            r = client.post("/api/auth/verify-owner-pin", json={"pin": "0002"})
        assert r.status_code == 429
        retry.append(int(r.headers["Retry-After"]))
        clock[0] += retry[-1] + 1
    assert retry == [30, 60, 120, 240, 480, 900, 900]
    # an hour without failures: the escalation starts over
    clock[0] += 3601
    for _ in range(5):
        r = client.post("/api/auth/verify-owner-pin", json={"pin": "0002"})
    assert r.status_code == 429 and r.headers["Retry-After"] == "30"


def test_success_resets_the_escalation(test_db, owner, clock):
    _set_pin(test_db, owner, "9999")
    acc = A._find(A.load_accounts(test_db), owner.id)
    for _ in range(2):
        for _ in range(5):
            with pytest.raises(A.SwitchDenied):
                A.check_pin(acc, "0000", client="c")
        clock[0] += 200
    A.check_pin(acc, "9999", client="c")
    for _ in range(4):
        with pytest.raises(A.SwitchDenied):
            A.check_pin(acc, "0000", client="c")
    with pytest.raises(A.SwitchDenied) as e:
        A.check_pin(acc, "0000", client="c")
    assert e.value.retry_after == 30


def test_counters_are_memory_bounded(test_db, owner, clock, monkeypatch):
    monkeypatch.setattr(A, "PIN_GLOBAL_MAX_FAILURES", 10 ** 6)
    _set_pin(test_db, owner, "9999")
    acc = A._find(A.load_accounts(test_db), owner.id)
    for i in range(A.PIN_MAX_BUCKETS + 500):
        with pytest.raises(A.SwitchDenied):
            A.check_pin(acc, "0000", client=f"c{i}")
    assert len(A._pin_buckets) <= A.PIN_MAX_BUCKETS + 1
    # old idle buckets go once their failures have aged out
    clock[0] += 2 * 3600
    with pytest.raises(A.SwitchDenied):
        A.check_pin(acc, "0000", client="late")
    assert len(A._pin_buckets) <= 3


def test_owner_reset_of_a_member_pin_clears_its_cooldown(client, test_db, owner, clock):
    """R2-14 (same code): the member can use the new PIN at once."""
    m = _member(test_db, pin="4321")
    for _ in range(5):
        client.post("/api/auth/verify-pin", json={"account_id": str(m.id), "pin": "0000"})
    assert client.patch(f"/api/accounts/{m.id}", json={"pin": "5555"}).status_code == 200
    assert client.post("/api/auth/verify-pin",
                       json={"account_id": str(m.id), "pin": "5555"}).status_code == 200


# ── R2-06: owner unlock is revocable ────────────────────────────────────────

def test_unlock_dies_with_lock_owner_pin_change_and_switch(browser, test_db, owner):
    """T4 test_PROVE_R2_06_unlock_survives_lock_and_owner_pin_change, inverted (was: the
    copied unlocked cookie still wrote install settings after Lock and after the owner
    changed their PIN, and stayed valid after switching away)."""
    _set_pin(test_db, owner, "9999")
    m = _member(test_db, name="Member")
    r, cookie = _switch_as(browser, "", m.id)
    assert r.status_code == 200
    unlocked = _unlock_as(browser, cookie, "9999")
    assert _me_as(browser, unlocked).json()["owner_unlocked_until"]
    lock = _as_cookie(browser, unlocked, "POST", "/api/auth/owner-lock")
    assert lock.json()["locked"] is True
    assert _me_as(browser, lock.cookies.get("jn_account")).json()["owner_unlocked_until"] is None
    # replay the copied value
    assert _me_as(browser, unlocked).json()["owner_unlocked_until"] is None
    w = _as_cookie(browser, unlocked, "PATCH", "/api/settings", json={"proxy_url": "socks5://attacker:1"})
    assert w.status_code == 403

    # a fresh unlock, then the owner changes their PIN from another device
    unlocked = _unlock_as(browser, cookie, "9999")
    assert _me_as(browser, unlocked).json()["owner_unlocked_until"]
    r = _owner_patch(browser, owner.id, {"pin": "1111", "current_pin": "9999"})
    assert r.status_code == 200, r.text
    assert _me_as(browser, unlocked).json()["owner_unlocked_until"] is None
    w = _as_cookie(browser, unlocked, "PATCH", "/api/settings", json={"proxy_url": "socks5://attacker:2"})
    assert w.status_code == 403

    # switching away ends the unlock of the previous cookie
    unlocked = _unlock_as(browser, cookie, "1111")
    other = _member(test_db, name="Other")
    r, _ = _switch_as(browser, unlocked, other.id)
    assert r.status_code == 200
    me = _me_as(browser, unlocked).json()
    assert me["account"]["id"] == str(m.id) and me["owner_unlocked_until"] is None
    # and so does logout
    unlocked = _unlock_as(browser, cookie, "1111")
    _as_cookie(browser, unlocked, "POST", "/api/auth/logout")
    assert _me_as(browser, unlocked).json()["owner_unlocked_until"] is None


def test_owner_removing_their_pin_also_ends_unlocks(browser, test_db, owner):
    _set_pin(test_db, owner, "9999")
    m = _member(test_db, name="Member")
    _, cookie = _switch_as(browser, "", m.id)
    unlocked = _unlock_as(browser, cookie, "9999")
    assert _me_as(browser, unlocked).json()["owner_unlocked_until"]
    assert _owner_patch(browser, owner.id, {"pin": None, "current_pin": "9999"}).status_code == 200
    assert _me_as(browser, unlocked).json()["owner_unlocked_until"] is None


def test_member_pin_change_keeps_its_own_live_unlock(browser, test_db, owner):
    _set_pin(test_db, owner, "9999")
    m = _member(test_db, name="Member", pin="4321")
    _, cookie = _switch_as(browser, "", m.id, "4321")
    unlocked = _unlock_as(browser, cookie, "9999")
    r = _as_cookie(browser, unlocked, "PATCH", f"/api/accounts/{m.id}",
                   json={"pin": "2468", "current_pin": "4321"})
    assert r.status_code == 200
    resigned = r.cookies.get("jn_account")
    assert _me_as(browser, resigned).json()["owner_unlocked_until"]   # same unlock, new PIN


def test_cookie_with_u_but_no_live_unlock_id_is_locked(browser, test_db, owner):
    m = _member(test_db, name="Member")
    acc = A._find(A.load_accounts(test_db), m.id)
    until = A._now() + 600
    legacy = A.sign_cookie(A.cookie_secret(test_db),
                           {"k": "cookie", "a": str(m.id), "p": "", "e": int(until), "u": int(until)})
    me = _me_as(browser, legacy).json()
    assert me["account"]["id"] == str(m.id) and me["owner_unlocked_until"] is None
    # an unknown id is locked too; a granted one unlocks until revoked
    assert _me_as(browser, A.make_cookie_value(test_db, acc, until, "nope")).json()["owner_unlocked_until"] is None
    uid = A.grant_unlock(until)
    value = A.make_cookie_value(test_db, acc, until, uid)
    assert _me_as(browser, value).json()["owner_unlocked_until"]
    A.revoke_unlock(uid)
    assert _me_as(browser, value).json()["owner_unlocked_until"] is None


def test_unlock_allowlist_drops_expired_ids(clock):
    now = A._now()
    old = A.grant_unlock(now - 1)
    A.grant_unlock(now + 60)
    assert old not in A._unlocks and len(A._unlocks) == 1


# ── R2-07: non-ASCII auth values -> 401, never 500 ──────────────────────────

@pytest.mark.parametrize("hdr", [
    {"Cookie": "jn_account=abc.\xe9\xe9".encode("latin-1")},
    {"X-API-Key": "r4-test-k\xe9y".encode("latin-1")},
    {"Cookie": f"jn_session={API_KEY}\xe9".encode("latin-1")},
], ids=["jn_account", "x_api_key", "jn_session"])
def test_non_ascii_auth_values_are_401(client, test_db, owner, hdr):
    """T4 test_PROVE_R2_07_non_ascii_auth_values_500, inverted (was 500 on each)."""
    client.headers.pop(ACT_AS, None)
    if "X-API-Key" in hdr or b"jn_session" in hdr.get("Cookie", b""):
        client.headers.pop("X-API-Key", None)    # else: the key header + the bad jn_account
    _member(test_db)
    r = client.get("/api/jobs", headers=hdr)
    assert r.status_code == 401


@pytest.mark.parametrize("path", ["/api/auth/verify", "/api/auth/set-session"])
def test_non_ascii_key_in_the_body_is_401(client, path):
    r = client.post(path, json={"api_key": "kéy-☃"})
    assert r.status_code == 401


def test_secret_eq_never_raises():
    from backend.main import _secret_eq
    assert _secret_eq("abc", "abc") and not _secret_eq("ab\xe9", "abc")
    assert not _secret_eq(None, "abc") and not _secret_eq(5, "abc")
    assert _secret_eq("kéy", "kéy")


# ── R2-03: a stale cookie never falls back to the owner ─────────────────────

def test_removed_member_browser_does_not_become_owner(client, test_db, owner, no_backup):
    """T4 test_PROVE_R2_03_removed_member_browser_becomes_owner, inverted (was /me role=owner,
    export owner 200, backup 202 for the removed member's browser)."""
    _set_pin(test_db, owner, "9999")
    m = _member(test_db, name="Leaver")
    r, member_cookie = _switch_as(client, "", m.id)
    assert r.status_code == 200
    assert _me_as(client, member_cookie).json()["account"]["id"] == str(m.id)
    d = client.request("DELETE", f"/api/accounts/{m.id}", json={"confirm_name": "Leaver"},
                       headers={"X-API-Key": API_KEY, ACT_AS: str(owner.id)})
    assert d.status_code == 200, d.text
    r = _me_as(client, member_cookie)
    assert (r.status_code, r.json()) == (401, {"detail": "account_required"})
    assert r.headers.get("X-JN-Auth") == "account"
    sc = r.headers.get("set-cookie", "")
    assert sc.startswith("jn_account=") and ("Max-Age=0" in sc or "max-age=0" in sc.lower())
    for method, path in (("GET", f"/api/accounts/{owner.id}/export"), ("POST", "/api/db/backup"),
                         ("GET", "/api/settings"), ("GET", "/api/jobs")):
        assert _as_cookie(client, member_cookie, method, path).status_code == 401, path
    # garbage also gets the picker, not the owner
    assert _me_as(client, "garbage.value").status_code == 401
    # the picker still works and lists just the owner; its PIN is needed
    accts = _as_cookie(client, member_cookie, "GET", "/api/auth/accounts").json()
    assert [a["role"] for a in accts] == ["owner"]
    r, _ = _switch_as(client, member_cookie, owner.id)
    assert r.json()["detail"] == "pin_required"
    r, owner_cookie = _switch_as(client, member_cookie, owner.id, "9999")
    assert r.status_code == 200
    assert _me_as(client, owner_cookie).json()["account"]["role"] == "owner"


def test_no_cookie_at_all_keeps_the_one_account_owner(browser, test_db, owner):
    _set_pin(test_db, owner, "9999")
    r = _me(browser)
    assert r.status_code == 200 and r.json()["account"]["id"] == str(owner.id)


def test_stale_cookie_with_pinless_owner_is_one_click(browser, test_db, owner):
    m = _member(test_db, name="Gone")
    _, stale = _switch_as(browser, "", m.id)
    test_db.delete(m)
    test_db.commit()
    assert _me_as(browser, stale).status_code == 401
    r, cookie = _switch_as(browser, stale, owner.id)            # no PIN: one click
    assert r.status_code == 200
    assert _me_as(browser, cookie).json()["account"]["id"] == str(owner.id)


# ── R2-08: delete busy check ────────────────────────────────────────────────

def _fake_run(job_type, account_id=None, all_accounts=False):
    import backend.job_monitor as jm
    key = f"{job_type}:{uuid.uuid4()}"
    jm._running[key] = jm.RunningJob(run_id=uuid.uuid4(), job_type=job_type, trigger="scheduler",
                                     started_at=datetime.now(timezone.utc), account_id=account_id,
                                     all_accounts=all_accounts)
    return key


def _delete(c, m):
    return c.request("DELETE", f"/api/accounts/{m.id}", json={"confirm_name": m.name})


def test_scheduled_scrape_all_makes_every_account_busy(client, test_db, owner, no_backup):
    import backend.job_monitor as jm
    m = _member(test_db, name="Busy")
    key = _fake_run("scrape_all", None, all_accounts=True)
    try:
        r = _delete(client, m)
        assert r.status_code == 409 and r.json()["detail"] == "account_busy"
        assert r.json()["runs"] == ["scrape_all"]
    finally:
        jm._running.pop(key, None)
    assert _delete(client, m).status_code == 200


def test_owner_run_that_is_not_all_accounts_does_not_block(client, test_db, owner, no_backup):
    import backend.job_monitor as jm
    m = _member(test_db, name="Free")
    key = _fake_run("company_scrape", None)
    try:
        assert _delete(client, m).status_code == 200
    finally:
        jm._running.pop(key, None)


def test_per_account_sweep_that_includes_the_account_is_busy(client, test_db, owner, no_backup):
    import backend.job_monitor as jm
    m = _member(test_db, name="Swept")
    with jm.fanning_out([owner.id, m.id]):
        assert _delete(client, m).json()["detail"] == "account_busy"
    assert _delete(client, m).status_code == 200


def test_run_started_during_the_backup_is_caught(client, test_db, owner, monkeypatch):
    import backend.api.routes_accounts as ra
    import backend.job_monitor as jm
    m = _member(test_db, name="Late")
    keys = []

    def slow_backup(acc):
        # while the dump runs: a new run for the account is refused, and a run that
        # slipped in before the mark is seen by the re-check
        with use_account(m.id):
            with pytest.raises(jm.AccountBeingRemovedError):
                jm.launch_background("daily_digest", lambda: None)
        keys.append(_fake_run("email_check", m.id))
        return "pre_remove_fake.sql"
    monkeypatch.setattr(ra, "backup_before_remove", slow_backup)
    try:
        r = _delete(client, m)
        assert r.status_code == 409 and r.json()["runs"] == ["email_check"]
        test_db.expire_all()
        assert test_db.query(Account).filter(Account.id == m.id).count() == 1
        assert not jm._removing
    finally:
        for k in keys:
            jm._running.pop(k, None)


def test_removal_blocks_all_account_runs_and_skips_the_account_in_sweeps():
    import backend.job_monitor as jm
    from backend.accounts.background import for_each_account
    a, b = uuid.uuid4(), uuid.uuid4()
    seen = []

    async def body():
        from backend.accounts.context import current_account
        seen.append(current_account.get())

    async def scheduled_scrape():
        async with jm.tracked_run("scrape_all", "scheduler", all_accounts=True):
            pass

    jm.begin_removal(b)
    try:
        asyncio.run(for_each_account("test", body, [a, b]))
        assert seen == [a]
        with pytest.raises(jm.AccountBeingRemovedError):
            asyncio.run(scheduled_scrape())
    finally:
        jm.end_removal(b)
    assert not jm.runs_touching(b)
