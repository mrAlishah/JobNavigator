"""QA F7 regression (R6-01, server side): a write from a dashboard tab that was loaded for
another account is refused.

The dashboard sends `X-Expected-Account: <the account the page was loaded for>` on every
POST/PUT/PATCH/DELETE. The cookie is per browser, so after a switch in one tab another
tab's writes would land in the new account; the middleware now answers 409
`account_changed` (with the account the browser is on) instead. T5 #8 proved the old
behaviour (a stale tab's persona PATCH overwrote the switched-to account's node); this is
that attack inverted.
"""
from backend.accounts import auth as A
from backend.accounts.context import use_account
from backend.models.db import Persona
from backend.tests.r4_support import client  # noqa: F401 (fixture)
from backend.tests.test_mt_auth import _member, _switch, browser, owner  # noqa: F401

EXPECT = A.EXPECTED_ACCOUNT_HEADER


def _contact(db, account_id):
    db.expire_all()
    with use_account(account_id):
        p = db.query(Persona).first()
        return (p.contact or {}) if p else {}


def test_stale_tab_write_is_refused_and_nothing_lands(browser, test_db, owner):
    m = _member(test_db)
    assert _switch(browser, owner.id).status_code == 200
    # tab 1 loaded for the owner; tab 2 then switches the browser to the member
    assert _switch(browser, m.id).status_code == 200
    r = browser.patch("/api/persona", json={"contact": {"name": "Owner's tab"}},
                      headers={EXPECT: str(owner.id)})
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["detail"] == "account_changed"
    assert body["account"] == {"id": str(m.id), "name": m.name}
    assert r.headers[A.ACCOUNT_HEADER] == str(m.id)
    assert _contact(test_db, m.id).get("name") != "Owner's tab"


def test_matching_expected_account_writes(browser, test_db, owner):
    m = _member(test_db)
    assert _switch(browser, m.id).status_code == 200
    r = browser.patch("/api/persona", json={"contact": {"name": "Member tab"}},
                      headers={EXPECT: str(m.id).upper()})   # case-insensitive UUID
    assert r.status_code == 200, r.text
    assert _contact(test_db, m.id).get("name") == "Member tab"


def test_every_write_method_is_checked(browser, test_db, owner):
    m = _member(test_db)
    assert _switch(browser, m.id).status_code == 200
    h = {EXPECT: str(owner.id)}
    assert browser.post("/api/companies", json={"name": "Stale Co"}, headers=h).status_code == 409
    assert browser.put("/api/companies/00000000-0000-0000-0000-000000000000", json={}, headers=h).status_code == 409
    assert browser.patch("/api/settings", json={"fit_score_threshold": 70}, headers=h).status_code == 409
    assert browser.delete("/api/companies/00000000-0000-0000-0000-000000000000", headers=h).status_code == 409


def test_reads_and_unhinted_writes_are_untouched(browser, test_db, owner):
    m = _member(test_db)
    assert _switch(browser, m.id).status_code == 200
    # a read carrying a stale hint is served (the page learns its account from the header)
    r = browser.get("/api/companies", headers={EXPECT: str(owner.id)})
    assert r.status_code == 200
    assert r.headers[A.ACCOUNT_HEADER] == str(m.id)
    # API clients and the extension send no hint: unchanged
    assert browser.post("/api/companies", json={"name": "Hintless Co"}).status_code in (200, 201)


def test_the_switch_itself_is_never_refused(browser, test_db, owner):
    m = _member(test_db)
    assert _switch(browser, owner.id).status_code == 200
    # the page that switches still names the account it was loaded for
    r = browser.post("/api/auth/switch", json={"account_id": str(m.id)}, headers={EXPECT: str(owner.id)})
    assert r.status_code == 200, r.text
    assert browser.get("/api/auth/me").json()["account"]["id"] == str(m.id)


def test_one_account_install_names_the_owner(browser, test_db, owner):
    r = browser.get("/api/auth/me")
    assert r.status_code == 200
    assert r.headers[A.ACCOUNT_HEADER] == str(owner.id)
    ok = browser.patch("/api/persona", json={"contact": {"name": "Solo"}}, headers={EXPECT: str(owner.id)})
    assert ok.status_code == 200, ok.text


def test_account_required_is_not_masked(browser, test_db, owner):
    """With several accounts and no cookie the answer stays 401 account_required (the
    picker), whatever the hint says."""
    _member(test_db)
    browser.cookies.clear()
    r = browser.patch("/api/persona", json={"contact": {}}, headers={EXPECT: str(owner.id)})
    assert r.status_code == 401
    assert r.json()["detail"] == "account_required"
