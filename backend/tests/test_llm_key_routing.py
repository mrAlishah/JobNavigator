"""3.0 key routing fixes (qa-findings/KEYS-PLAN.md B1, B2, B3) and the owner-only LM Studio /
Ollama addresses.

B1: a slot on another provider than the Primary never gets the Primary's key; it uses its own
key or that provider's env key (owner / inherit only). B2: the fallback reuses the Primary's key
only on the same provider. B3: OpenAI dispatch falls back to OPENAI_API_KEY (the SDK reads it
only for api_key=None, and an empty string was sent); OpenRouter / OpenCode Go / LM Studio never
receive it.

3.1: one API key per provider (`anthropic_api_key`, `openai_api_key`, …) replaces the per-slot
keys, which are never read again (they were L-01's stale keys). L-02 Claude API and
OpenRouter with no key fail at once with a NonRetryable "No API key for <provider>". L-03 the
LM Studio / Ollama addresses are seeded empty (env, then default, at call time), the owner sees
the address in use, and a one-time cleanup blanks values that equal what the env/default gives.
"""
import types

import pytest

from backend.accounts import settings as S
from backend.accounts.context import use_account
from backend.analyzer import llm_client as L
from backend.models.db import Account, SectionPolicy, Setting
from backend.tests.r4_support import ACT_AS, client, set_setting  # noqa: F401 (fixture)

ANT_KEY = "sk-ant-primary"
ENV_OPENAI = "sk-openai-from-env"
FEATURES = ("scoring", "cv_tailor", "cover_letter", "autofill", "email")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENCODE_API_KEY", "OPENROUTER_API_KEY",
                 "LMSTUDIO_BASE_URL", "OLLAMA_BASE_URL"):
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


def _models_own(db, account_id):
    db.add(SectionPolicy(account_id=account_id, section="models", mode="own"))
    db.commit()


def _anthropic_primary(db, **slots):
    set_setting(db, "llm_provider", "claude_api")
    set_setting(db, "llm_model", "claude-sonnet-5")
    set_setting(db, "anthropic_api_key", ANT_KEY)
    for k, v in slots.items():
        set_setting(db, k, v)


class _FakeOpenAI:
    """Stands in for AsyncOpenAI; records the key and base URL each client was built with."""

    def __init__(self, seen, api_key, base_url):
        seen.append((api_key, base_url))
        self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self._create))

    async def _create(self, **kw):
        if kw.get("stream"):
            async def gen():
                yield types.SimpleNamespace(choices=[types.SimpleNamespace(
                    delta=types.SimpleNamespace(content="ok"))])
            return gen()
        msg = types.SimpleNamespace(content="ok")
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=msg, finish_reason="stop")],
            usage=types.SimpleNamespace(prompt_tokens=1, completion_tokens=1))


@pytest.fixture
def openai_seen(monkeypatch):
    seen = []
    monkeypatch.setattr(L, "_openai_client", lambda api_key, base_url, feature=None: _FakeOpenAI(seen, api_key, base_url))
    return seen


# ── B1: resolution ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("feature", FEATURES)
def test_other_provider_slot_without_key_never_gets_the_primary_key(test_db, feature):
    _anthropic_primary(test_db, **{f"{feature}_llm_provider": "openai", f"{feature}_llm_model": "gpt-5"})
    cfg = L.resolve_llm_config(feature, db=test_db)
    assert cfg["provider"] == "openai"
    assert cfg["api_key"] == ""


@pytest.mark.parametrize("feature", FEATURES)
def test_same_provider_slot_still_reuses_the_primary_key(test_db, feature):
    _anthropic_primary(test_db, **{f"{feature}_llm_provider": "claude_api",
                                   f"{feature}_llm_model": "claude-haiku-4-5"})
    cfg = L.resolve_llm_config(feature, db=test_db)
    assert (cfg["provider"], cfg["model"], cfg["api_key"]) == ("claude_api", "claude-haiku-4-5", ANT_KEY)


