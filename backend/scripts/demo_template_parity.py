"""Template parity for the public demo: the in-browser (nunjucks) render must show the same text as the backend's Jinja render.

    DEMO_PARITY_OUT=/app/.parity-out bash tests/tools/fe-test.sh demoPdf     # writes the nunjucks HTML (frontend/.parity-out)
    py -3.10 backend/scripts/demo_template_parity.py frontend/.parity-out     # renders the same data here and compares

Compares visible text only (style/script/tags removed, entities decoded, whitespace collapsed). Exits 1 on any difference.
"""
import html as html_lib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.api import routes_cover_letters, routes_resumes  # noqa: E402


def visible(doc: str) -> str:
    doc = re.sub(r"(?is)<(style|script|title)\b.*?</\1>", " ", doc)
    doc = re.sub(r"(?s)<[^>]+>", " ", doc)
    return re.sub(r"\s+", " ", html_lib.unescape(doc)).strip()


def main(out_dir: Path) -> int:
    fixture = json.loads((ROOT / "frontend/src/demo/fixture.json").read_text(encoding="utf-8"))
    resume = fixture["resumes"][0]["json_data"]
    letter = json.loads((out_dir / "letter.json").read_text(encoding="utf-8"))
    bad = 0
    checked = 0
    for kind, render, data in (("resume", routes_resumes._render_html, resume),
                               ("letter", routes_cover_letters._render_html, letter)):
        for f in sorted(out_dir.glob(f"{kind}_*.html")):
            tpl = f.stem.split("_", 1)[1]
            want = visible(render(data, tpl, "a4" if kind == "letter" else "letter"))
            got = visible(f.read_text(encoding="utf-8"))
            checked += 1
            if want == got:
                print(f"OK    {kind} {tpl}")
            else:
                bad += 1
                i = next((n for n, (a, b) in enumerate(zip(want, got)) if a != b), min(len(want), len(got)))
                print(f"DIFF  {kind} {tpl} at {i}:\n   jinja:    {want[max(0, i - 40):i + 80]!r}\n   nunjucks: {got[max(0, i - 40):i + 80]!r}")
    print(f"{checked} templates compared, {bad} differ")
    return 1 if bad or not checked else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    sys.exit(main(Path(sys.argv[1])))
