"""telemetry/level.py and GET /api/telemetry: the owner's choice, the JN_TELEMETRY override."""
import pytest
from fastapi import HTTPException

from backend.accounts.context import use_account
from backend.api import routes_settings as R
from backend.models.db import Account, Setting
from backend.telemetry import level as L
from backend.tests.r4_support import client, set_setting  # noqa: F401 (fixture)


@pytest.fixture(autouse=True)
def no_env(monkeypatch):
    monkeypatch.delenv("JN_TELEMETRY", raising=False)


@pytest.fixture
def accounts(test_db):
    a = test_db.query(Account).filter(Account.role == "owner").one()
    b = Account(name="Partner", role="member")
    test_db.add(b)
    test_db.commit()
    return a.id, b.id


def test_unset_is_unset_and_not_asked(test_db):
    assert L.effective_level(test_db) == "unset"
    assert not L.sends_anything(L.effective_level(test_db))


@pytest.mark.parametrize("value", ["full", "essentials"])
def test_setting_full_and_essentials(test_db, value):
    set_setting(test_db, "telemetry_level", value)
    assert L.effective_level(test_db) == value and L.sends_anything(value)


@pytest.mark.parametrize("env,expected", [("full", "full"), ("essentials", "essentials"),
                                          ("limited", "essentials"), ("OFF", "off"), (" off ", "off")])
def test_env_overrides_and_locks(test_db, monkeypatch, env, expected):
    set_setting(test_db, "telemetry_level", "full")
    monkeypatch.setenv("JN_TELEMETRY", env)
    assert L.env_level() == expected and L.effective_level(test_db) == expected


def test_garbage_env_is_ignored(test_db, monkeypatch):
    set_setting(test_db, "telemetry_level", "essentials")
    monkeypatch.setenv("JN_TELEMETRY", "maybe")
    assert L.env_level() is None and L.effective_level(test_db) == "essentials"


def test_install_id_is_created_once(test_db):
    first = L.install_id(test_db)
    assert len(first) == 36 and L.install_id(test_db) == first
    assert test_db.query(Setting).filter(Setting.key == "telemetry_install_id").count() == 1


def test_owner_sets_the_level_and_only_known_values(test_db, accounts):
    a, _ = accounts
    with use_account(a):
        R.update_settings({"telemetry_level": "essentials"}, db=test_db)
        with pytest.raises(HTTPException) as e:
            R.update_settings({"telemetry_level": "off"}, db=test_db)   # off is env-only
    assert e.value.status_code == 400
    assert L.chosen_level(test_db) == "essentials"


def test_member_cannot_write_telemetry_level(test_db, accounts):
    _, b = accounts
    with use_account(b):
        with pytest.raises(HTTPException) as e:
            R.update_settings({"telemetry_level": "full"}, db=test_db)
    assert e.value.status_code == 403


def test_route_reports_level_locked_asked(client, test_db, monkeypatch):
    r = client.get("/api/telemetry").json()
    assert r["level"] == "unset" and r["asked"] is False and r["locked"] is False and r["owner"] is True
    set_setting(test_db, "telemetry_level", "full")
    monkeypatch.setenv("JN_TELEMETRY", "off")
    r = client.get("/api/telemetry").json()
    assert r["level"] == "off" and r["asked"] is True and r["locked"] is True


@pytest.mark.parametrize("env", ["0", "false", "no", "none", "disabled", "OFF"])
def test_common_off_spellings_are_off(test_db, monkeypatch, env):
    set_setting(test_db, "telemetry_level", "full")
    monkeypatch.setenv("JN_TELEMETRY", env)
    assert L.effective_level(test_db) == "off"


def test_the_browser_is_never_told_where_umami_is(client):
    assert set(client.get("/api/telemetry").json()) == {"level", "locked", "asked", "owner"}


def test_install_state_is_never_served(client, test_db):
    L.install_id(test_db)
    set_setting(test_db, "telemetry_last_sent_at", "2026-10-26T00:00:00+00:00")
    served = client.get("/api/settings").json()
    assert "telemetry_install_id" not in served and "telemetry_last_sent_at" not in served