def test_slot_inheriting_the_primary_provider_reuses_its_key(test_db):
    _anthropic_primary(test_db)
    assert L.resolve_llm_config("autofill", db=test_db)["api_key"] == ANT_KEY


def test_slot_on_another_provider_gets_that_provider_key(test_db):
    _anthropic_primary(test_db, autofill_llm_provider="openai", openai_api_key="sk-openai")
    assert L.resolve_llm_config("autofill", db=test_db)["api_key"] == "sk-openai"
    assert L.resolve_llm_config("scoring", db=test_db)["api_key"] == ANT_KEY


def test_switching_a_slot_provider_back_keeps_both_keys(test_db):
    """The point of one key per provider: nothing is lost or re-pasted on a switch."""
    _anthropic_primary(test_db, openai_api_key="sk-openai")
    set_setting(test_db, "llm_provider", "openai")
    assert L.resolve_llm_config("", db=test_db)["api_key"] == "sk-openai"
    set_setting(test_db, "llm_provider", "claude_api")
    assert L.resolve_llm_config("", db=test_db)["api_key"] == ANT_KEY


# ── B1: dispatch ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_openai_slot_gets_the_env_openai_key_not_the_anthropic_key(test_db, monkeypatch, openai_seen):
    _anthropic_primary(test_db, autofill_llm_provider="openai", autofill_llm_model="gpt-5")
    monkeypatch.setenv("OPENAI_API_KEY", ENV_OPENAI)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env")
    res = await L.call_autofill_llm("p", "s")
    assert res["provider"] == "openai"
    assert openai_seen == [(ENV_OPENAI, None)]


@pytest.mark.asyncio
async def test_openai_slot_with_no_key_anywhere_fails_clearly(test_db, openai_seen):
    _anthropic_primary(test_db, autofill_llm_provider="openai", autofill_llm_model="gpt-5")
    with pytest.raises(L.NonRetryableLLMError, match="No API key for OpenAI"):
        await L.call_autofill_llm("p", "s")
    assert openai_seen == []   # nothing was sent, least of all the Anthropic key


@pytest.mark.asyncio
async def test_scoring_override_on_openai_through_call_llm(test_db, monkeypatch, openai_seen):
    _anthropic_primary(test_db, scoring_llm_provider="openai", scoring_llm_model="gpt-5")
    monkeypatch.setenv("OPENAI_API_KEY", ENV_OPENAI)
    cfg = L.resolve_llm_config("scoring", db=test_db)
    await L.call_llm("p", "s", 10, provider=cfg["provider"], model=cfg["model"], api_key=cfg["api_key"])
    assert [k for k, _b in openai_seen] == [ENV_OPENAI]


@pytest.mark.asyncio
async def test_member_on_own_never_gets_the_owner_or_env_key(test_db, member, monkeypatch, openai_seen):
    _anthropic_primary(test_db, autofill_llm_provider="openai", openai_api_key="sk-openai-owner")
    monkeypatch.setenv("OPENAI_API_KEY", ENV_OPENAI)
    _models_own(test_db, member)
    _own(test_db, member, "llm_provider", "claude_api")
    _own(test_db, member, "anthropic_api_key", "sk-ant-member")
    _own(test_db, member, "autofill_llm_provider", "openai")
    with use_account(member):
        cfg = L.resolve_llm_config("autofill", db=test_db)
        assert cfg["api_key"] == ""
        with pytest.raises(L.NonRetryableLLMError, match="No API key for OpenAI"):
            await L.call_autofill_llm("p", "s")
        # a same-provider slot reuses the member's own Primary key, not the owner's
        assert L.resolve_llm_config("scoring", db=test_db)["api_key"] == "sk-ant-member"
    assert openai_seen == []


@pytest.mark.asyncio
async def test_member_on_inherit_gets_the_env_openai_key(test_db, member, monkeypatch, openai_seen):
    _anthropic_primary(test_db, autofill_llm_provider="openai", autofill_llm_model="gpt-5")
    monkeypatch.setenv("OPENAI_API_KEY", ENV_OPENAI)
    with use_account(member):
        await L.call_autofill_llm("p", "s")
    assert openai_seen == [(ENV_OPENAI, None)]


