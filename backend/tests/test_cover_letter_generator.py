"""Unit tests for analyzer/cover_letter_generator.py."""
import json
import pytest
from unittest.mock import AsyncMock

from backend.models.db import Setting


SAMPLE_RESUME = {
    "header": {"name": "Viktor Esadze", "contact_items": [{"text": "viktor@example.com", "url": "mailto:viktor@example.com"}]},
    "summary": "Product manager with fintech experience.",
    "experience": [{"company": "Acme", "title": "PM", "bullets": ["Shipped X", "Led Y"]}],
    "skills": {"Core": "Roadmapping, Discovery"},
}


# ── parse_cover_letter_response ──────────────────────────────────────────────

def test_parse_extracts_json_from_noise():
    from backend.analyzer.cover_letter_generator import parse_cover_letter_response
    raw = 'Here you go:\n{"greeting": "Dear Team,", "body_paragraphs": ["One.", "Two."], "closing": "Sincerely,", "signature": "Viktor"}\nHope that helps.'
    out = parse_cover_letter_response(raw)
    assert out["greeting"] == "Dear Team,"
    assert out["body_paragraphs"] == ["One.", "Two."]
    assert out["closing"] == "Sincerely,"
    assert out["signature"] == "Viktor"


def test_parse_applies_defaults_for_missing_fields():
    from backend.analyzer.cover_letter_generator import parse_cover_letter_response
    out = parse_cover_letter_response('{"body_paragraphs": ["Only body."]}')
    assert out["greeting"] == "Dear Hiring Team,"
    assert out["closing"] == "Sincerely,"
    assert out["signature"] == ""


def test_parse_drops_empty_paragraphs():
    from backend.analyzer.cover_letter_generator import parse_cover_letter_response
    out = parse_cover_letter_response('{"body_paragraphs": ["Real.", "", "   ", "Also real."]}')
    assert out["body_paragraphs"] == ["Real.", "Also real."]


def test_parse_raises_on_garbage():
    from backend.analyzer.cover_letter_generator import parse_cover_letter_response
    with pytest.raises(json.JSONDecodeError):
        parse_cover_letter_response("no json here at all")


# ── build_cover_letter_prompt ────────────────────────────────────────────────

def test_build_prompt_splits_prefix_and_suffix():
    from backend.analyzer.cover_letter_generator import build_cover_letter_prompt
    template = "Voice: {voice_instruction}\nLength: {length_instruction}\nJD:\n{job_description}"
    prefix, suffix = build_cover_letter_prompt(
        SAMPLE_RESUME, {"remote": True}, "We need a fintech PM.",
        "Be concise.", "standard", template,
    )
    # Prefix carries the resume + preferences (cacheable, stable per resume)
    assert "Viktor Esadze" in prefix
    assert "CANDIDATE RESUME" in prefix
    assert "remote" in prefix
    # Suffix carries the volatile bits (JD, voice, length)
    assert "Be concise." in suffix
    assert "We need a fintech PM." in suffix
    assert "{job_description}" not in suffix


def test_build_prompt_truncates_long_jd():
    from backend.analyzer.cover_letter_generator import build_cover_letter_prompt
    long_jd = "x" * 9000
    _, suffix = build_cover_letter_prompt(SAMPLE_RESUME, {}, long_jd, "v", "concise", "{job_description}")
    body = suffix.split("<<<JOB POSTING>>>\n", 1)[1].rsplit("\n<<<END JOB POSTING>>>", 1)[0]
    assert body == "x" * 6000  # capped at 6000, inside the fence


# ── resolve_voice_instruction ────────────────────────────────────────────────

def _seed_presets(test_db):
    presets = [
        {"id": "professional", "label": "Pro", "instruction": "Be direct."},
        {"id": "warm", "label": "Warm", "instruction": "Be warm."},
    ]
    test_db.add(Setting(key="cover_letter_voice_presets", value=json.dumps(presets)))
    test_db.add(Setting(key="cover_letter_default_voice", value="professional"))
    test_db.commit()


def test_resolve_voice_by_id(test_db):
    from backend.analyzer.cover_letter_generator import resolve_voice_instruction
    _seed_presets(test_db)
    vid, instr = resolve_voice_instruction(test_db, "warm")
    assert vid == "warm"
    assert instr == "Be warm."


def test_resolve_voice_falls_back_to_default(test_db):
    from backend.analyzer.cover_letter_generator import resolve_voice_instruction
    _seed_presets(test_db)
    vid, instr = resolve_voice_instruction(test_db, None)
    assert vid == "professional"
    assert instr == "Be direct."


def test_resolve_voice_unknown_id_falls_to_first(test_db):
    from backend.analyzer.cover_letter_generator import resolve_voice_instruction
    _seed_presets(test_db)
    vid, instr = resolve_voice_instruction(test_db, "does-not-exist")
    assert vid == "professional"  # first preset


def test_resolve_voice_no_presets_returns_neutral(test_db):
    from backend.analyzer.cover_letter_generator import resolve_voice_instruction
    vid, instr = resolve_voice_instruction(test_db, None)
    assert vid == ""
    assert instr  # non-empty neutral fallback


# ── generate_cover_letter_body (mocked LLM) ──────────────────────────────────

@pytest.mark.asyncio
async def test_generate_body_calls_llm_with_cached_prefix(monkeypatch):
    from backend.analyzer import cover_letter_generator as gen

    captured = {}

    async def _fake_llm(prompt, system, max_tokens=1500, cached_prefix=None):
        captured["prompt"] = prompt
        captured["cached_prefix"] = cached_prefix
        return {"text": '{"greeting":"Dear Team,","body_paragraphs":["P1","P2","P3"],"closing":"Sincerely,","signature":"Viktor"}',
                "usage": {"input_tokens": 100}}

    monkeypatch.setattr(gen, "call_cover_letter_llm", _fake_llm)

    out = await gen.generate_cover_letter_body(
        SAMPLE_RESUME, {"remote": True}, "JD text here.",
        "Be bold.", "standard",
        "Voice: {voice_instruction}\nLength: {length_instruction}\nJD: {job_description}",
    )
    # The resume goes into the cached prefix (prompt-caching win)
    assert "Viktor Esadze" in captured["cached_prefix"]
    assert "JD text here." in captured["prompt"]
    assert out["body_paragraphs"] == ["P1", "P2", "P3"]
    assert out["_usage"]["input_tokens"] == 100
