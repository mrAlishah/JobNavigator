"""When the renderer adds the fork's behaviour to a template, and that it never changes a heading twice.

`_render_html` adds the print layout and the custom headings to a template's output unless the template reads
`footer_reserved_in` / `section_titles` itself. "Reads" is decided by Jinja's parser (comments and string text do
not count; `{% include %}`d partials do), not by searching the source text. Custom headings are swapped in one
pass, so a title that is another section's default name ("Experience" as the Summary title) is kept as written.
"""
import re
import shutil
from pathlib import Path

import pytest

from backend import resume_template_overrides as overrides
from backend.api import routes_resumes
from backend.api.routes_resumes import _render_html
from backend.tests.fixtures.generate_fork_golden import BASE
from backend.tests.test_resume_template_ownership import EXCEPTIONS, FORK_OWNED, TEMPLATES_DIR, UPSTREAM, _headings

KEYS = ("summary", "experience", "skills", "education", "projects", "publications")
# every section titled with another section's default name: a permutation of the six defaults
ROTATED = {"summary": "Experience", "experience": "Skills", "skills": "Education", "education": "Summary",
           "projects": "Publications", "publications": "Projects"}
EVERY_TEMPLATE = UPSTREAM + FORK_OWNED + EXCEPTIONS


def _section_order(name):
    """The sections in the order a template prints them: give each a distinct title and read them back."""
    sentinels = {k: f"T-{k}" for k in KEYS}
    return [h[2:] for h in _headings(_render_html({**BASE, "section_titles": sentinels}, name, "letter")) if h.startswith("T-")]


@pytest.mark.parametrize("name", EVERY_TEMPLATE)
def test_a_title_that_is_another_sections_default_name_is_kept_as_written(name):
    order = _section_order(name)
    assert len(order) == 6, (name, order)
    shown = _headings(_render_html({**BASE, "section_titles": ROTATED}, name, "letter"))
    assert shown == [ROTATED[k] for k in order]


def test_the_swap_is_one_pass_on_plain_markup():
    html = "".join(f'<div class="section-title">{t}</div><p>{t}</p>' for t in ("Summary", "Experience", "Skills"))
    out = overrides.section_titles(html, {"summary": "Experience", "experience": "Skills", "skills": "Summary"})
    assert re.findall(r'section-title">([^<]*)<', out) == ["Experience", "Skills", "Summary"]
    assert out.count("<p>Summary</p>") == 1                                   # body text is not a heading


@pytest.mark.parametrize("name", EVERY_TEMPLATE)
def test_the_layout_is_added_once_to_an_official_template_and_never_to_the_others(name):
    html = _render_html(BASE, name, "letter", footer_reserved_in=0.4)
    assert html.count("<style>@page") == (1 if name in UPSTREAM else 0)
    own = (TEMPLATES_DIR / name / "template.html.j2").read_text().count("break-inside: avoid")
    assert html.count("break-inside: avoid") == own + (1 if name in UPSTREAM else 0)


# ── detection: comments and includes ────────────────────────────────────────────────────────────────────────────

TITLES = {"experience": "Career"}


@pytest.fixture
def lab(tmp_path, monkeypatch):
    """A copy of the official `inter` template the test can edit, rendered through the real `_render_html`."""
    root = tmp_path / "templates"
    shutil.copytree(TEMPLATES_DIR / "inter", root / "inter")
    monkeypatch.setattr(routes_resumes, "TEMPLATES_DIR", root)
    folder = Path(root / "inter")
    original = (folder / "template.html.j2").read_text()

    def render(source=None, **partials):
        (folder / "template.html.j2").write_text(original if source is None else source(original))
        for filename, text in partials.items():
            (folder / filename.replace("__", ".")).write_text(text)
        html = _render_html({**BASE, "section_titles": TITLES}, "inter", "letter", footer_reserved_in=0.4)
        return "<style>@page" in html, "Career" in _headings(html)          # layout added, headings swapped
    return render


def test_a_pristine_official_template_gets_both(lab):
    assert lab() == (True, True)


@pytest.mark.parametrize("source", [
    lambda s: "{# footer_reserved_in, section_titles #}\n" + s,
    lambda s: s.replace("</body>", "<!-- footer_reserved_in section_titles --></body>"),
    lambda s: s.replace("<style>", "<style>\n/* footer_reserved_in section_titles */", 1),
    lambda s: s.replace("</body>", "{{ 'footer_reserved_in section_titles' }}</body>"),
], ids=["jinja comment", "html comment", "css comment", "string literal"])
def test_a_mention_in_a_comment_or_a_string_does_not_switch_anything_off(lab, source):
    assert lab(source) == (True, True)


def test_a_template_that_reads_the_band_variable_keeps_its_own_layout(lab):
    assert lab(lambda s: s.replace("</body>", "<!--{{ footer_reserved_in }}--></body>")) == (False, True)


def test_a_template_that_reads_the_titles_variable_keeps_its_own_headings(lab):
    assert lab(lambda s: s.replace("</body>", "<!--{{ section_titles.summary }}--></body>")) == (True, False)


def test_a_partial_that_only_has_comments_changes_nothing(lab):
    assert lab(lambda s: s.replace("</body>", "{% include '_p.j2' %}</body>"), _p__j2="{# footer_reserved_in section_titles #}") == (True, True)


def test_a_partial_that_reads_a_variable_counts_for_the_template(lab):
    include = lambda s: s.replace("</body>", "{% include '_p.j2' %}</body>")  # noqa: E731
    assert lab(include, _p__j2="{{ footer_reserved_in }}") == (False, True)
    assert lab(include, _p__j2="{{ section_titles.summary }}") == (True, False)


def test_a_partial_inside_a_partial_counts_too(lab):
    assert lab(lambda s: s.replace("</body>", "{% include '_a.j2' %}</body>"), _a__j2="{% include '_b.j2' %}", _b__j2="{{ section_titles }}") == (True, False)


def test_an_include_whose_name_is_not_known_ahead_still_gets_the_overrides(lab):
    """A dynamic include cannot be read, so the parser cannot say; the safe default is to add the overrides,
    which only touch the structures they recognise."""
    source = lambda s: s.replace("</body>", "{% set p = '_p.j2' %}{% include p %}</body>")  # noqa: E731
    assert lab(source, _p__j2="") == (True, True)


def test_a_missing_partial_that_is_never_reached_does_not_break_the_render(lab):
    assert lab(lambda s: s.replace("</body>", "{% if false %}{% include '_missing.j2' %}{% endif %}</body>")) == (True, True)