# ── B2: fallback ─────────────────────────────────────────────────────────────

def _capture_dispatch(monkeypatch):
    calls = []

    async def fake_dispatch(provider, model, api_key, prompt, system, max_tokens, cached_prefix=None, effort=""):
        calls.append((provider, api_key))
        if len(calls) == 1:
            raise L.NonRetryableLLMError("primary down")
        return {"text": "ok", "usage": {}}

    monkeypatch.setattr(L, "_dispatch", fake_dispatch)
    return calls


@pytest.mark.asyncio
@pytest.mark.parametrize("fb_provider,expected", [("claude_api", ANT_KEY), ("openrouter", "")])
async def test_fallback_reuses_the_primary_key_only_on_the_same_provider(test_db, monkeypatch, fb_provider, expected):
    _anthropic_primary(test_db, llm_fallback_provider=fb_provider, llm_fallback_model="fb-model")
    calls = _capture_dispatch(monkeypatch)
    await L.call_llm("p", "s", 10)
    assert calls[1] == (fb_provider, expected)


@pytest.mark.asyncio
async def test_fallback_on_another_provider_gets_that_provider_key(test_db, monkeypatch):
    _anthropic_primary(test_db, llm_fallback_provider="openai", llm_fallback_model="fb-model",
                       openai_api_key="sk-openai")
    calls = _capture_dispatch(monkeypatch)
    await L.call_llm("p", "s", 10)
    assert calls[1] == ("openai", "sk-openai")


# ── B3: OpenAI env key, and only for OpenAI ──────────────────────────────────

@pytest.mark.asyncio
async def test_owner_openai_primary_with_only_the_env_key(test_db, monkeypatch, openai_seen):
    set_setting(test_db, "llm_provider", "openai")
    set_setting(test_db, "llm_model", "gpt-5")
    monkeypatch.setenv("OPENAI_API_KEY", ENV_OPENAI)
    res = await L.call_llm("p", "s", 10)
    assert res["text"] == "ok"
    assert openai_seen == [(ENV_OPENAI, None)]


@pytest.mark.asyncio
async def test_openai_setting_beats_the_env_key(test_db, monkeypatch, openai_seen):
    set_setting(test_db, "llm_provider", "openai")
    set_setting(test_db, "openai_api_key", "sk-openai-setting")
    monkeypatch.setenv("OPENAI_API_KEY", ENV_OPENAI)
    await L.call_llm("p", "s", 10)
    assert openai_seen == [("sk-openai-setting", None)]


@pytest.mark.asyncio
async def test_streaming_openai_uses_the_env_key(test_db, monkeypatch, openai_seen):
    _anthropic_primary(test_db, autofill_llm_provider="openai", autofill_llm_model="gpt-5")
    monkeypatch.setenv("OPENAI_API_KEY", ENV_OPENAI)
    out = [c async for c in L.call_autofill_llm_stream("p", "s")]
    assert out == ["ok"]
    assert openai_seen == [(ENV_OPENAI, None)]


@pytest.mark.asyncio
@pytest.mark.parametrize("provider,base", [
    ("opencode_go", L.OPENCODE_GO_BASE_URL),
    ("lmstudio", "http://localhost:1234/v1"),
])
async def test_openai_env_key_never_reaches_compatible_endpoints(test_db, monkeypatch, openai_seen, provider, base):
    monkeypatch.setenv("OPENAI_API_KEY", ENV_OPENAI)
    await L._dispatch(provider, "m", "", "p", "s", 10)
    assert len(openai_seen) == 1
    key, sent_base = openai_seen[0]
    assert sent_base == base
    assert key != ENV_OPENAI
    assert key == ("lm-studio" if provider == "lmstudio" else "")


