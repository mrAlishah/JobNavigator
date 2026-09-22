"""_dispatch must concatenate cached_prefix into the prompt for non-Anthropic providers, since only claude_api supports a separate cache_control block."""
import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_dispatch_concatenates_prefix_for_claude_code(monkeypatch):
    """claude_code provider receives cached_prefix + prompt combined (subprocess can't cache)."""
    captured = {}

    async def fake_claude_code(prompt, system, model, max_tokens):
        captured["prompt"] = prompt
        return {"text": '{"ok": 1}',
                "usage": {"input_tokens": 0, "output_tokens": 0,
                          "cache_read_tokens": 0, "cache_write_tokens": 0}}

    monkeypatch.setattr("backend.analyzer.llm_client._call_claude_code", fake_claude_code)

    from backend.analyzer.llm_client import _dispatch
    await _dispatch(
        provider="claude_code", model="claude-sonnet-4-6",
        api_key="",
        prompt="JOB DESCRIPTION: Senior PM role",
        system="rubric scorer",
        max_tokens=600,
        cached_prefix="RUBRIC + CVs + SCHEMA",
    )

    assert "RUBRIC + CVs + SCHEMA" in captured["prompt"]
    assert "JOB DESCRIPTION: Senior PM role" in captured["prompt"]
    # Prefix should appear before the suffix
    assert captured["prompt"].index("RUBRIC") < captured["prompt"].index("JOB DESCRIPTION")


@pytest.mark.asyncio
async def test_dispatch_concatenates_prefix_for_codex_cli(monkeypatch):
    """codex_cli receives cached_prefix + prompt combined."""
    captured = {}

    async def fake_codex_cli(prompt, system, model, max_tokens):
        captured["prompt"] = prompt
        return {"text": "ok", "usage": {"input_tokens": 1, "output_tokens": 1,
                                            "cache_read_tokens": 0, "cache_write_tokens": 0}}

    monkeypatch.setattr("backend.analyzer.llm_client._call_codex_cli", fake_codex_cli)
    from backend.analyzer.llm_client import _dispatch
    await _dispatch(
        provider="codex_cli", model="gpt-5.6-sol", api_key="",
        prompt="JOB DESCRIPTION", system="rubric scorer", max_tokens=600,
        cached_prefix="RUBRIC + CVs",
    )

    assert captured["prompt"] == "RUBRIC + CVs\n\nJOB DESCRIPTION"


@pytest.mark.asyncio
async def test_dispatch_concatenates_prefix_for_antigravity_cli(monkeypatch):
    """antigravity_cli receives cached_prefix + prompt combined (a CLI cannot cache)."""
    seen = {}

    async def fake_agy(prompt, system, model, max_tokens):
        seen["prompt"] = prompt
        return {"text": "ok", "usage": {"input_tokens": 1, "output_tokens": 1,
                                        "cache_read_tokens": 0, "cache_write_tokens": 0}}

    monkeypatch.setattr("backend.analyzer.llm_client._call_antigravity_cli", fake_agy)
    from backend.analyzer.llm_client import _dispatch
    await _dispatch(
        provider="antigravity_cli", model="gemini-3.8-flash-medium", api_key="",
        prompt="the question", system="s", max_tokens=10, cached_prefix="the resume",
    )
    assert seen["prompt"] == "the resume\n\nthe question"


@pytest.mark.asyncio
async def test_dispatch_concatenates_prefix_for_openai(monkeypatch):
    """openai provider receives cached_prefix + prompt combined."""
    captured = {}

    async def fake_openai(prompt, system, model, api_key, max_tokens):
        captured["prompt"] = prompt
        return {"text": "{}",
                "usage": {"input_tokens": 10, "output_tokens": 5,
                          "cache_read_tokens": 0, "cache_write_tokens": 0}}

    monkeypatch.setattr("backend.analyzer.llm_client._call_openai", fake_openai)

    from backend.analyzer.llm_client import _dispatch
    await _dispatch(
        provider="openai", model="gpt-4o",
        api_key="sk-test",
        prompt="JD text",
        system="sys",
        max_tokens=600,
        cached_prefix="RUBRIC HERE",
    )

    assert "RUBRIC HERE" in captured["prompt"]
    assert "JD text" in captured["prompt"]


