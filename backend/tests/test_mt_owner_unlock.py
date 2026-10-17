"""MT-14 · Settings by role: the owner-PIN unlock lets a member write as the owner.

A member's browser that entered the owner's PIN (POST /api/auth/verify-owner-pin, MT-10)
holds `u` in its jn_account cookie for 15 minutes. While it does, a PATCH /api/settings
writes keys of sections the member does not own to the INSTALL rows (as the owner would),
keys of sections it owns still go to its own rows; policies and owner-only jobs open too.
POST /api/auth/owner-lock ends it.
"""
import pytest

from backend.accounts import auth as A
from backend.accounts import settings as S
from backend.models.db import Account, SectionPolicy, Setting
from backend.tests.r4_support import client, set_setting  # noqa: F401 (fixture)

OWNER_PIN = "9999"


@pytest.fixture
def owner(test_db):
    o = test_db.query(Account).filter(Account.role == "owner").one()
    o.pin_hash = A.hash_pin(OWNER_PIN)
    test_db.commit()
    return o


@pytest.fixture
def member(test_db):
    m = Account(name="Partner", role="member", color="#2e9e6b")
    test_db.add(m)
    test_db.commit()
    return m


@pytest.fixture
def browser(client, owner, member):
    """The member's browser: install key, no account token, the member's cookie."""
    client.headers.pop("X-Test-Act-As", None)
    client.cookies.clear()
    r = client.post("/api/auth/switch", json={"account_id": str(member.id)})
    assert r.status_code == 200
    return client


def _install(db, key):
    db.expire_all()
    row = db.query(Setting).filter(Setting.key == key, Setting.account_id.is_(None)).first()
    return row.value if row else None


def _own(db, account_id, key):
    db.expire_all()
    row = db.query(Setting).filter(Setting.key == key, Setting.account_id == account_id).first()
    return row.value if row else None


def _unlock(c):
    r = c.post("/api/auth/verify-owner-pin", json={"pin": OWNER_PIN})
    assert r.status_code == 200, r.text
    return r


def test_locked_member_cannot_write_owner_sections(browser, test_db):
    set_setting(test_db, "proxy_url", "")
    r = browser.patch("/api/settings", json={"proxy_url": "socks5://x"})
    assert r.status_code == 403
    assert _install(test_db, "proxy_url") == ""


def test_unlocked_member_writes_install_values(browser, test_db, member):
    set_setting(test_db, "proxy_url", "")
    _unlock(browser)
    r = browser.patch("/api/settings", json={"proxy_url": "socks5://unlocked"})
    assert r.status_code == 200, r.text
    assert _install(test_db, "proxy_url") == "socks5://unlocked"
    assert _own(test_db, member.id, "proxy_url") is None


def test_unlocked_member_writes_an_inherited_section_to_install(browser, test_db, member):
    set_setting(test_db, "scoring_rubric", "owner rubric")
    _unlock(browser)
    assert browser.patch("/api/settings", json={"scoring_rubric": "edited as owner"}).status_code == 200
    assert _install(test_db, "scoring_rubric") == "edited as owner"
    assert _own(test_db, member.id, "scoring_rubric") is None


def test_unlocked_mixed_patch_splits_own_and_install(browser, test_db, member):
    # 'exclude' is own by default (member row); 'dedup' is owner-only (install row)
    set_setting(test_db, "title_exclude_global", '["intern"]')
    _unlock(browser)
    r = browser.patch("/api/settings", json={"title_exclude_global": ["senior"],
                                              "dedup_tracking_params": ["ref"]})
    assert r.status_code == 200, r.text
    assert _own(test_db, member.id, "title_exclude_global") == '["senior"]'
    assert _install(test_db, "title_exclude_global") == '["intern"]'
    assert _install(test_db, "dedup_tracking_params") == '["ref"]'


