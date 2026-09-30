"""Cover-letter PDFs keep the preview margins on every printed page (the résumé fix from #19, applied here)."""
import re
from pathlib import Path

import pytest


TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "cover_letter_templates"


def _template_names():
    return sorted(p.name for p in TEMPLATES_DIR.iterdir() if (p / "template.html.j2").exists())


@pytest.mark.parametrize("name", _template_names())
def test_cover_letter_template_repeats_preview_margins_on_every_page(name):
    source = (TEMPLATES_DIR / name / "template.html.j2").read_text(encoding="utf-8")
    # Both rules are one line each in these templates; the @page line also carries
    # Jinja braces, so match by line rather than by brace.
    page_rule = re.search(r"^@page \{(.*)\}\s*$", source, re.M)
    wrapper_rule = re.search(r"^\.page \{(.*)\}\s*$", source, re.M)
    assert page_rule and wrapper_rule, f"{name} has no @page or .page rule"
    page_margin = re.search(r"margin:\s*([^;]+);", page_rule.group(1))
    preview_padding = re.search(r"padding:\s*([^;]+);", wrapper_rule.group(1))
    assert page_margin and preview_padding, f"{name} has no @page margin or preview padding"
    assert page_margin.group(1).strip() == preview_padding.group(1).strip(), (
        f"{name} print margins differ from its preview padding")
    assert re.search(r"@media print \{[\s\S]*?\.page \{ width: auto; padding: 0; margin: 0; \}", source), (
        f"{name} must drop the wrapper padding in print, since @page carries the gutters")