@pytest.mark.asyncio
async def test_dispatch_no_prefix_passes_prompt_unchanged(monkeypatch):
    """When cached_prefix is None, the prompt goes through without modification."""
    captured = {}

    async def fake_claude_code(prompt, system, model, max_tokens):
        captured["prompt"] = prompt
        return {"text": "ok",
                "usage": {"input_tokens": 0, "output_tokens": 0,
                          "cache_read_tokens": 0, "cache_write_tokens": 0}}

    monkeypatch.setattr("backend.analyzer.llm_client._call_claude_code", fake_claude_code)

    from backend.analyzer.llm_client import _dispatch
    await _dispatch(
        provider="claude_code", model="claude-sonnet-4-6",
        api_key="",
        prompt="bare prompt",
        system="sys",
        max_tokens=50,
        cached_prefix=None,
    )

    assert captured["prompt"] == "bare prompt"


@pytest.mark.asyncio
async def test_dispatch_claude_api_still_uses_cache_control(monkeypatch):
    """claude_api branch passes cached_prefix through (NOT concatenated) so it uses cache_control."""
    captured = {}

    async def fake_claude_api(prompt, system, model, api_key, max_tokens, cached_prefix=None):
        captured["prompt"] = prompt
        captured["cached_prefix"] = cached_prefix
        return {"text": "ok",
                "usage": {"input_tokens": 0, "output_tokens": 0,
                          "cache_read_tokens": 0, "cache_write_tokens": 0}}

    monkeypatch.setattr("backend.analyzer.llm_client._call_claude_api", fake_claude_api)

    from backend.analyzer.llm_client import _dispatch
    await _dispatch(
        provider="claude_api", model="claude-sonnet-4-6",
        api_key="sk",
        prompt="JD only",
        system="sys",
        max_tokens=50,
        cached_prefix="RUBRIC",
    )

    # For Anthropic, the prefix must stay separate (it becomes the cache_control block)
    assert captured["prompt"] == "JD only"
    assert captured["cached_prefix"] == "RUBRIC"


@pytest.mark.asyncio
async def test_dispatch_lmstudio_routes_to_openai_client(monkeypatch):
    """lmstudio is OpenAI-API-compatible — routes to _call_openai with the :1234 base URL and a dummy key when none is set."""
    captured = {}

    async def fake_openai(prompt, system, model, api_key, max_tokens, base_url=None, extra_body=None):
        captured["prompt"] = prompt
        captured["api_key"] = api_key
        captured["base_url"] = base_url
        captured["extra_body"] = extra_body
        return {"text": "ok",
                "usage": {"input_tokens": 1, "output_tokens": 1,
                          "cache_read_tokens": 0, "cache_write_tokens": 0}}

    monkeypatch.setattr("backend.analyzer.llm_client._call_openai", fake_openai)
    monkeypatch.delenv("LMSTUDIO_BASE_URL", raising=False)

    from backend.analyzer.llm_client import _dispatch
    await _dispatch(
        provider="lmstudio", model="qwen2.5-7b-instruct",
        api_key="", prompt="JD text", system="sys", max_tokens=600,
        cached_prefix="RUBRIC HERE",
    )

    assert captured["base_url"] == "http://localhost:1234/v1"
    assert captured["api_key"] == "lm-studio"  # dummy — LM Studio ignores it
    assert captured["prompt"] == "RUBRIC HERE\n\nJD text"  # prefix concatenated, like other non-Anthropic providers
    assert captured["extra_body"] == {"reasoning_effort": "none"}


@pytest.mark.asyncio
async def test_dispatch_openai_compat_removed():
    """openai_compat provider was removed 2026-07 — _dispatch rejects it."""
    from backend.analyzer.llm_client import _dispatch
    with pytest.raises(ValueError, match="Unknown LLM provider"):
        await _dispatch(
            provider="openai_compat", model="anything/model",
            api_key="", prompt="x", system="s", max_tokens=10,
        )
