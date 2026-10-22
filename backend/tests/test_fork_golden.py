"""The fork's résumé output and API surface against their golden files.

Like `test_location_corpus.py`, the golden files are a photograph, not a specification: they turn any
change to the template HTML, the Word export or the routes into a reviewable diff instead of a silent
shift. After an intended change (an upstream merge, a refactor), regenerate them deliberately:

    python -m backend.tests.fixtures.generate_fork_golden
"""
import json

import pytest

from backend.tests.fixtures import generate_fork_golden as gen

REGENERATE = "python -m backend.tests.fixtures.generate_fork_golden"


def _first_diff(a: list, b: list) -> int:
    return next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))


def _why(key, want, got) -> str:
    if want is None or got is None:
        return "%s: %s" % (key, "new case" if want is None else "case gone")
    if not isinstance(want, dict):
        return "%s: %.60r -> %.60r" % (key, want, got)
    fields = sorted(f for f in set(want) | set(got) if want.get(f) != got.get(f))
    note = ""
    if "len" in fields:
        note = " (length %s -> %s)" % (want["len"], got["len"])
    elif "paragraphs" in fields:
        i = _first_diff(want["paragraphs"], got["paragraphs"])
        note = " (first differing paragraph #%d: %.50r)" % (i, (got["paragraphs"][i:i + 1] or [{}])[0].get("text"))
    return "%s: %s%s" % (key, fields, note)


@pytest.mark.parametrize("name", sorted(gen.SNAPSHOTS))
def test_the_fork_output_still_matches_the_golden_file(name):
    want = json.loads(gen.FILES[name].read_text(encoding="utf-8"))
    got = gen.SNAPSHOTS[name]()
    wrong = sorted(k for k in set(want) | set(got) if want.get(k) != got.get(k))
    assert not wrong, (
        "%d of %d %s entries differ from %s. If the change is intended, regenerate with `%s` and "
        "review the diff. First three: %s" % (
            len(wrong), len(got), name, gen.FILES[name].name, REGENERATE,
            "; ".join(_why(k, want.get(k), got.get(k)) for k in wrong[:3])))
