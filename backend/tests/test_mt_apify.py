"""PR #28 review probe: whose Apify token does a search run with?

A is the owner (install values), B a member. The token is a paid credential, so a member
on its own 'apify' section must use its own token and never fall back to the owner's; a
member on 'inherit' runs on the owner's (the owner pays, by choice).
"""
import pytest

from backend.accounts.context import use_account
from backend.models.db import Account, SectionPolicy, Setting
from backend.scraper.sources import apify
from backend.tests.r4_support import set_setting


@pytest.fixture
def accounts(test_db):
    a = test_db.query(Account).filter(Account.role == "owner").one()
    b = Account(name="Partner", role="member")
    test_db.add(b)
    test_db.commit()
    return a.id, b.id


def _own(db, account_id, key, value):
    db.add(Setting(key=key, value=value, account_id=account_id))
    db.commit()


def test_token_key_has_a_policy_section():
    from backend.accounts.settings import SETTING_SECTIONS, section_of
    assert section_of("apify_api_key") == "apify"
    assert SETTING_SECTIONS["apify"]["scope"] == "policy"
    assert SETTING_SECTIONS["apify"]["default"] == "own"


def test_member_on_own_never_uses_the_owner_token(test_db, accounts):
    a, b = accounts
    set_setting(test_db, "apify_api_key", "tok-owner")
    with use_account(a):
        assert apify._token() == "tok-owner"
    with use_account(b):
        with pytest.raises(apify.ApifyError, match="not set"):
            apify._token()


def test_member_on_own_uses_its_own_token(test_db, accounts):
    a, b = accounts
    set_setting(test_db, "apify_api_key", "tok-owner")
    _own(test_db, b, "apify_api_key", "tok-member")
    with use_account(b):
        assert apify._token() == "tok-member"
    with use_account(a):
        assert apify._token() == "tok-owner"


def test_member_on_inherit_runs_on_the_owner_token(test_db, accounts):
    a, b = accounts
    set_setting(test_db, "apify_api_key", "tok-owner")
    _own(test_db, b, "apify_api_key", "tok-member")
    test_db.add(SectionPolicy(account_id=b, section="apify", mode="inherit"))
    test_db.commit()
    with use_account(b):
        assert apify._token() == "tok-owner"


async def _fake_account_name():
    return "owner-login"


@pytest.mark.asyncio
async def test_check_key_hides_the_owner_account_from_an_inheriting_member(test_db, accounts, monkeypatch):
    """M4: a member on 'inherit' checks the owner's key; it learns only that the key works."""
    from backend.api.routes_settings import check_apify_key
    a, b = accounts
    monkeypatch.setattr(apify, "account_name", _fake_account_name)
    test_db.add(SectionPolicy(account_id=b, section="apify", mode="inherit"))
    test_db.commit()
    with use_account(b):
        assert await check_apify_key(db=test_db) == {"ok": True, "username": None, "inherited": True}
    with use_account(a):
        assert await check_apify_key(db=test_db) == {"ok": True, "username": "owner-login"}


@pytest.mark.asyncio
async def test_check_key_names_a_member_own_account(test_db, accounts, monkeypatch):
    from backend.api.routes_settings import check_apify_key
    _, b = accounts
    monkeypatch.setattr(apify, "account_name", _fake_account_name)
    with use_account(b):    # 'own' is the default
        assert await check_apify_key(db=test_db) == {"ok": True, "username": "owner-login"}
