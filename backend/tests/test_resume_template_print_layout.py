"""Resume PDFs keep the preview margins on every printed page."""

import re
from pathlib import Path

import pytest


TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "resume_templates"


def _template_names():
    return sorted(
        path.name
        for path in TEMPLATES_DIR.iterdir()
        if (path / "template.html.j2").exists()
    )


@pytest.mark.parametrize("name", _template_names())
def test_resume_template_repeats_preview_margins_on_every_page(name):
    source = (TEMPLATES_DIR / name / "template.html.j2").read_text()
    page_rule = source.split("@page {", 1)[1].split("}\n\nhtml", 1)[0]
    wrapper_rule = source.split(".page {", 1)[1].split("}\n\n", 1)[0]
    page_margin = re.search(r"margin:\s*([^;]+);", page_rule)
    preview_padding = re.search(r"padding:\s*([^;]+);", wrapper_rule)

    assert page_margin, f"{name} has no @page margin"
    assert preview_padding, f"{name} has no preview padding"
    assert page_margin.group(1) == preview_padding.group(1), (
        f"{name} print margins differ from its preview padding"
    )
    assert ".page { width: auto; padding: 0; margin: 0; }" in source, (
        f"{name} must remove wrapper padding when @page margins take over"
    )

