"""_parse_model_json: pulls structured JSON out of raw LLM replies (bare, fenced, prose-wrapped) and raises on truncated output."""
import json as _json

import pytest

from backend.api.routes_resumes import _parse_model_json


GOOD = {"header": {"name": "Ada Lovelace"}, "experience": [], "skills": {}}


def test_bare_json():
    assert _parse_model_json(_json.dumps(GOOD)) == GOOD


def test_fenced_json():
    raw = "```json\n" + _json.dumps(GOOD) + "\n```"
    assert _parse_model_json(raw) == GOOD


def test_prose_before_and_after_the_object():
    raw = 'Here is the parsed resume:\n' + _json.dumps(GOOD, indent=2) + '\nHope this helps!'
    assert _parse_model_json(raw) == GOOD


def test_braces_inside_strings_survive():
    data = {"summary": "fixed {a} and }b{ bugs", "experience": []}
    assert _parse_model_json(_json.dumps(data)) == data


def test_truncated_json_raises_decode_error():
    with pytest.raises(_json.JSONDecodeError):
        _parse_model_json('{"header": {"name": "Ada", "contact_items": [{"text": "bo')


def test_empty_reply_raises_decode_error():
    with pytest.raises(_json.JSONDecodeError):
        _parse_model_json("")
