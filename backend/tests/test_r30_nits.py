"""R30 review nits fixed in 3.1: D-09 (résumé JSON type and size), D-10 (download filenames),
L-05 (an override on another provider never borrows the Primary model), L-07 (OpenCode Go usage limit not retried), L-08 (Ollama errors without the server address),
L-09 (streamed autofill logged), S-17 (Apify 402 names Apify's reason)."""
import httpx
import pytest

from backend.analyzer import llm_client
from backend.analyzer.llm_client import NonRetryableLLMError
from backend.models.db import LlmCallLog, Setting


@pytest.fixture
def api_client(api_client, test_db):
    """Open access: an empty dashboard_api_key row (see test_apify_source.py)."""
    test_db.add(Setting(key="dashboard_api_key", value=""))
    test_db.commit()
    return api_client


# ── D-09 ────────────────────────────────────────────────────────────────────

def test_resume_json_data_must_be_an_object_of_at_most_512_kb(api_client):
    r = api_client.post("/api/resumes", json={"name": "A", "json_data": ["not", "an", "object"]})
    assert r.status_code == 400 and "must be an object" in r.json()["detail"]
    r = api_client.post("/api/resumes", json={"name": "A", "json_data": {"summary": "x" * (600 * 1024)}})
    assert r.status_code == 400 and "too large" in r.json()["detail"]
    rid = api_client.post("/api/resumes", json={"name": "A", "json_data": {"summary": "ok"}}).json()["id"]
    r = api_client.patch(f"/api/resumes/{rid}", json={"json_data": "a string"})
    assert r.status_code == 400
    r = api_client.patch(f"/api/resumes/{rid}", json={"json_data": {"summary": "x" * (600 * 1024)}})
    assert r.status_code == 400
    assert api_client.get(f"/api/resumes/{rid}").json()["json_data"] == {"summary": "ok"}   # row untouched
    assert api_client.patch(f"/api/resumes/{rid}", json={"json_data": None}).status_code == 200


# ── D-10 ────────────────────────────────────────────────────────────────────

def test_download_names_have_no_path_parts_and_a_bounded_length():
    from backend.api.routes_resumes import FILENAME_STEM_MAX, _content_disposition, _safe_filename_stem
    assert _safe_filename_stem("../../etc/passwd") == "etc_passwd"
    assert _safe_filename_stem("Ana\\Smith:CV") == "Ana_Smith_CV"
    assert _safe_filename_stem("...") == "Resume"
    assert len(_safe_filename_stem("Jose_" + "x" * 500)) == FILENAME_STEM_MAX
    assert _safe_filename_stem("José_Müller_Resume") == "José_Müller_Resume"   # non-ASCII kept
    header = _content_disposition("../a/b", "pdf")
    assert "/" not in header.split("filename*=")[0] and "%2F" not in header and ".." not in header


# ── L-07 ────────────────────────────────────────────────────────────────────

class _StatusError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status_code = status


@pytest.mark.asyncio
@pytest.mark.parametrize("status,message", [
    (429, "Too many requests"),
    (403, "You have reached your usage limit for this period"),
])
async def test_opencode_go_usage_limit_is_not_retried(monkeypatch, status, message):
    async def boom(*_a, **_k):
        raise _StatusError(status, message)
    monkeypatch.setattr(llm_client, "_call_openai", boom)
    with pytest.raises(NonRetryableLLMError, match="usage limit reached"):
        await llm_client._call_opencode_go("p", "s", "glm-5.3", "sk", 10)


# ── L-08 ────────────────────────────────────────────────────────────────────

def _ollama_answering(monkeypatch, handler):
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(llm_client, "local_base_url", lambda provider: "http://10.0.0.5:11434")


@pytest.mark.asyncio
async def test_an_ollama_http_error_does_not_name_the_server(monkeypatch):
    _ollama_answering(monkeypatch, lambda req: httpx.Response(404, json={"error": "model 'x' not found"}))
    with pytest.raises(RuntimeError) as e:
        await llm_client._call_ollama("p", "s", "x", 10)
    assert "10.0.0.5" not in str(e.value) and "11434" not in str(e.value)
    assert "HTTP 404" in str(e.value) and "model 'x' not found" in str(e.value)


@pytest.mark.asyncio
async def test_an_unreachable_ollama_does_not_name_the_server(monkeypatch):
    def refuse(req):
        raise httpx.ConnectError("All connection attempts failed to 10.0.0.5:11434", request=req)
    _ollama_answering(monkeypatch, refuse)
    with pytest.raises(RuntimeError) as e:
        await llm_client._call_ollama("p", "s", "x", 10)
    assert "10.0.0.5" not in str(e.value) and "not reachable" in str(e.value)


# ── L-09 ────────────────────────────────────────────────────────────────────

def test_a_streamed_autofill_answer_is_logged(api_client, test_db, monkeypatch):
    import backend.api.routes_autofill as ra

    async def fake_stream(*_a, **_k):
        yield "I led "
        yield "the launch."
    monkeypatch.setattr(ra, "_build_autofill_prompt", lambda body, want_provider=False: {
        "cached_prefix": "", "suffix": "Q", "max_chars": 200, "provider": "claude_api", "model": "m-1"})
    monkeypatch.setattr(ra, "call_autofill_llm_stream", fake_stream)
    r = api_client.post("/api/autofill/answer/stream", json={"question": "Why us?"})
    assert r.status_code == 200 and "the launch." in r.text
    rows = test_db.query(LlmCallLog).filter(LlmCallLog.purpose == "autofill").all()
    assert len(rows) == 1 and rows[0].success is True and rows[0].model == "m-1"


# ── S-17 ────────────────────────────────────────────────────────────────────

def test_an_apify_402_names_apify_s_own_reason():
    from backend.scraper.sources import apify
    reason = "You must rent a paid Actor in order to run it."
    err = apify._api_error(httpx.Response(402, json={"error": {"message": reason, "type": "actor-is-not-rented"}}))
    assert reason in str(err) and "no credit" not in str(err)
    bare = apify._api_error(httpx.Response(402, json={}))
    assert "no credit left" in str(bare)


# ── L-05 ────────────────────────────────────────────────────────────────────

def test_an_override_on_another_provider_never_borrows_the_primary_model(test_db):
    from backend.tests.r4_support import set_setting
    set_setting(test_db, "llm_provider", "claude_api")
    set_setting(test_db, "llm_model", "claude-sonnet-5")
    set_setting(test_db, "autofill_llm_provider", "openai")
    assert llm_client.resolve_llm_config("autofill", db=test_db)["model"] == ""
    set_setting(test_db, "scoring_llm_provider", "claude_api")      # same provider: inherits
    assert llm_client.resolve_llm_config("scoring", db=test_db)["model"] == "claude-sonnet-5"


@pytest.mark.asyncio
async def test_an_api_provider_without_a_model_fails_once_and_names_the_fix(monkeypatch):
    with pytest.raises(NonRetryableLLMError, match="No model picked for OpenAI"):
        await llm_client._dispatch("openai", "", "sk", "p", "s", 10)
    seen = []

    async def fake_cc(prompt, system, model, max_tokens, effort=""):
        seen.append(model)
        return {"text": "ok", "usage": {}}
    monkeypatch.setattr(llm_client, "_call_claude_code", fake_cc)
    await llm_client._dispatch("claude_code", "", "", "p", "s", 10)   # a CLI keeps its own default
    assert seen == [""]