@pytest.mark.asyncio
async def test_openai_env_key_never_reaches_openrouter(test_db, monkeypatch, openai_seen):
    """OpenRouter with no key of its own now stops before sending anything (L-02)."""
    monkeypatch.setenv("OPENAI_API_KEY", ENV_OPENAI)
    with pytest.raises(L.NonRetryableLLMError, match="No API key for OpenRouter"):
        await L._dispatch("openrouter", "m", "", "p", "s", 10)
    assert openai_seen == []


def test_openai_client_never_hands_the_sdk_none(monkeypatch):
    """With api_key=None the SDK would read OPENAI_API_KEY itself."""
    monkeypatch.setenv("OPENAI_API_KEY", ENV_OPENAI)
    for base in (L.OPENROUTER_BASE_URL, L.OPENCODE_GO_BASE_URL, "http://localhost:1234/v1"):
        assert L._openai_client(None, base).api_key == ""


# ── LM Studio / Ollama addresses ─────────────────────────────────────────────

@pytest.mark.parametrize("provider", ["lmstudio", "ollama"])
def test_local_url_setting_then_env_then_default(test_db, monkeypatch, provider):
    key, env_name, default = L.LOCAL_LLM_URLS[provider]
    set_setting(test_db, key, "")
    assert L.local_base_url(provider) == default
    monkeypatch.setenv(env_name, "http://env-host:9/v1/")
    assert L.local_base_url(provider) == "http://env-host:9/v1"
    set_setting(test_db, key, " http://host.docker.internal:7/ ")
    assert L.local_base_url(provider) == "http://host.docker.internal:7"


def test_local_url_defaults_are_the_previous_addresses_and_seeds_are_empty():
    """L-03: the seed is "" so the env var is read at call time, not frozen at first boot."""
    from backend.seed import DEFAULT_SETTINGS
    assert L.LOCAL_LLM_URLS["lmstudio"][2] == "http://localhost:1234/v1"
    assert L.LOCAL_LLM_URLS["ollama"][2] == "http://localhost:11434"
    for key, _env, _default in L.LOCAL_LLM_URLS.values():
        assert DEFAULT_SETTINGS[key][0] == ""


@pytest.mark.asyncio
async def test_lmstudio_dispatch_uses_the_setting(test_db, openai_seen):
    set_setting(test_db, "lmstudio_base_url", "http://host.docker.internal:1234/v1")
    await L._dispatch("lmstudio", "qwen", "", "p", "s", 10)
    assert openai_seen == [("lm-studio", "http://host.docker.internal:1234/v1")]


