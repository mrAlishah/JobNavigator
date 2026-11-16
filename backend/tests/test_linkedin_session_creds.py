"""Two LinkedIn sessions, each signed in on its own (direct-links plan, Task 8):

- main: the user's own account (linkedin_email / linkedin_password), the LinkedIn Personal jar.
- mock: the mock account (linkedin_mock_email / linkedin_mock_password), the extension jar that the
  LinkedIn import and the employer-link lookup use.

There is no switch any more: a Sign in names its session."""
import pytest

from backend import refresh_linkedin_session as rls
from backend.models.db import Setting


def _set(db, **values):
    for key, value in values.items():
        db.add(Setting(key=key, value=value))
    db.commit()


@pytest.fixture
def logins(monkeypatch):
    """Record every login attempt instead of starting a browser."""
    seen = []

    async def fake_login(email, password, which):
        seen.append((email, password, which))
        return 0

    monkeypatch.setattr(rls, "_login_and_save", fake_login)
    for which in (rls.MOCK, rls.MAIN):
        rls.state(which).update(phase="idle", detail="")
    return seen


@pytest.fixture
def jars(tmp_path, monkeypatch):
    from backend.accounts import credentials as C
    monkeypatch.setattr(C, "LI_EXTENSION_OWNER", str(tmp_path / "li_cookies.json"))
    monkeypatch.setattr(C, "LI_PERSONAL_OWNER", str(tmp_path / "linkedin_cookies.json"))
    return tmp_path


BOTH = dict(linkedin_mock_email="mock@x.io", linkedin_mock_password="m",
            linkedin_email="me@x.io", linkedin_password="p")


@pytest.mark.asyncio
async def test_mock_sign_in_uses_the_mock_account(test_db, logins):
    _set(test_db, **BOTH)
    assert await rls.run_refresh(rls.MOCK) == 0
    assert logins == [("mock@x.io", "m", "mock")]
    assert rls.state(rls.MOCK)["phase"] == "ok"


@pytest.mark.asyncio
async def test_main_sign_in_uses_the_main_account(test_db, logins):
    _set(test_db, **BOTH)
    assert await rls.run_refresh(rls.MAIN) == 0
    assert logins == [("me@x.io", "p", "main")]
    assert rls.state(rls.MAIN)["phase"] == "ok" and rls.state(rls.MOCK)["phase"] == "idle"


@pytest.mark.asyncio
async def test_a_missing_account_names_its_fields(test_db, logins):
    _set(test_db, linkedin_email="me@x.io", linkedin_password="p")
    assert await rls.run_refresh(rls.MOCK) == 2
    assert logins == [] and "Mock account email/password are not set" in rls.state(rls.MOCK)["detail"]
    _set(test_db, linkedin_mock_email="mock@x.io", linkedin_mock_password="m")
    test_db.query(Setting).filter(Setting.key.in_(("linkedin_email", "linkedin_password"))).delete()
    test_db.commit()
    assert await rls.run_refresh(rls.MAIN) == 2
    assert "Main account email/password are not set" in rls.state(rls.MAIN)["detail"]


def test_each_session_has_its_own_jar_state_and_pin(jars):
    assert rls.jar(rls.MOCK).endswith("li_cookies.json")
    assert rls.jar(rls.MAIN).endswith("linkedin_cookies.json")
    assert rls.state(rls.MOCK) is not rls.state(rls.MAIN)
    assert rls.pin_file(rls.MOCK) != rls.pin_file(rls.MAIN)


def test_a_sign_in_records_which_account_the_jar_holds(jars):
    assert rls.signed_in_as(rls.MOCK) is None
    rls.record_sign_in(rls.MOCK, "mock@x.io")
    assert rls.signed_in_as(rls.MOCK) == {"account": "mock", "email": "mock@x.io"}
    assert rls.signed_in_as(rls.MAIN) is None


