"""Contract tests for the OpenCode Go provider — an OpenAI-compatible endpoint, no CLI/binary."""
import pytest

from backend.analyzer import llm_client
from backend.analyzer.llm_client import NonRetryableLLMError, OPENCODE_GO_BASE_URL


class _StatusError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status_code = status


@pytest.mark.asyncio
async def test_dispatch_routes_opencode_go_to_its_base_url(monkeypatch):
    """opencode_go is OpenAI-API-compatible: same client, Go base URL, key passed through."""
    captured = {}

    async def fake_openai(prompt, system, model, api_key, max_tokens, base_url=None, extra_body=None, effort=""):
        captured.update(prompt=prompt, model=model, api_key=api_key, base_url=base_url)
        return {"text": "ok", "usage": {"input_tokens": 1, "output_tokens": 1,
                                        "cache_read_tokens": 0, "cache_write_tokens": 0}}

    monkeypatch.setattr(llm_client, "_call_openai", fake_openai)
    await llm_client._dispatch(provider="opencode_go", model="deepseek-v4.1-flash", api_key="sk-go",
                               prompt="the question", system="s", max_tokens=10, cached_prefix="the resume")

    assert captured["base_url"] == OPENCODE_GO_BASE_URL
    assert captured["api_key"] == "sk-go"
    assert captured["model"] == "deepseek-v4.1-flash"
    # Non-Anthropic provider: the prefix is concatenated, not sent as a cache block.
    assert captured["prompt"] == "the resume\n\nthe question"


@pytest.mark.asyncio
async def test_missing_key_is_not_retryable(monkeypatch):
    """A Go key is required; a 401 must not retry into the fallback as a transient error."""
    async def boom(*_a, **_k):
        raise _StatusError(401, "Error code: 401 - {'error': {'message': 'Missing API key.'}}")

    monkeypatch.setattr(llm_client, "_call_openai", boom)
    with pytest.raises(NonRetryableLLMError, match="needs an API key"):
        await llm_client._call_opencode_go("p", "s", "deepseek-v4.1-flash", "", 10)


@pytest.mark.asyncio
async def test_autofill_streaming_uses_the_go_base_url(monkeypatch):
    """opencode_go streams natively (like openai/openrouter), so autofill animation works."""
    captured = {}

    async def fake_stream(prompt, system, model, api_key, max_tokens, base_url=None, effort="", feature=""):
        captured.update(base_url=base_url, model=model, max_tokens=max_tokens, feature=feature)
        yield "ok"

    monkeypatch.setattr(llm_client, "_stream_openai", fake_stream)
    monkeypatch.setattr(llm_client, "resolve_llm_config", lambda feature="", db=None: {
        "provider": "opencode_go", "model": "glm-5.3", "api_key": "sk-go", "effort": ""})

    chunks = [c async for c in llm_client.call_autofill_llm_stream("q", "s", 100)]
    assert chunks == ["ok"]
    assert captured["base_url"] == OPENCODE_GO_BASE_URL
    assert captured["feature"] == "autofill"   # its own x-opencode-session


@pytest.mark.asyncio
async def test_other_errors_still_retry(monkeypatch):
    async def boom(*_a, **_k):
        raise _StatusError(500, "server error")

    monkeypatch.setattr(llm_client, "_call_openai", boom)
    with pytest.raises(_StatusError):
        await llm_client._call_opencode_go("p", "s", "x", "sk", 10)


def test_output_cap_always_gives_opencode_go_headroom():
    """Go reasons by default and has no effort to lower, so the cap always gets the room; a
    scoring call at 600 otherwise spends the whole cap on reasoning and returns no text."""
    from backend.analyzer.llm_client import _output_cap, REASONING_HEADROOM
    assert _output_cap("opencode_go", 600, "") == 600 + REASONING_HEADROOM
    assert _output_cap("opencode_go", 600, "none") == 600
    assert _output_cap("openai", 600, "") == 600
    assert _output_cap("openai", 600, "high") == 600 + REASONING_HEADROOM


@pytest.mark.asyncio
async def test_dispatch_sends_the_padded_cap_to_go(monkeypatch):
    """Go has no effort values, so a left-over effort is dropped; the cap still gets the
    reasoning headroom so a reasoning-default model answers instead of returning no text."""
    captured = {}

    async def fake_openai(prompt, system, model, api_key, max_tokens, base_url=None, extra_body=None, effort=""):
        captured.update(max_tokens=max_tokens, effort=effort)
        return {"text": "ok", "usage": {}}

    monkeypatch.setattr(llm_client, "_call_openai", fake_openai)
    await llm_client._dispatch(provider="opencode_go", model="glm-5.3", api_key="sk-go",
                               prompt="p", system="s", max_tokens=600, effort="high")
    assert captured == {"max_tokens": 600 + llm_client.REASONING_HEADROOM, "effort": ""}


