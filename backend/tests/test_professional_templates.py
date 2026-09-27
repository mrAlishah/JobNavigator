"""The two Professional templates follow the ATS reference résumé: Arial (Arimo), #232323 text,
#5A5A5A secondary, #1F4E79 ruled section titles, "Title - Company" entries, • bullets."""
import re

import pytest

from backend.api.routes_resumes import _render_html
PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="

TEMPLATES = ["professional-top-left-image", "professional-top-right-image"]
DATA = {
    "header": {"name": "DANA OKONKWO", "title": "Senior Product Manager",
               "contact_items": [{"text": "dana@example.com", "url": "mailto:dana@example.com"}, {"text": "Berlin"}]},
    "summary": "Product leader.",
    "experience": [{"title": "PM", "company": "Acme", "location": "Berlin", "date": "2020 - 2024",
                    "description": "Ran the platform.", "bullets": ["Shipped **Go** services"]}],
    "skills": {"Languages": "Go, Python"},
    "education": [{"school": "TU Berlin", "location": "Berlin", "degree": "BSc CS"}],
    "projects": [{"name": "Side", "bullets": ["Did it"]}],
    "publications": [{"name": "Paper"}],
}


def _html(tpl, **kw):
    return _render_html(DATA, tpl, "letter", profile_image_path=PNG, profile_image_enabled=True, **kw)


@pytest.mark.parametrize("tpl", TEMPLATES)
def test_sections_use_the_reference_titles_in_the_reference_order(tpl):
    html = _html(tpl)
    order = ["PROFESSIONAL SUMMARY", "CORE SKILLS", "PROFESSIONAL EXPERIENCE", "EDUCATION &amp; TRAINING", "PROJECTS", "PUBLICATIONS"]
    pos = [html.find(t) for t in order]
    assert -1 not in pos, order[pos.index(-1)]
    assert pos == sorted(pos)


@pytest.mark.parametrize("tpl", TEMPLATES)
def test_palette_and_font_match_the_reference(tpl):
    html = _html(tpl).lower()
    for token in ("#1f4e79", "#232323", "#5a5a5a", "arimo", "arial"):
        assert token in html, token
    assert "inter" not in re.findall(r"font-family:[^;]*", html)[0]


@pytest.mark.parametrize("tpl", TEMPLATES)
def test_entries_read_title_dash_company_then_location_pipe_date(tpl):
    html = _html(tpl)
    assert "PM - Acme" in html
    assert re.search(r"Berlin\s*\|\s*2020 - 2024", html)
    assert "Ran the platform." in html


@pytest.mark.parametrize("tpl", TEMPLATES)
def test_contact_items_are_pipe_separated_and_bullets_use_a_dot(tpl):
    html = _html(tpl)
    assert '<span class="sep">|</span>' in html
    assert "content: '•" in html


@pytest.mark.parametrize("tpl,reversed_", [(TEMPLATES[0], False), (TEMPLATES[1], True)])
def test_profile_image_stays_on_its_side(tpl, reversed_):
    html = _html(tpl)
    assert '<img src="data:image/png' in html
    assert ("flex-direction: row-reverse" in html) is reversed_


def _size(html, selector):
    return float(re.search(re.escape(selector) + r"\s*\{[^}]*?font-size:\s*([\d.]+)pt", html).group(1))


@pytest.mark.parametrize("tpl", TEMPLATES)
def test_content_text_is_one_point_larger_than_the_reference_and_headers_are_not(tpl):
    html = _html(tpl)
    for selector, pt in [("html, body", 9.5), ("ul.bullets li", 9.5), (".skill-line", 9), (".entry-meta", 9),
                         (".entry-description", 9), (".edu-school", 9.5), (".edu-degree", 9), (".entry-url", 9)]:
        assert _size(html, selector) == pt, selector
    for selector, pt in [(".header h1", 19), (".header .title-line", 11.5), (".header .contact", 8),
                         (".section-title", 11.5), (".entry-title, .entry-name", 9.5)]:
        assert _size(html, selector) == pt, selector