@pytest.mark.asyncio
async def test_ollama_dispatch_uses_the_setting(test_db, monkeypatch):
    import httpx
    set_setting(test_db, "ollama_base_url", "http://host.docker.internal:11434/")
    posted = []

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None):
            posted.append(url)
            return httpx.Response(200, json={"response": " hi ", "prompt_eval_count": 1, "eval_count": 1},
                                  request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    res = await L._dispatch("ollama", "llama3", "", "p", "s", 10)
    assert res["text"] == "hi"
    assert posted == ["http://host.docker.internal:11434/api/generate"]


def test_local_urls_are_one_owner_only_section_shown_in_models():
    sec = S.SETTING_SECTIONS["llm_local"]
    assert (sec["scope"], sec["ui"]) == ("owner_only", ["models"])
    assert set(sec["keys"]) == {"lmstudio_base_url", "ollama_base_url"}
    assert S.section_of("lmstudio_base_url") == S.section_of("ollama_base_url") == "llm_local"


def test_owner_patches_local_urls_and_bad_ones_are_400(client, test_db):
    r = client.patch("/api/settings", json={"lmstudio_base_url": "http://host.docker.internal:1234/v1",
                                            "ollama_base_url": ""})
    assert r.status_code == 200, r.text
    assert S.get_install_setting(test_db, "lmstudio_base_url") == "http://host.docker.internal:1234/v1"
    for bad in ("file:///etc/passwd", "localhost:11434", "gopher://x"):
        r = client.patch("/api/settings", json={"ollama_base_url": bad})
        assert r.status_code == 400, bad
    assert S.get_install_setting(test_db, "ollama_base_url") == ""


def test_member_cannot_write_local_urls_and_sees_them_masked(client, test_db, member):
    set_setting(test_db, "ollama_base_url", "http://user:pw@host.docker.internal:11434")
    client.headers[ACT_AS] = str(member)
    r = client.patch("/api/settings", json={"ollama_base_url": "http://169.254.169.254"})
    assert r.status_code == 403
    _models_own(test_db, member)   # owning Models does not give the addresses
    r = client.patch("/api/settings", json={"lmstudio_base_url": "http://169.254.169.254"})
    assert r.status_code == 403
    body = client.get("/api/settings").json()
    assert body["ollama_base_url"] == "http://••••@host.docker.internal:11434"
    assert body["_meta"]["ollama_base_url"]["editable"] is False
    assert S.get_install_setting(test_db, "ollama_base_url") == "http://user:pw@host.docker.internal:11434"
    assert "_effective" not in body   # the addresses in use are the owner's (L-03)


# ── shared fakes for the Claude API ──────────────────────────────────────────

class _FakeAnthropic:
    """Stands in for anthropic.AsyncAnthropic; records the key each client was built with."""

    def __init__(self, seen, api_key):
        seen.append(api_key)
        self.messages = types.SimpleNamespace(create=self._create, stream=self._stream)

    async def _create(self, **kw):
        return types.SimpleNamespace(
            content=[types.SimpleNamespace(type="text", text="ok")], stop_reason="end_turn",
            usage=types.SimpleNamespace(input_tokens=1, output_tokens=1))

    def _stream(self, **kw):
        class _Stream:
            async def __aenter__(self):
                async def gen():
                    yield "ok"
                return types.SimpleNamespace(text_stream=gen())

            async def __aexit__(self, *a):
                return False
        return _Stream()


@pytest.fixture
def anthropic_seen(monkeypatch):
    import anthropic
    seen = []
    monkeypatch.setattr(anthropic, "AsyncAnthropic", lambda api_key=None, **kw: _FakeAnthropic(seen, api_key))
    return seen


def _count_dispatch(monkeypatch):
    """Wrap _dispatch to count the attempts call_llm makes."""
    calls = []
    real = L._dispatch

    async def counting(provider, *a, **kw):
        calls.append(provider)
        return await real(provider, *a, **kw)

    monkeypatch.setattr(L, "_dispatch", counting)
    return calls


# ── the per-slot keys of 3.0 are never read (L-01's stale keys) ─────────────

@pytest.mark.parametrize("feature", FEATURES)
def test_a_legacy_slot_key_is_never_read(test_db, feature):
    _anthropic_primary(test_db, **{f"{feature}_llm_api_key": "sk-proj-STALE-OPENAI",
                                   f"{feature}_llm_provider": "openai"})
    assert L.resolve_llm_config(feature, db=test_db)["api_key"] == ""
    set_setting(test_db, f"{feature}_llm_provider", "")
    assert L.resolve_llm_config(feature, db=test_db)["api_key"] == ANT_KEY


def test_the_legacy_primary_key_is_never_read(test_db):
    set_setting(test_db, "llm_provider", "claude_api")
    set_setting(test_db, "llm_api_key", "sk-ant-legacy")
    assert L.resolve_llm_config("", db=test_db)["api_key"] == ""


def test_primary_key_still_counts_with_the_provider_left_to_the_default(test_db):
    """The Primary has no slot to follow: an empty llm_provider means the default provider."""
    set_setting(test_db, "llm_provider", "")
    set_setting(test_db, "anthropic_api_key", ANT_KEY)
    assert L.resolve_llm_config("", db=test_db)["api_key"] == ANT_KEY


@pytest.mark.asyncio
async def test_stale_openai_key_never_reaches_anthropic(test_db, monkeypatch, anthropic_seen):
    """The R30 probe: Autofill once ran on OpenAI, the override was switched off, the Primary is Claude API."""
    _anthropic_primary(test_db, autofill_llm_api_key="sk-proj-STALE-OPENAI", openai_api_key="sk-openai")
    res = await L.call_autofill_llm("p", "s")
    assert res["provider"] == "claude_api"
    assert anthropic_seen == [ANT_KEY]


@pytest.mark.asyncio
async def test_a_legacy_slot_key_does_not_hide_the_env_key(test_db, monkeypatch, anthropic_seen):
    set_setting(test_db, "llm_provider", "claude_api")
    set_setting(test_db, "scoring_llm_api_key", "sk-proj-STALE-OPENAI")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env")
    cfg = L.resolve_llm_config("scoring", db=test_db)
    await L.call_llm("p", "s", 10, provider=cfg["provider"], model=cfg["model"], api_key=cfg["api_key"])
    assert anthropic_seen == ["sk-ant-env"]


def test_member_own_provider_key_is_used(test_db, member):
    _models_own(test_db, member)
    _own(test_db, member, "llm_provider", "claude_api")
    _own(test_db, member, "anthropic_api_key", "sk-ant-member")
    _own(test_db, member, "cover_letter_llm_api_key", "sk-proj-member-stale")
    with use_account(member):
        assert L.resolve_llm_config("cover_letter", db=test_db)["api_key"] == "sk-ant-member"


# ── L-02: Claude API and OpenRouter with no key ──────────────────────────────

@pytest.mark.asyncio
async def test_claude_api_with_no_key_anywhere_fails_once_and_clearly(test_db, monkeypatch, anthropic_seen):
    set_setting(test_db, "llm_provider", "claude_api")
    calls = _count_dispatch(monkeypatch)
    with pytest.raises(L.NonRetryableLLMError, match="No API key for Claude API"):
        await L.call_llm("p", "s", 10)
    assert calls == ["claude_api"]   # no retries
    assert anthropic_seen == []      # no SDK client, so no "Could not resolve authentication method"


@pytest.mark.asyncio
async def test_claude_api_owner_uses_the_env_key(test_db, monkeypatch, anthropic_seen):
    set_setting(test_db, "llm_provider", "claude_api")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env")
    res = await L.call_llm("p", "s", 10)
    assert res["text"] == "ok"
    assert anthropic_seen == ["sk-ant-env"]


@pytest.mark.asyncio
async def test_claude_api_member_on_inherit_uses_the_env_key(test_db, member, monkeypatch, anthropic_seen):
    set_setting(test_db, "llm_provider", "claude_api")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env")
    with use_account(member):
        await L.call_llm("p", "s", 10)
    assert anthropic_seen == ["sk-ant-env"]


@pytest.mark.asyncio
async def test_claude_api_member_on_own_never_gets_the_env_key(test_db, member, monkeypatch, anthropic_seen):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env")
    _models_own(test_db, member)
    _own(test_db, member, "llm_provider", "claude_api")
    with use_account(member):
        with pytest.raises(L.NonRetryableLLMError, match="No API key for Claude API"):
            await L.call_llm("p", "s", 10)
    assert anthropic_seen == []


@pytest.mark.asyncio
async def test_claude_api_with_no_key_falls_back_at_once(test_db, monkeypatch, anthropic_seen, openai_seen):
    set_setting(test_db, "llm_provider", "claude_api")
    set_setting(test_db, "llm_fallback_provider", "openai")
    set_setting(test_db, "llm_fallback_model", "gpt-5-mini")
    set_setting(test_db, "openai_api_key", "sk-openai-fb")
    calls = _count_dispatch(monkeypatch)
    res = await L.call_llm("p", "s", 10)
    assert (res["provider"], res["model"], res["text"]) == ("openai", "gpt-5-mini", "ok")
    assert calls == ["claude_api", "openai"]
    assert openai_seen == [("sk-openai-fb", None)]


@pytest.mark.asyncio
async def test_streaming_claude_api_with_no_key_fails_clearly(test_db, anthropic_seen):
    set_setting(test_db, "llm_provider", "claude_api")
    with pytest.raises(L.NonRetryableLLMError, match="No API key for Claude API"):
        [c async for c in L.call_autofill_llm_stream("p", "s")]
    assert anthropic_seen == []


@pytest.mark.asyncio
async def test_streaming_claude_api_uses_the_env_key(test_db, monkeypatch, anthropic_seen):
    set_setting(test_db, "llm_provider", "claude_api")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env")
    assert [c async for c in L.call_autofill_llm_stream("p", "s")] == ["ok"]
    assert anthropic_seen == ["sk-ant-env"]


@pytest.mark.asyncio
async def test_openrouter_with_no_key_fails_once_and_clearly(test_db, monkeypatch, openai_seen):
    set_setting(test_db, "llm_provider", "openrouter")
    set_setting(test_db, "llm_model", "openai/gpt-5")
    calls = _count_dispatch(monkeypatch)
    with pytest.raises(L.NonRetryableLLMError, match="No API key for OpenRouter"):
        await L.call_llm("p", "s", 10)
    assert calls == ["openrouter"]
    assert openai_seen == []   # no empty bearer sent


@pytest.mark.asyncio
async def test_streaming_openrouter_with_no_key_fails_clearly(test_db, openai_seen):
    _anthropic_primary(test_db, autofill_llm_provider="openrouter", autofill_llm_model="openai/gpt-5")
    with pytest.raises(L.NonRetryableLLMError, match="No API key for OpenRouter"):
        [c async for c in L.call_autofill_llm_stream("p", "s")]
    assert openai_seen == []


@pytest.mark.asyncio
async def test_openrouter_with_a_key_still_works(test_db, openai_seen):
    _anthropic_primary(test_db, autofill_llm_provider="openrouter", autofill_llm_model="openai/gpt-5",
                       openrouter_api_key="sk-or-slot")
    res = await L.call_autofill_llm("p", "s")
    assert res["text"] == "ok"
    assert openai_seen == [("sk-or-slot", L.OPENROUTER_BASE_URL)]


class _StatusError(Exception):
    def __init__(self, status_code):
        super().__init__(f"Error code: {status_code}")
        self.status_code = status_code


def _openai_raising(monkeypatch, status_code):
    async def create(**kw):
        raise _StatusError(status_code)

    client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create)))
    monkeypatch.setattr(L, "_openai_client", lambda api_key, base_url, feature=None: client)