def test_client_sends_identity_and_session_headers(monkeypatch):
    """Go asks clients to identify themselves and send a stable x-opencode-session; the SDK's
    generic user agent and a missing session header are what the docs warn against."""
    import openai
    captured = {}

    class Fake:
        def __init__(self, **kw):
            captured.update(kw)

    monkeypatch.setattr(openai, "AsyncOpenAI", Fake)
    from backend.analyzer.llm_client import _openai_client, OPENCODE_USER_AGENT, _opencode_session

    _openai_client("sk-go", OPENCODE_GO_BASE_URL)
    assert captured["default_headers"]["User-Agent"] == OPENCODE_USER_AGENT
    assert captured["default_headers"]["x-opencode-session"] == _opencode_session("")

    captured.clear()
    _openai_client("sk", None)          # plain OpenAI keeps the SDK defaults
    assert captured["default_headers"] is None


@pytest.mark.asyncio
async def test_session_id_is_one_per_account_and_feature(monkeypatch):
    """x-opencode-session: stable for one (account, feature) so its prompts cache together, a
    different id per feature, and never the same id for two accounts."""
    import uuid
    import openai
    from backend.accounts.context import use_account
    sessions = []

    class Fake:
        def __init__(self, **kw):
            self.kw = kw

    async def fake_call_openai(prompt, system, model, api_key, max_tokens, base_url=None,
                               extra_body=None, effort=""):
        sessions.append(llm_client._openai_client(api_key, base_url).kw["default_headers"]["x-opencode-session"])
        return {"text": "ok", "usage": {}}

    monkeypatch.setattr(openai, "AsyncOpenAI", Fake)
    monkeypatch.setattr(llm_client, "_call_openai", fake_call_openai)
    monkeypatch.setattr(llm_client, "resolve_llm_config", lambda feature="", db=None: {
        "provider": "opencode_go", "model": "glm-5.3", "api_key": "sk-go", "effort": ""})
    monkeypatch.setattr(llm_client, "_require_own_key", lambda *a: None)

    a, b = uuid.uuid4(), uuid.uuid4()
    with use_account(a):
        await llm_client.call_cv_tailor_llm("p", "s")
        await llm_client.call_cv_tailor_llm("p", "s")
        await llm_client.call_email_llm("p", "s")
        with llm_client.llm_feature("scoring"):
            await llm_client._dispatch("opencode_go", "glm-5.3", "sk-go", "p", "s", 10)
    with use_account(b):
        await llm_client.call_cv_tailor_llm("p", "s")
    a_tailor, a_tailor_again, a_email, a_scoring, b_tailor = sessions

    assert a_tailor == a_tailor_again                       # stable per (account, feature)
    assert len({a_tailor, a_email, a_scoring}) == 3         # one per feature
    assert b_tailor != a_tailor                             # never shared across accounts
    assert llm_client._llm_feature.get() == ""              # the wrappers leave no feature behind
    with use_account(a):
        assert llm_client._opencode_session("cv_tailor") == a_tailor
        assert llm_client._opencode_session("autofill") not in sessions


# The exact payloads Go returned to @mreid-tt's probe (#25).
_GO_PROTOCOL = {"type": "error", "error": {"type": "ModelProtocolUnsupported",
                                           "message": "Model does not support this protocol."}}
_GO_UNKNOWN = {"type": "error", "error": {"type": "server_error",
                                          "message": "Upstream request failed: Model is unavailable."}}
_GO_CONTRIBUTOR = {"type": "error", "error": {
    "type": "server_error",
    "message": "This Go model trains on request data. Allow paid endpoints that train on request data to use it."}}


def _go_answers(monkeypatch, status, payload):
    """Route the real OpenAI SDK at a mock transport that answers every request with `payload`,
    so the SDK's own error parsing (e.body, e.type) is what the provider reads."""
    import httpx
    from openai import AsyncOpenAI
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, json=payload)

    def client(api_key, base_url, feature=None):
        return AsyncOpenAI(api_key=api_key, base_url=base_url, max_retries=0,
                           http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    monkeypatch.setattr(llm_client, "_openai_client", client)
    return calls


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["gpt-6-luna", "grok-4.7", "claude-haiku-5-5"])
async def test_a_model_on_another_protocol_is_named_and_not_retried(monkeypatch, model):
    _go_answers(monkeypatch, 400, _GO_PROTOCOL)
    with pytest.raises(NonRetryableLLMError,
                       match=f"OpenCode Go serves {model} on another protocol, not chat completions"):
        await llm_client._call_opencode_go("p", "s", model, "sk", 10)


