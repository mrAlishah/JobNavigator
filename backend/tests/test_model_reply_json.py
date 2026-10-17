"""The shared model-reply reader: one tolerant parser behind every JSON-answer site, and a sentence instead of a decoder offset when a reply carries no JSON."""
import json as _json

import pytest

from backend.analyzer.model_json import (
    UNPARSEABLE_MESSAGE, ModelReplyError, first_json_object, parse_model_json,
    require_model_json,
)


GOOD = {"summary": "s", "experience": [], "skills": {}}


# ── the reader ───────────────────────────────────────────────────────────────

def test_bare_object():
    assert parse_model_json(_json.dumps(GOOD)) == GOOD


def test_fenced_object():
    assert parse_model_json("```json\n" + _json.dumps(GOOD) + "\n```") == GOOD


def test_prose_around_the_object():
    raw = "Sure, here it is:\n" + _json.dumps(GOOD) + "\nHope that helps."
    assert parse_model_json(raw) == GOOD


def test_first_object_wins_over_a_trailing_one():
    """A greedy brace regex spanned both objects and parsed neither."""
    raw = '{"summary": "first"} and then {"summary": "second"}'
    assert parse_model_json(raw) == {"summary": "first"}


def test_braces_inside_strings_survive():
    data = {"summary": "fixed {a} and }b{ bugs"}
    assert parse_model_json(_json.dumps(data)) == data


def test_first_json_object_returns_none_without_one():
    assert first_json_object("no object here") is None


@pytest.mark.parametrize("raw", ["", "I can't help with that.", '{"summary": "trunc'])
def test_unusable_reply_raises_decode_error(raw):
    with pytest.raises(_json.JSONDecodeError):
        parse_model_json(raw)


# ── the worded failure ───────────────────────────────────────────────────────

def test_require_model_json_passes_a_good_reply_through():
    assert require_model_json(_json.dumps(GOOD)) == GOOD


def test_require_model_json_words_the_failure():
    with pytest.raises(ModelReplyError) as exc:
        require_model_json("I need to flag something about that first bullet.")
    assert str(exc.value) == UNPARSEABLE_MESSAGE
    assert "Expecting value" not in str(exc.value)


# ── the old homes still answer ───────────────────────────────────────────────

def test_routes_resumes_reexports_the_parser():
    from backend.api.routes_resumes import _parse_model_json
    assert _parse_model_json is parse_model_json


def test_routes_autofill_reexports_the_scanner():
    from backend.api.routes_autofill import _first_json_object
    assert _first_json_object is first_json_object
