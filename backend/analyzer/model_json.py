"""Reading a JSON object out of a raw model reply.

A model asked for JSON does not always send only JSON: it wraps the object in a
```json fence, puts a sentence in front of it, or — when something in the prompt
reads to it like an instruction it should not follow — answers in prose and
sends no JSON at all. One tolerant reader serves every caller (tailoring,
scoring, cover letters, PDF import, autofill) so none of them has to keep its
own brace regex, and one plain message stands in for the decoder's
"Expecting value: line 1 column 1 (char 0)" when the reply carries no JSON.
"""
import json

# What the user reads when a reply cannot be parsed; the decoder's own message
# names a column in a string they never saw.
UNPARSEABLE_MESSAGE = "The model's reply was not valid JSON — try again"


class ModelReplyError(RuntimeError):
    """A reply that carried no usable JSON, worded for the person who triggered the run."""


def first_json_object(text: str):
    r"""Return the first balanced {...} in text (string/escape aware), or None; a naive brace regex would span to the last "}" in the reply and break on trailing prose."""
    depth = 0
    start = -1
    in_str = False
    esc = False
    for i, ch in enumerate(text or ""):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth:
                depth -= 1
                if depth == 0 and start >= 0:
                    return text[start:i + 1]
    return None


def parse_model_json(raw_response: str) -> dict:
    """The first balanced {...} in a raw model reply (string/escape aware), falling back to the whole reply; raises json.JSONDecodeError when neither parses."""
    last_err = None
    for candidate in (first_json_object(raw_response), (raw_response or "").strip()):
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as e:
            last_err = e
    raise last_err or json.JSONDecodeError("no JSON object found", raw_response or "", 0)


def require_model_json(raw_response: str) -> dict:
    """parse_model_json, but a reply with no JSON in it raises ModelReplyError — a sentence a user can act on instead of a decoder's character offset."""
    try:
        return parse_model_json(raw_response)
    except json.JSONDecodeError:
        raise ModelReplyError(UNPARSEABLE_MESSAGE)