@pytest.mark.asyncio
async def test_the_protocol_signal_is_the_type_not_the_message(monkeypatch):
    """Go may reword the message; ModelProtocolUnsupported alone decides."""
    _go_answers(monkeypatch, 400, {"type": "error", "error": {"type": "ModelProtocolUnsupported",
                                                              "message": "something new"}})
    with pytest.raises(NonRetryableLLMError, match="another protocol"):
        await llm_client._call_opencode_go("p", "s", "grok-4.7", "sk", 10)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 500, 502])
async def test_an_unknown_model_is_named_and_not_retried(monkeypatch, status):
    _go_answers(monkeypatch, status, _GO_UNKNOWN)
    with pytest.raises(NonRetryableLLMError, match="does not know the model definitely-not-a-real-model"):
        await llm_client._call_opencode_go("p", "s", "definitely-not-a-real-model", "sk", 10)


@pytest.mark.asyncio
async def test_a_contributor_model_gets_a_readable_hint(monkeypatch):
    _go_answers(monkeypatch, 400, _GO_CONTRIBUTOR)
    with pytest.raises(NonRetryableLLMError, match="contributor model that trains on request data") as e:
        await llm_client._call_opencode_go("p", "s", "gemini-4-argon-contributor", "sk", 10)
    assert "gemini-4-argon-contributor" in str(e.value) and "allow" in str(e.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("status,payload", [
    (500, {"type": "error", "error": {"type": "server_error", "message": "Upstream request failed: timeout"}}),
    (503, {"type": "error", "error": {"type": "overloaded", "message": "Model is unavailable."}}),
    (400, {"type": "error", "error": {"type": "invalid_request_error",
                                      "message": "This model's maximum context length is 131072 tokens."}}),
    (404, {"error": {"message": "The model grok-4.7 does not exist"}}),
])
async def test_every_other_go_error_takes_the_retry_path(monkeypatch, status, payload):
    """Only the three cases above skip the retry: a server_error with another message, an
    "unavailable" message under another type, and the old keyword-shaped 400/404s all re-raise
    as they are, so call_llm retries and the fallback model can take over."""
    import openai
    _go_answers(monkeypatch, status, payload)
    with pytest.raises(openai.APIStatusError) as e:
        await llm_client._call_opencode_go("p", "s", "glm-5.3", "sk", 10)
    assert not isinstance(e.value, NonRetryableLLMError)


@pytest.mark.asyncio
async def test_call_llm_skips_the_retries_for_a_protocol_error(monkeypatch):
    """End to end: the primary is tried once, then straight to the fallback."""
    calls = _go_answers(monkeypatch, 400, _GO_PROTOCOL)
    settings = {"llm_provider": "opencode_go", "llm_model": "grok-4.7", "llm_api_key": "sk",
                "llm_fallback_provider": "", "llm_fallback_model": ""}
    monkeypatch.setattr(llm_client, "_get_setting", lambda db, k, d="": settings.get(k, d))
    monkeypatch.setattr(llm_client, "SessionLocal", lambda: type("S", (), {"close": lambda self: None})())
    monkeypatch.setattr(llm_client, "_require_own_key", lambda *a: None)
    with pytest.raises(NonRetryableLLMError, match="another protocol"):
        await llm_client.call_llm("p", "s", 10)
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("message", [
    "This model's maximum context length is 131072 tokens. However, you requested 140000 tokens.",
    "max_tokens is too large: 30000. This model supports at most 16384 completion tokens.",
    "Unsupported parameter: 'max_tokens' is not supported with this model.",
    "Invalid value for 'temperature' with this model",
])
async def test_other_400s_that_mention_the_model_take_the_normal_path(monkeypatch, message):
    """A context-length or max_tokens 400 is not "model not served": it re-raises as is, so
    call_llm retries and the fallback model can take over, as for every other provider."""
    async def boom(*_a, **_k):
        raise _StatusError(400, f"Error code: 400 - {message}")

    monkeypatch.setattr(llm_client, "_call_openai", boom)
    with pytest.raises(_StatusError):
        await llm_client._call_opencode_go("p", "s", "glm-5.3", "sk", 10)


@pytest.mark.asyncio
async def test_catalog_reads_models_from_the_go_base_url(monkeypatch):
    import backend.api.routes_llm as rl
    seen = {}

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"id": "deepseek-v4.1-flash"}, {"id": "glm-5.3"}]}

    class _Client:
        def __init__(self, *_a, **_k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def get(self, url, headers=None):
            seen["url"] = url
            seen["headers"] = headers
            return _Resp()

    monkeypatch.setattr(rl.httpx, "AsyncClient", _Client)
    models = await rl._fetch_opencode_go("sk-go")

    assert seen["url"] == OPENCODE_GO_BASE_URL + "/models"
    assert seen["headers"] == {"Authorization": "Bearer sk-go"}
    assert [m["id"] for m in models] == ["deepseek-v4.1-flash", "glm-5.3"]
