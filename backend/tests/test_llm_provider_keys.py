"""One API key per provider (3.1): the startup migration from the 3.0 per-slot keys, and the
settings API around the new keys. Resolution and dispatch are in test_llm_key_routing.py."""
import pytest

from backend.accounts import settings as S
from backend.accounts.context import use_account
from backend.analyzer import llm_client as L
from backend.models.db import Account, SectionPolicy, Setting
from backend.seed import PROVIDER_KEYS_MIGRATED_KEY, migrate_llm_provider_keys
from backend.tests.r4_support import ACT_AS, client, set_setting  # noqa: F401 (fixture)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENCODE_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def member(test_db):
    b = Account(name="Partner", role="member")
    test_db.add(b)
    test_db.commit()
    return b.id


def _own(db, account_id, key, value):
    db.add(Setting(key=key, value=value, account_id=account_id))
    db.commit()


def _install(db, key):
    return (S.install_row(db, key).value if S.install_row(db, key) is not None else None)


def _member_value(db, account_id, key):
    row = db.query(Setting).filter(Setting.key == key, Setting.account_id == account_id).first()
    return row.value if row is not None else None


# ── migration ────────────────────────────────────────────────────────────────

def test_an_install_without_keys_writes_only_the_marker(test_db):
    assert migrate_llm_provider_keys(test_db) == {}
    assert _install(test_db, PROVIDER_KEYS_MIGRATED_KEY) == "1"
    for setting in S.PROVIDER_KEY_SETTINGS.values():
        assert not _install(test_db, setting)


def test_the_primary_key_moves_to_its_provider(test_db):
    set_setting(test_db, "llm_provider", "openai")
    set_setting(test_db, "llm_api_key", " sk-openai ")
    migrate_llm_provider_keys(test_db)
    assert _install(test_db, "openai_api_key") == "sk-openai"


def test_an_empty_primary_provider_means_claude_api(test_db):
    set_setting(test_db, "llm_api_key", "sk-ant")
    migrate_llm_provider_keys(test_db)
    assert _install(test_db, "anthropic_api_key") == "sk-ant"


def test_each_slot_key_goes_to_its_own_provider(test_db):
    set_setting(test_db, "llm_provider", "claude_api")
    set_setting(test_db, "llm_api_key", "sk-ant")
    set_setting(test_db, "scoring_llm_provider", "openrouter")
    set_setting(test_db, "scoring_llm_api_key", "sk-or")
    set_setting(test_db, "llm_fallback_provider", "openai")
    set_setting(test_db, "llm_fallback_api_key", "sk-openai")
    set_setting(test_db, "email_llm_provider", "opencode_go")
    set_setting(test_db, "email_llm_api_key", "sk-go")
    migrate_llm_provider_keys(test_db)
    assert [_install(test_db, k) for k in ("anthropic_api_key", "openrouter_api_key",
                                           "openai_api_key", "opencode_api_key")] == ["sk-ant", "sk-or", "sk-openai", "sk-go"]


def test_on_a_conflict_the_primary_key_wins_and_the_log_names_the_others(test_db, caplog):
    set_setting(test_db, "llm_provider", "claude_api")
    set_setting(test_db, "llm_api_key", "sk-ant-primary")
    set_setting(test_db, "cv_tailor_llm_provider", "claude_api")
    set_setting(test_db, "cv_tailor_llm_api_key", "sk-ant-other")
    set_setting(test_db, "autofill_llm_provider", "claude_api")
    set_setting(test_db, "autofill_llm_api_key", "sk-ant-primary")       # same key: no conflict
    migrate_llm_provider_keys(test_db)
    assert _install(test_db, "anthropic_api_key") == "sk-ant-primary"
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1 and "cv_tailor_llm" in warnings[0] and "sk-ant" not in warnings[0]
    assert _install(test_db, "cv_tailor_llm_api_key") == "sk-ant-other"  # legacy row kept


def test_a_key_in_a_slot_without_a_provider_is_not_migrated(test_db):
    """A switched-off override (or the orphan fallback key) was never in use: it must not start billing."""
    set_setting(test_db, "llm_provider", "claude_code")
    set_setting(test_db, "llm_fallback_api_key", "sk-ant-orphan")
    set_setting(test_db, "autofill_llm_api_key", "sk-proj-stale")
    migrate_llm_provider_keys(test_db)
    for setting in S.PROVIDER_KEY_SETTINGS.values():
        assert not _install(test_db, setting)


def test_a_key_already_set_is_never_overwritten(test_db):
    set_setting(test_db, "llm_provider", "claude_api")
    set_setting(test_db, "llm_api_key", "sk-ant-old")
    set_setting(test_db, "anthropic_api_key", "sk-ant-new")
    migrate_llm_provider_keys(test_db)
    assert _install(test_db, "anthropic_api_key") == "sk-ant-new"


def test_it_runs_once_and_a_cleared_key_stays_cleared(test_db):
    set_setting(test_db, "llm_provider", "claude_api")
    set_setting(test_db, "llm_api_key", "sk-ant")
    migrate_llm_provider_keys(test_db)
    set_setting(test_db, "anthropic_api_key", "")
    assert migrate_llm_provider_keys(test_db) == {}
    assert _install(test_db, "anthropic_api_key") == ""
    assert test_db.query(Setting).filter(Setting.key == PROVIDER_KEYS_MIGRATED_KEY).count() == 1