@pytest.mark.asyncio
async def test_openrouter_401_is_not_retried(test_db, monkeypatch):
    _openai_raising(monkeypatch, 401)
    with pytest.raises(L.NonRetryableLLMError, match="OpenRouter rejected the API key"):
        await L._dispatch("openrouter", "openai/gpt-5", "sk-or-bad", "p", "s", 10)


@pytest.mark.asyncio
async def test_openrouter_other_errors_still_take_the_retry_path(test_db, monkeypatch):
    _openai_raising(monkeypatch, 503)
    with pytest.raises(_StatusError):
        await L._dispatch("openrouter", "openai/gpt-5", "sk-or", "p", "s", 10)


# ── L-03: local model server addresses ──────────────────────────────────────

@pytest.fixture
def local_env(monkeypatch):
    monkeypatch.setenv("LMSTUDIO_BASE_URL", "http://lm-user:lm-pw@lm-host:1234/v1")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://ollama-host:11434")


def test_defaults_never_serve_the_env_addresses(client, test_db, member, local_env):
    for headers in ({}, {ACT_AS: str(member)}):
        body = client.get("/api/settings/defaults", headers=headers).json()
        assert body["lmstudio_base_url"] == "" and body["ollama_base_url"] == ""
        assert "lm-host" not in str(body) and "ollama-host" not in str(body)


