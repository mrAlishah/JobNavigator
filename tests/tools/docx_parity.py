"""Compare the fork's generic Word export with upstream's `_build_docx` on the same résumé JSON.

    git fetch upstream
    python3 tests/tools/docx_parity.py [upstream-ref]        # default: upstream/main

The fork replaced upstream's builder with a styled one, so an upstream fix to `_build_docx` merges as a
conflict that is easy to resolve in the fork's favour and lose. Run this after fetching, before and after a
merge: it builds each case with both builders (the fork's with no template style) and reports

  * input upstream's builder survives but the fork's does not (a lost guard),
  * different text, hyperlinks, section headings or document author for the same input.

Exits 1 on any report. An empty paragraph is ignored (upstream prints one for a missing header, the fork two).
Needs the backend dependencies of CI (python-docx) and `DATABASE_URL=sqlite:///:memory:`.
"""
import copy
import importlib.util
import io
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import docx  # noqa: E402

from backend.api import routes_resumes as fork  # noqa: E402
from backend.tests.test_resume_docx_malformed import GOOD, MALFORMED  # noqa: E402

CASES = {
    "valid": GOOD,
    "no header": {**GOOD, "header": None},
    "header is a string": {**GOOD, "header": "Dana"},
    "contact list is not a list": {**GOOD, "header": {"name": "D", "contact_items": "x"}},
    "contact values are numbers": {**GOOD, "header": {"name": 5, "title": 7, "contact_items": [{"text": 12, "url": ""}]}},
    "control characters": {**GOOD, "summary": "bad\x00\x0bchar\x1f"},
    "skills values are lists": {**GOOD, "skills": {"Tools": ["Jira", "SQL"], "N": None}},
    "dates key": {**GOOD, "experience": [{"title": "PM", "dates": "2020"}]},
    "empty": {},
    **{name: data for name, (data, _) in MALFORMED.items()},
}


def load_upstream(ref: str):
    source = subprocess.run(["git", "show", f"{ref}:backend/api/routes_resumes.py"], cwd=ROOT,
                            capture_output=True, text=True, check=True).stdout
    with tempfile.NamedTemporaryFile("w", suffix="_upstream_routes_resumes.py", delete=False) as handle:
        handle.write(source)
    spec = importlib.util.spec_from_file_location("upstream_routes_resumes", handle.name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def describe(blob: bytes) -> dict:
    document = docx.Document(io.BytesIO(blob))
    texts = [p.text for p in document.paragraphs if p.text.strip()]
    links = sorted(r.target_ref for r in document.part.rels.values() if r.reltype.endswith("/hyperlink"))
    return {"text": texts, "links": links, "author": document.core_properties.author}


def build(module, data, styled: bool):
    data = module._xml_safe(copy.deepcopy(data))
    blob = module._build_docx(data, "letter", None, None, "") if styled else module._build_docx(data, "letter", "")
    return describe(blob)


def main(ref: str) -> int:
    upstream = load_upstream(ref)
    problems = 0
    for name, data in CASES.items():
        try:
            theirs = build(upstream, data, styled=False)
        except Exception:  # noqa: BLE001 - upstream cannot read it either, nothing to compare
            continue
        try:
            ours = build(fork, data, styled=True)
        except Exception as error:  # noqa: BLE001
            problems += 1
            print(f"LOST GUARD  {name}: the fork's builder raises {type(error).__name__}, upstream's does not")
            continue
        for field in ("text", "links", "author"):
            if theirs[field] != ours[field]:
                problems += 1
                print(f"DIFFERENT   {name}: {field}\n    upstream: {str(theirs[field])[:160]}\n    fork:     {str(ours[field])[:160]}")
    print(f"{len(CASES)} inputs compared against {ref}, {problems} difference(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "upstream/main"))