def test_the_import_uses_the_mock_session_when_a_mock_account_is_set_else_the_main_one(test_db, jars):
    from backend.scraper.sources.linkedin_extension import _session_path
    _set(test_db, linkedin_email="me@x.io")
    assert _session_path() == rls.jar(rls.MAIN)
    _set(test_db, linkedin_mock_email="mock@x.io")
    assert _session_path() == rls.jar(rls.MOCK)


def test_the_switch_setting_is_gone(test_db):
    from backend.seed import DEFAULT_SETTINGS, cleanup_removed_settings
    from backend.accounts.settings import KEY_SECTION
    assert "linkedin_use_mock_account" not in DEFAULT_SETTINGS
    assert "linkedin_use_mock_account" not in KEY_SECTION
    _set(test_db, linkedin_use_mock_account="true")
    cleanup_removed_settings(test_db)
    assert test_db.query(Setting).filter(Setting.key == "linkedin_use_mock_account").count() == 0


def test_the_api_reports_each_session(test_db, api_client, jars):
    _set(test_db, dashboard_api_key="")
    rls.record_sign_in(rls.MOCK, "mock@x.io")
    open(rls.jar(rls.MOCK), "w").write("[]")
    mock = api_client.get("/api/linkedin/session?which=mock").json()
    main = api_client.get("/api/linkedin/session?which=main").json()
    assert mock["which"] == "mock" and mock["exists"] is True and mock["signed_in_as"] == "mock@x.io"
    assert main["which"] == "main" and main["exists"] is False
    assert api_client.get("/api/linkedin/session?which=other").status_code == 422


def test_sign_in_starts_the_named_sessions_sign_in(test_db, api_client, jars, monkeypatch):
    """The Sign in button: POST /linkedin/session/refresh?which= starts run_refresh(which)."""
    _set(test_db, dashboard_api_key="")
    started = []

    async def fake_refresh(which="mock"):
        started.append(which)
        return 0
    monkeypatch.setattr(rls, "run_refresh", fake_refresh)
    for which in ("main", "mock"):
        rls.state(which).update(phase="idle", detail="")
        r = api_client.post(f"/api/linkedin/session/refresh?which={which}")
        assert r.status_code == 202, r.text
    import time
    for _ in range(50):
        if len(started) == 2:
            break
        time.sleep(0.05)
    assert sorted(started) == ["main", "mock"]


def test_a_fresh_session_reads_signed_in_today(test_db, api_client, jars):
    _set(test_db, dashboard_api_key="", linkedin_mock_email="mock@x.io")
    open(rls.jar(rls.MOCK), "w").write("[]")
    rls.record_sign_in(rls.MOCK, "mock@x.io")
    body = api_client.get("/api/linkedin/session?which=mock").json()
    assert body["status"] == "ok" and body["summary"] == "Signed in today."



def test_without_a_mock_account_the_old_session_becomes_the_main_one(test_db, jars):
    """Review #5: an install that ran the extension session on the personal account (no mock email)
    keeps it: the old jar is copied to the main jar the import now reads."""
    from backend.scraper.sources.linkedin_extension import _session_path
    open(rls.jar(rls.MOCK), "w").write('[{"name": "li_at", "value": "personal"}]')
    assert _session_path() == rls.jar(rls.MAIN)
    assert open(rls.jar(rls.MAIN)).read() == '[{"name": "li_at", "value": "personal"}]'


def test_a_member_on_inherit_does_not_see_the_owners_email(test_db, api_client, jars, monkeypatch):
    """Review #9: signed_in_as is hidden when the session shown is the owner's."""
    from backend.api import routes_linkedin as rl
    _set(test_db, dashboard_api_key="")
    rls.record_sign_in(rls.MOCK, "owner-mock@x.io")
    monkeypatch.setattr(rl, "_shared_from_owner", lambda: True)
    body = api_client.get("/api/linkedin/session?which=mock").json()
    assert body["shared"] is True and body["signed_in_as"] is None
