"""Which résumé templates are the fork's, and what the renderer does for the others.

Fork-owned: the one `professional-photo` template (photo on the left or right, per résumé). It does its own print
layout and headings and reads `footer_reserved_in` / `section_titles` itself.
Unused until deleted: the two folders it replaced (`professional-top-*-image`). Résumés that store those ids are
served by `professional-photo` (see resume_template_aliases.py); the folders are never rendered.
Upstream-owned: every other folder. They stay as JobNavigator ships them, and the renderer applies the fork's
print layout and custom headings to their output (`backend/resume_template_overrides.py`), so a new or updated
upstream template needs no patch.
Documented exception: helvetica still carries the old template edits (its PDF has a wider right margin than the
others, which has not been reviewed), so it handles those parts itself until it is restored to upstream's file.
"""
import re
from pathlib import Path

import pytest

from backend.api.routes_resumes import _render_html
from backend.tests.fixtures.generate_fork_golden import BASE

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "resume_templates"
FORK_OWNED = ["professional-photo"]
UNUSED_LEGACY = ["professional-top-left-image", "professional-top-right-image"]
EXCEPTIONS = ["helvetica"]

FOLDERS = sorted(p.name for p in TEMPLATES_DIR.iterdir() if (p / "template.html.j2").exists())
UPSTREAM = [n for n in FOLDERS if n not in FORK_OWNED + UNUSED_LEGACY + EXCEPTIONS]


def _source(name):
    return (TEMPLATES_DIR / name / "template.html.j2").read_text()


TITLES = {"summary": "Profile & <b>Bio</b>", "experience": "Career", "skills": "Stack",
          "education": "Studies", "projects": "Work", "publications": "Papers"}
ESCAPED_SUMMARY = "Profile &amp; &lt;b&gt;Bio&lt;/b&gt;"


def _headings(html):
    return [re.sub(r"\s+", " ", t).strip() for t in re.findall(r'<div class="section-title">(.*?)</div>', html, re.S)]


def _page_margins(html):
    return [m.split() for m in re.findall(r"@page\s*\{[^}]*?\bmargin:\s*([^;]+);", html)]


def test_the_fork_owns_exactly_the_one_professional_template():
    assert all(name in FOLDERS for name in FORK_OWNED + EXCEPTIONS)
    assert len(UPSTREAM) >= 7


@pytest.mark.parametrize("name", UPSTREAM)
def test_upstream_owned_templates_carry_no_fork_code(name):
    for marker in ("section_titles", "footer_reserved_in", "break-inside"):
        assert marker not in _source(name), f"{name} is upstream's; {marker!r} belongs in the renderer"


@pytest.mark.parametrize("name", UPSTREAM)
@pytest.mark.parametrize("reserve", [0, 0.4])
def test_renderer_keeps_the_templates_margins_and_reserves_the_footer_band(name, reserve):
    margins = _page_margins(_render_html(BASE, name, "letter", footer_reserved_in=reserve))
    own, effective = margins[0], margins[-1]
    assert len(own) == 2 and len(effective) == 4, (name, margins)       # the template's "top sides", then ours
    assert effective == [own[0], own[1], f"{reserve:g}in", own[1]]
    assert re.search(r"@media print\s*\{[^}]*break-inside:\s*avoid", _render_html(BASE, name, "letter"))


@pytest.mark.parametrize("name", UPSTREAM)
def test_custom_headings_replace_the_defaults_and_are_escaped(name):
    default = _headings(_render_html(BASE, name, "letter"))
    custom_html = _render_html({**BASE, "section_titles": TITLES}, name, "letter")
    custom = _headings(custom_html)
    assert {"Experience", "Skills", "Education"} <= set(default)
    assert {"Career", "Stack", "Studies", "Work", "Papers"} <= set(custom)
    assert not {"Experience", "Skills", "Education", "Projects", "Publications"} & set(custom)
    assert custom.count(ESCAPED_SUMMARY) == 1               # replaces the heading, or adds one where the template has none
    assert len(custom) == len(default) + (0 if "Summary" in default else 1)
    assert "<b>Bio</b>" not in custom_html


@pytest.mark.parametrize("name", UPSTREAM)
def test_without_custom_headings_the_output_is_the_templates_own(name):
    html = _render_html(BASE, name, "letter")
    assert _headings(html) == _headings(_render_html({**BASE, "section_titles": {}}, name, "letter"))
    assert ESCAPED_SUMMARY not in html


@pytest.mark.parametrize("name", FORK_OWNED + EXCEPTIONS)
def test_templates_that_do_it_themselves_are_not_adjusted_twice(name):
    html = _render_html({**BASE, "section_titles": TITLES}, name, "letter", footer_reserved_in=0.4)
    assert _headings(html).count(ESCAPED_SUMMARY) == 1
    assert sum(h in {"Career", "Stack", "Studies", "Work", "Papers"} for h in _headings(html)) == 5
    assert "<style>@page" not in html                       # the renderer's print layout was not added on top