def test_member_rows_migrate_into_member_rows(test_db, member):
    set_setting(test_db, "llm_provider", "claude_api")
    set_setting(test_db, "llm_api_key", "sk-ant-owner")
    _own(test_db, member, "llm_provider", "openai")
    _own(test_db, member, "llm_api_key", "sk-openai-member")
    migrate_llm_provider_keys(test_db)
    assert _install(test_db, "anthropic_api_key") == "sk-ant-owner"
    assert _install(test_db, "openai_api_key") in (None, "")
    assert _member_value(test_db, member, "openai_api_key") == "sk-openai-member"
    assert _member_value(test_db, member, "anthropic_api_key") is None


def test_a_member_slot_follows_its_own_primary_provider(test_db, member):
    """The member's effective providers come from its own rows only (as its 'own' mode resolves them)."""
    set_setting(test_db, "llm_provider", "openai")
    _own(test_db, member, "llm_api_key", "sk-ant-member")    # no own llm_provider: claude_api
    migrate_llm_provider_keys(test_db)
    assert _member_value(test_db, member, "anthropic_api_key") == "sk-ant-member"


def test_migrated_keys_are_what_dispatch_resolves(test_db):
    set_setting(test_db, "llm_provider", "claude_api")
    set_setting(test_db, "llm_api_key", "sk-ant")
    set_setting(test_db, "autofill_llm_provider", "openai")
    set_setting(test_db, "autofill_llm_api_key", "sk-openai")
    migrate_llm_provider_keys(test_db)
    assert L.resolve_llm_config("", db=test_db)["api_key"] == "sk-ant"
    assert L.resolve_llm_config("autofill", db=test_db)["api_key"] == "sk-openai"
    assert L.resolve_llm_config("scoring", db=test_db)["api_key"] == "sk-ant"


# ── settings API ─────────────────────────────────────────────────────────────

def test_provider_keys_are_in_the_models_section(test_db):
    for setting in S.PROVIDER_KEY_SETTINGS.values():
        assert S.section_of(setting) == "models"
    for legacy in S.LEGACY_LLM_KEY_SETTINGS:
        assert legacy not in S.SETTING_SECTIONS["models"]["keys"]


def test_get_redacts_the_provider_keys_and_hides_the_legacy_ones(client, test_db):
    set_setting(test_db, "openai_api_key", "sk-openai")
    set_setting(test_db, "llm_api_key", "sk-legacy")
    set_setting(test_db, PROVIDER_KEYS_MIGRATED_KEY, "1")
    body = client.get("/api/settings").json()
    assert body["openai_api_key"] == "•" * 6
    assert "llm_api_key" not in body and PROVIDER_KEYS_MIGRATED_KEY not in body


def test_legacy_keys_cannot_be_written(client, test_db):
    for legacy in S.LEGACY_LLM_KEY_SETTINGS:
        assert client.patch("/api/settings", json={legacy: "sk-x"}).status_code == 400, legacy
    assert client.patch("/api/settings", json={PROVIDER_KEYS_MIGRATED_KEY: ""}).status_code == 400


def test_a_provider_key_is_written_and_used(client, test_db):
    set_setting(test_db, "llm_provider", "openrouter")
    assert client.patch("/api/settings", json={"openrouter_api_key": "sk-or"}).status_code == 200
    assert L.resolve_llm_config("", db=test_db)["api_key"] == "sk-or"


def test_member_on_inherit_cannot_write_and_reads_the_owner_key(client, test_db, member):
    set_setting(test_db, "anthropic_api_key", "sk-ant-owner")
    client.headers[ACT_AS] = str(member)
    assert client.patch("/api/settings", json={"anthropic_api_key": "sk-mine"}).status_code == 403
    with use_account(member):
        assert L.resolve_llm_config("", db=test_db)["api_key"] == "sk-ant-owner"


def test_member_on_own_writes_its_own_key(client, test_db, member):
    test_db.add(SectionPolicy(account_id=member, section="models", mode="own"))
    test_db.commit()
    set_setting(test_db, "anthropic_api_key", "sk-ant-owner")
    client.headers[ACT_AS] = str(member)
    assert client.patch("/api/settings", json={"anthropic_api_key": "sk-ant-mine"}).status_code == 200
    assert _install(test_db, "anthropic_api_key") == "sk-ant-owner"
    with use_account(member):
        assert L.resolve_llm_config("", db=test_db)["api_key"] == "sk-ant-mine"


@pytest.mark.asyncio
async def test_openrouter_env_key_for_the_owner_never_for_a_member_on_own(test_db, member, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-env")
    assert L._openrouter_key("") == "sk-or-env"
    test_db.add(SectionPolicy(account_id=member, section="models", mode="own"))
    test_db.commit()
    with use_account(member):
        with pytest.raises(L.NonRetryableLLMError, match="No API key for OpenRouter"):
            L._openrouter_key("")