def test_unlock_reconfigures_with_install_values(browser, test_db, member, monkeypatch):
    # the member owns 'scheduler' with its own interval; the reconfigure must still read
    # the install value after an unlocked install write
    set_setting(test_db, "scrape_interval_minutes", "60")
    test_db.add(SectionPolicy(account_id=member.id, section="scheduler", mode="own"))
    test_db.add(Setting(key="scrape_interval_minutes", value="5", account_id=member.id))
    test_db.commit()
    seen = []

    def _fake_configure():
        # runs in the request's context: the member, made unscoped by the PATCH
        test_db.expire_all()
        seen.append(S.get_setting(test_db, "scrape_interval_minutes"))
    from backend.api import routes_settings
    monkeypatch.setattr(routes_settings, "configure_scheduler", _fake_configure)
    _unlock(browser)
    r = browser.patch("/api/settings", json={"backup_cron": "0 3 * * *"})
    assert r.status_code == 200 and r.json()["warnings"] == []
    assert seen == ["60"]


def test_member_own_values_do_not_reconfigure(browser, test_db, member, monkeypatch):
    calls = []
    from backend.api import routes_settings
    monkeypatch.setattr(routes_settings, "configure_scheduler", lambda: calls.append(1))
    test_db.add(SectionPolicy(account_id=member.id, section="scheduler", mode="own"))
    test_db.commit()
    assert browser.patch("/api/settings", json={"scrape_interval_minutes": 7}).status_code == 200
    assert calls == []


def test_unlocked_member_flips_policies(browser, test_db, member):
    assert browser.get("/api/settings/policies").status_code == 403
    _unlock(browser)
    assert browser.get("/api/settings/policies").status_code == 200
    r = browser.patch("/api/settings/policies", json={"account_id": str(member.id), "section": "models", "mode": "own"})
    assert r.status_code == 200
    assert S.section_mode(test_db, member.id, "models") == "own"


def test_owner_lock_ends_the_unlock(browser, test_db):
    set_setting(test_db, "proxy_url", "")
    _unlock(browser)
    assert browser.get("/api/auth/me").json()["owner_unlocked_until"]
    r = browser.post("/api/auth/owner-lock")
    assert r.status_code == 200 and r.json() == {"ok": True, "locked": True}
    assert browser.get("/api/auth/me").json()["owner_unlocked_until"] is None
    assert browser.get("/api/auth/me").json()["account"]["name"] == "Partner"   # still the member
    assert browser.patch("/api/settings", json={"proxy_url": "x"}).status_code == 403


def test_owner_lock_without_an_account_cookie_is_a_no_op(client, owner):
    r = client.post("/api/auth/owner-lock")
    assert r.status_code == 200 and r.json() == {"ok": True, "locked": False}


def test_an_expired_unlock_is_locked_again(browser, test_db, monkeypatch):
    set_setting(test_db, "proxy_url", "")
    _unlock(browser)
    real = A._now
    monkeypatch.setattr(A, "_now", lambda: real() + 16 * 60)
    assert browser.patch("/api/settings", json={"proxy_url": "late"}).status_code == 403


def test_unlocked_member_may_start_owner_only_jobs(browser, test_db, monkeypatch):
    import backend.main as main_mod
    calls = []
    monkeypatch.setattr(main_mod, "launch_background", lambda job_type, *a, **k: calls.append(job_type) or "run-1")
    assert browser.post("/api/db/backup").status_code == 403
    _unlock(browser)
    assert browser.post("/api/db/backup").status_code in (200, 202)
    assert calls


def test_sections_carry_the_member_mode(browser, test_db, member, client):
    test_db.add(SectionPolicy(account_id=member.id, section="models", mode="own"))
    test_db.commit()
    rows = {s["id"]: s for s in browser.get("/api/settings/sections").json()}
    assert rows["models"]["mode"] == "own"
    assert rows["scoring"]["mode"] == "inherit"
    assert rows["advanced"]["mode"] == "owner_only"
    assert rows["notifications"]["mode"] == "own_always"


def test_owner_sections_have_no_mode(client, owner):
    rows = client.get("/api/settings/sections").json()
    assert all("mode" not in s for s in rows)