def test_owner_sees_the_address_in_use(client, test_db, local_env):
    set_setting(test_db, "lmstudio_base_url", "")
    set_setting(test_db, "ollama_base_url", "http://box:11434/")
    body = client.get("/api/settings").json()
    assert body["_effective"] == {"lmstudio_base_url": "http://lm-user:lm-pw@lm-host:1234/v1",
                                  "ollama_base_url": "http://box:11434"}
    assert body["lmstudio_base_url"] == ""


def test_owner_sees_the_default_address_with_no_env(client, test_db):
    body = client.get("/api/settings").json()
    assert body["_effective"] == {"lmstudio_base_url": "http://localhost:1234/v1",
                                  "ollama_base_url": "http://localhost:11434"}


def test_member_never_gets_the_address_in_use(client, test_db, member, local_env):
    client.headers[ACT_AS] = str(member)
    body = client.get("/api/settings").json()
    assert "_effective" not in body
    assert "lm-pw" not in str(body) and "ollama-host" not in str(body)


def _row(db, key):
    return db.query(Setting).filter(Setting.key == key, Setting.account_id.is_(None)).first()


def test_cleanup_blanks_the_old_seed_with_no_env(test_db):
    from backend.seed import clear_seeded_local_urls
    set_setting(test_db, "lmstudio_base_url", "http://localhost:1234/v1")
    set_setting(test_db, "ollama_base_url", "http://localhost:11434")
    assert sorted(clear_seeded_local_urls(test_db)) == ["lmstudio_base_url", "ollama_base_url"]
    assert S.get_install_setting(test_db, "lmstudio_base_url") == ""
    assert S.get_install_setting(test_db, "ollama_base_url") == ""
    assert L.local_base_url("lmstudio") == "http://localhost:1234/v1"   # same address as before


