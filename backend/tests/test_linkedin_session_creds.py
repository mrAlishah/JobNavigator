"""Which LinkedIn account the session refresh signs in with: the mock account while
`linkedin_use_mock_account` is on, the personal account when it is off. Unset, the
switch follows whether a mock email exists."""
import pytest

from backend import refresh_linkedin_session as rls
from backend.models.db import Setting


def _set(db, **values):
    for key, value in values.items():
        db.add(Setting(key=key, value=value))
    db.commit()


def _switch(db):
    return db.query(Setting).filter(Setting.key == "linkedin_use_mock_account").one().value


@pytest.fixture
def logins(monkeypatch):
    """Record every login attempt instead of starting a browser."""
    seen = []

    async def fake_login(email, password, fresh=False):
        seen.append((email, password, fresh))
        return 0

    monkeypatch.setattr(rls, "_login_and_save", fake_login)
    rls.STATE.update(phase="idle", detail="")
    return seen


async def test_mock_account_is_used_when_the_switch_is_absent_and_a_mock_email_exists(test_db, logins):
    _set(test_db, linkedin_mock_email="mock@x.io", linkedin_mock_password="m",
         linkedin_email="me@x.io", linkedin_password="p")
    assert await rls.run_refresh() == 0
    assert logins == [("mock@x.io", "m", True)]


async def test_personal_account_is_used_when_the_switch_is_absent_and_no_mock_email_exists(test_db, logins):
    _set(test_db, linkedin_email="me@x.io", linkedin_password="p")
    assert await rls.run_refresh() == 0
    assert logins == [("me@x.io", "p", False)]


async def test_personal_account_is_used_when_the_switch_is_off(test_db, logins):
    _set(test_db, linkedin_use_mock_account="false",
         linkedin_mock_email="mock@x.io", linkedin_mock_password="m",
         linkedin_email="me@x.io", linkedin_password="p")
    assert await rls.run_refresh() == 0
    # The personal login may reuse the personal scraper's cookies, so it is not fresh.
    assert logins == [("me@x.io", "p", False)]
    assert rls.STATE["phase"] == "ok"


async def test_missing_mock_account_names_the_switch(test_db, logins):
    _set(test_db, linkedin_use_mock_account="true",
         linkedin_email="me@x.io", linkedin_password="p")
    assert await rls.run_refresh() == 2
    assert logins == []
    assert rls.STATE["phase"] == "failed"
    assert "Use a mock account" in rls.STATE["detail"]


async def test_missing_personal_account_names_the_personal_fields(test_db, logins):
    _set(test_db, linkedin_use_mock_account="false",
         linkedin_mock_email="mock@x.io", linkedin_mock_password="m")
    assert await rls.run_refresh() == 2
    assert logins == []
    assert "Personal email/password are not set" in rls.STATE["detail"]


def test_seed_starts_a_new_db_on_the_personal_account(test_db):
    from backend.seed import seed_settings
    seed_settings(test_db)
    assert _switch(test_db) == "false"


def test_seed_keeps_an_existing_mock_account_in_use(test_db):
    from backend.seed import seed_settings
    _set(test_db, linkedin_mock_email="mock@x.io")
    seed_settings(test_db)
    assert _switch(test_db) == "true"


def test_seed_leaves_a_chosen_switch_alone(test_db):
    from backend.seed import seed_settings
    _set(test_db, linkedin_mock_email="mock@x.io", linkedin_use_mock_account="false")
    seed_settings(test_db)
    assert _switch(test_db) == "false"
