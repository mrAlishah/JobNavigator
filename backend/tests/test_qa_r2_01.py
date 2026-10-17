"""QA F1 · R2-01 regression: the T4 attacks on the old bare `X-Account` header, ported
from the T4 attack suite (test_t4_auth.py, test_PROVE_R2_01_*) and inverted: each one
must now FAIL for the attacker.

R2-01: any browser holding the install key (the dashboard keeps it in localStorage and
sends it as X-API-Key) could add `X-Account: <owner id>` and act as the owner without
the owner's PIN; in first-run mode (no install key) anyone on the LAN could. The fix
(decision D1): only a signed `X-Account-Token`, issued after the account's PIN, selects
an account through a header; a bare id is ignored.
"""
import pytest

from backend.models.db import Setting
from backend.tests.r4_support import client  # noqa: F401 (fixture)
from backend.tests.test_mt_auth import (  # noqa: F401 (fixtures)
    _forge, _job, _me, _member, _set_pin, _switch, browser, owner,
)


@pytest.fixture
def no_backup(monkeypatch):
    import backend.api.routes_accounts as ra
    monkeypatch.setattr(ra, "backup_before_remove", lambda acc: "pre_remove_fake.sql")


@pytest.fixture
def no_launch(monkeypatch):
    calls = []

    def _launch(job_type, *a, **k):
        calls.append(job_type)
        return "run-1"
    import backend.main as main_mod
    monkeypatch.setattr(main_mod, "launch_background", _launch)
    return calls


def test_member_with_install_key_and_x_account_owner_stays_the_member(browser, test_db, owner, no_backup, no_launch):
    m = _member(test_db, name="Member", pin="4321")
    victim = _member(test_db, name="Victim")
    _set_pin(test_db, owner, "9999")
    _job(test_db, owner.id, "owner-secret-job")
    assert _switch(browser, m.id, "4321").status_code == 200        # member signs in legitimately
    owner_id = next(a["id"] for a in browser.get("/api/auth/accounts").json() if a["role"] == "owner")
    H = {"X-Account": owner_id}                                     # the T4 attack header

    me = browser.get("/api/auth/me", headers=H).json()
    assert me["account"]["id"] == str(m.id) and me["account"]["role"] == "member"
    assert "owner-secret-job" not in browser.get("/api/jobs", headers=H).text
    lst = browser.get("/api/accounts", headers=H).json()
    assert [a for a in lst["accounts"] if "counts" in a] == [a for a in lst["accounts"] if a["id"] == str(m.id)]
    assert browser.get(f"/api/accounts/{owner_id}/export", headers=H).status_code == 403
    assert browser.post("/api/db/backup", headers=H).status_code == 403
    assert no_launch == []
    d = browser.request("DELETE", f"/api/accounts/{victim.id}", json={"confirm_name": "Victim"}, headers=H)
    assert d.status_code == 403
    assert browser.post(f"/api/accounts/{owner_id}/api-token", headers=H).status_code == 403
    # the cookie-conversion follow-up: no owner cookie can come out of it
    cr = browser.post("/api/accounts", json={"name": "Attacker2"}, headers=H)
    assert cr.status_code == 403 and "jn_account=" not in cr.headers.get("set-cookie", "")
    browser.cookies.delete("jn_account")
    r = browser.get("/api/auth/me", headers=H)
    assert (r.status_code, r.json()) == (401, {"detail": "account_required"})


def test_member_cannot_forge_or_borrow_a_token_for_the_owner(browser, test_db, owner):
    m = _member(test_db, name="Member", pin="4321")
    _set_pin(test_db, owner, "9999")
    # without the owner's PIN no owner token is issued
    for pin in (None, "4321"):
        body = {"account_id": str(owner.id)} if pin is None else {"account_id": str(owner.id), "pin": pin}
        r = browser.post("/api/auth/verify-pin", json=body)
        assert r.status_code == 401 and "token" not in r.json()
    # the member's own token, re-pointed at the owner, fails the signature
    mine = browser.post("/api/auth/verify-pin", json={"account_id": str(m.id), "pin": "4321"}).json()["token"]
    r = browser.get("/api/auth/me", headers={"X-Account-Token": _forge(mine, a=str(owner.id))})
    assert (r.status_code, r.json()) == (401, {"detail": "invalid_account_token"})
    # the member's own browser cookie is not a token either
    _switch(browser, m.id, "4321")
    cookie = browser.cookies.get("jn_account")
    browser.cookies.clear()
    r = browser.get("/api/auth/me", headers={"X-Account-Token": cookie})
    assert r.status_code == 401


def test_first_run_mode_x_account_without_any_key_selects_nothing(client, test_db, owner):
    _member(test_db, name="Member", pin="4321")
    _set_pin(test_db, owner, "9999")
    row = test_db.query(Setting).filter(Setting.key == "dashboard_api_key").one()
    row.value = ""
    test_db.commit()
    client.headers.pop("X-API-Key", None)
    client.headers.pop("X-Test-Act-As", None)
    client.cookies.clear()
    r = client.get("/api/auth/me", headers={"X-Account": str(owner.id)})
    assert (r.status_code, r.json()) == (401, {"detail": "account_required"})
    # a forged token signed with a guessed key does not help either
    from backend.accounts import auth as A
    fake = A.sign_cookie(b"guess", {"k": "api", "a": str(owner.id), "p": "", "i": 0, "e": 2 ** 40})
    r = client.get("/api/auth/me", headers={"X-Account-Token": fake})
    assert (r.status_code, r.json()) == (401, {"detail": "invalid_account_token"})