def test_cleanup_blanks_a_value_equal_to_the_env(test_db, monkeypatch):
    from backend.seed import clear_seeded_local_urls
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
    set_setting(test_db, "ollama_base_url", "http://host.docker.internal:11434/")
    before = L.local_base_url("ollama")
    assert clear_seeded_local_urls(test_db) == ["ollama_base_url"]
    assert L.local_base_url("ollama") == before


def test_cleanup_keeps_values_that_differ_from_env_or_default(test_db, monkeypatch):
    from backend.seed import clear_seeded_local_urls
    # seeded localhost before LMSTUDIO_BASE_URL was set: blanking would move the calls, so it stays
    monkeypatch.setenv("LMSTUDIO_BASE_URL", "http://host.docker.internal:1234/v1")
    set_setting(test_db, "lmstudio_base_url", "http://localhost:1234/v1")
    set_setting(test_db, "ollama_base_url", "http://gpu-box:11434")
    assert clear_seeded_local_urls(test_db) == []
    assert S.get_install_setting(test_db, "lmstudio_base_url") == "http://localhost:1234/v1"
    assert S.get_install_setting(test_db, "ollama_base_url") == "http://gpu-box:11434"


def test_cleanup_runs_once(test_db):
    from backend.seed import LOCAL_URLS_CLEARED_KEY, clear_seeded_local_urls
    set_setting(test_db, "ollama_base_url", "")
    assert clear_seeded_local_urls(test_db) == []
    assert _row(test_db, LOCAL_URLS_CLEARED_KEY) is not None
    # the owner later types the default address on purpose: a restart leaves it alone
    set_setting(test_db, "ollama_base_url", "http://localhost:11434")
    assert clear_seeded_local_urls(test_db) == []
    assert S.get_install_setting(test_db, "ollama_base_url") == "http://localhost:11434"
    assert test_db.query(Setting).filter(Setting.key == LOCAL_URLS_CLEARED_KEY).count() == 1


def test_cleanup_marker_is_never_served_or_writable(client, test_db):
    from backend.seed import LOCAL_URLS_CLEARED_KEY, clear_seeded_local_urls
    clear_seeded_local_urls(test_db)
    assert LOCAL_URLS_CLEARED_KEY not in client.get("/api/settings").json()
    assert client.patch("/api/settings", json={LOCAL_URLS_CLEARED_KEY: ""}).status_code == 400
