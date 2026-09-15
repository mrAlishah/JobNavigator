"""Scoring LLM override (scoring_llm_*) + call_llm primary/fallback behavior."""
import asyncio
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


class _DummyDB:
    def close(self):
        pass


# ── call_llm: primary override + automatic fallback ─────────────────────────

@pytest.mark.asyncio
async def test_call_llm_uses_primary_override(monkeypatch):
    """provider/model/api_key args override the Primary for this call."""
    import backend.analyzer.llm_client as L
    monkeypatch.setattr(L, "SessionLocal", lambda: _DummyDB())
    monkeypatch.setattr(L, "_get_setting", lambda db, k, d="": d)  # no fallback configured
    calls = []

    async def fake_dispatch(provider, model, api_key, prompt, system, max_tokens, cached_prefix=None):
        calls.append((provider, model, api_key))
        return {"text": "ok", "usage": {}}

    monkeypatch.setattr(L, "_dispatch", fake_dispatch)

    r = await L.call_llm("p", "s", 100, provider="openrouter",
                         model="anthropic/claude-sonnet-5", api_key="k1")
    assert r["text"] == "ok"
    assert calls == [("openrouter", "anthropic/claude-sonnet-5", "k1")]


@pytest.mark.asyncio
async def test_call_llm_reads_primary_from_settings_when_no_override(monkeypatch):
    """Without overrides, the Primary comes from llm_* settings (unchanged behavior)."""
    import backend.analyzer.llm_client as L
    settings = {"llm_provider": "claude_api", "llm_model": "claude-sonnet-5", "llm_api_key": "envkey"}
    monkeypatch.setattr(L, "SessionLocal", lambda: _DummyDB())
    monkeypatch.setattr(L, "_get_setting", lambda db, k, d="": settings.get(k, d))
    calls = []

    async def fake_dispatch(provider, model, api_key, *a, **k):
        calls.append((provider, model, api_key))
        return {"text": "ok", "usage": {}}

    monkeypatch.setattr(L, "_dispatch", fake_dispatch)

    await L.call_llm("p", "s", 100)
    assert calls == [("claude_api", "claude-sonnet-5", "envkey")]


@pytest.mark.asyncio
async def test_call_llm_falls_back_on_primary_failure(monkeypatch):
    """Primary exhausts its retries, then the Fallback (llm_fallback_*) is used."""
    import backend.analyzer.llm_client as L
    settings = {"llm_fallback_provider": "openai", "llm_fallback_model": "gpt-x",
                "llm_fallback_api_key": "fk"}
    monkeypatch.setattr(L, "SessionLocal", lambda: _DummyDB())
    monkeypatch.setattr(L, "_get_setting", lambda db, k, d="": settings.get(k, d))

    async def _no_sleep(*a, **k):
        pass
    monkeypatch.setattr(L.asyncio, "sleep", _no_sleep)
    calls = []

    async def fake_dispatch(provider, model, api_key, *a, **k):
        calls.append(provider)
        if provider == "openai":
            return {"text": "fallback-result", "usage": {}}
        raise RuntimeError("primary down")

    monkeypatch.setattr(L, "_dispatch", fake_dispatch)

    r = await L.call_llm("p", "s", 100, provider="claude_api", model="m", api_key="k")
    assert r["text"] == "fallback-result"
    assert calls.count("claude_api") == 4   # primary retried MAX_ATTEMPTS times
    assert calls[-1] == "openai"            # then fell back


@pytest.mark.asyncio
async def test_call_llm_raises_when_no_fallback(monkeypatch):
    """No fallback configured → the primary error propagates."""
    import backend.analyzer.llm_client as L
    monkeypatch.setattr(L, "SessionLocal", lambda: _DummyDB())
    monkeypatch.setattr(L, "_get_setting", lambda db, k, d="": d)  # fallback empty

    async def _no_sleep(*a, **k):
        pass
    monkeypatch.setattr(L.asyncio, "sleep", _no_sleep)

    async def boom(*a, **k):
        raise RuntimeError("primary down")
    monkeypatch.setattr(L, "_dispatch", boom)

    with pytest.raises(RuntimeError, match="primary down"):
        await L.call_llm("p", "s", 100, provider="claude_api", model="m", api_key="k")


# ── cv_scorer: scoring_llm_* override resolution ────────────────────────────

class FakeJob:
    def __init__(self):
        self.id = "job-1"
        self.description = "Senior PM role. " * 40
        self.cached_page_text = None
        self.url = None


def _scorer_session(extra_rows):
    from backend.models.db import Setting
    engine = create_engine("sqlite:///:memory:")
    Setting.__table__.create(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    s.add(Setting(key="scoring_rubric", value="RUBRIC"))
    s.add(Setting(key="scoring_output_light",
                  value='{"scores": {CV_NAMES_HERE}, "best_cv": "CV_NAME"}'))
    s.add(Setting(key="scoring_output_full", value="FULL"))
    s.add(Setting(key="llm_provider", value="claude_api"))
    s.add(Setting(key="llm_model", value="claude-sonnet-5"))
    s.add(Setting(key="llm_api_key", value="primkey"))
    for k, v in extra_rows.items():
        s.add(Setting(key=k, value=v))
    s.commit()
    s.close()
    return Session


async def _run_scorer_capture(monkeypatch, Session):
    from backend.analyzer import cv_scorer
    monkeypatch.setattr(cv_scorer, "SessionLocal", Session)
    monkeypatch.setattr(cv_scorer, "_get_scoring_semaphore", lambda: asyncio.Semaphore(1))
    monkeypatch.setattr("backend.analyzer.cv_scorer.log_llm_call", lambda **kw: None)
    captured = {}

    async def fake_call_llm(prompt, system, max_tokens, cached_prefix=None, **kwargs):
        captured.update(kwargs)
        return {"text": '{"scores":{"PM":80},"best_cv":"PM"}',
                "usage": {"input_tokens": 1, "output_tokens": 1,
                          "cache_read_tokens": 0, "cache_write_tokens": 0}}

    monkeypatch.setattr("backend.analyzer.cv_scorer.call_llm", fake_call_llm)
    await cv_scorer.score_job_sync(FakeJob(), {"PM": "PM resume text"},
                                   db=None, depth="light", preloaded_text="Senior PM role")
    return captured


@pytest.mark.asyncio
async def test_scorer_uses_primary_when_override_empty(monkeypatch):
    Session = _scorer_session({"scoring_llm_provider": "", "scoring_llm_model": "",
                               "scoring_llm_api_key": ""})
    cap = await _run_scorer_capture(monkeypatch, Session)
    assert cap["provider"] == "claude_api"
    assert cap["model"] == "claude-sonnet-5"
    assert cap["api_key"] == "primkey"


@pytest.mark.asyncio
async def test_scorer_uses_scoring_override_when_set(monkeypatch):
    Session = _scorer_session({"scoring_llm_provider": "openrouter",
                               "scoring_llm_model": "x-ai/grok-4.6",
                               "scoring_llm_api_key": "orkey"})
    cap = await _run_scorer_capture(monkeypatch, Session)
    assert cap["provider"] == "openrouter"
    assert cap["model"] == "x-ai/grok-4.6"
    assert cap["api_key"] == "orkey"


@pytest.mark.asyncio
async def test_scorer_override_provider_falls_back_to_primary_key(monkeypatch):
    """Override provider set but its key blank → falls back to the Primary api key."""
    Session = _scorer_session({"scoring_llm_provider": "openrouter",
                               "scoring_llm_model": "x-ai/grok-4.6",
                               "scoring_llm_api_key": ""})
    cap = await _run_scorer_capture(monkeypatch, Session)
    assert cap["provider"] == "openrouter"
    assert cap["api_key"] == "primkey"
