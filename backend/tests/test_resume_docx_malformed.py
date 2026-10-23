"""The Word export survives résumé JSON that the PDF templates render without complaint.

A tailored copy is written by an LLM and an imported one by a PDF parser, so a list can hold a string or
a null where an object belongs and a text field can hold a number. The upstream builder skips what it
cannot read and prints the rest; the fork's styled builder must too, with and without a template style,
or one odd entry turns the whole download into a 500.
"""
import io

import docx
import pytest

from backend.api.routes_resumes import _build_docx, _discover_templates, _xml_safe

GOOD = {
    "header": {"name": "Dana Okonkwo", "title": "PM", "contact_items": [{"text": "dana@example.com", "url": "mailto:dana@example.com"}]},
    "summary": "Product leader.",
    "experience": [{"title": "PM", "company": "Acme", "location": "Berlin", "date": "2020 - 2024", "description": "Ran it.", "bullets": ["Shipped"]}],
    "skills": {"Languages": "Go, Python"},
    "education": [{"school": "TU Berlin", "location": "Berlin", "degree": "BSc CS"}],
    "projects": [{"name": "Side", "description": "A thing", "bullets": ["Did it"]}],
    "publications": [{"name": "Paper", "description": "Journal", "bullets": ["Cited"]}],
}

# name -> (data, text that must still be printed)
MALFORMED = {
    "experience holds strings": ({**GOOD, "experience": ["Did stuff", GOOD["experience"][0]]}, "Acme"),
    "experience holds null": ({**GOOD, "experience": [None, GOOD["experience"][0]]}, "Acme"),
    "education holds strings": ({**GOOD, "education": ["TU Berlin", GOOD["education"][0]]}, "BSc CS"),
    "projects hold strings": ({**GOOD, "projects": ["Side", GOOD["projects"][0]]}, "Did it"),
    "publications hold strings": ({**GOOD, "publications": ["Paper", GOOD["publications"][0]]}, "Cited"),
    "skills is a list of strings": ({**GOOD, "skills": ["Jira", "SQL"]}, "Product leader."),
    "skills mixes strings and objects": ({**GOOD, "skills": ["Jira", {"category": "Tools", "items": "SQL"}]}, "Tools: SQL"),
    "skills object has no category": ({**GOOD, "skills": [{"items": "SQL"}]}, "SQL"),
    "summary is a number": ({**GOOD, "summary": 12}, "12"),
    "experience fields are numbers": ({**GOOD, "experience": [{"title": 7, "company": 5, "location": 3, "date": 2020, "description": 9, "bullets": [1, 2]}]}, "2020"),
    "education fields are numbers": ({**GOOD, "education": [{"school": 5, "location": 6, "degree": 7}]}, "5"),
    "project fields are numbers": ({**GOOD, "projects": [{"name": 5, "url": 6, "description": 7, "bullets": [8]}]}, "5"),
    "publication fields are numbers": ({**GOOD, "publications": [{"name": 5, "description": 7, "bullets": [8]}]}, "5"),
}

STYLES = {
    "generic template": None,
    "professional template": next(t["docx"] for t in _discover_templates() if t["id"] == "professional-photo"),
}


def _text(data, style):
    blob = _build_docx(_xml_safe(data), "letter", style, None, "")
    d = docx.Document(io.BytesIO(blob))
    return "\n".join(p.text for p in d.paragraphs)


@pytest.mark.parametrize("style", STYLES.values(), ids=STYLES.keys())
@pytest.mark.parametrize("data,expected", MALFORMED.values(), ids=MALFORMED.keys())
def test_word_export_prints_what_it_can_read(data, expected, style):
    text = _text(data, style)
    assert "Dana Okonkwo" in text
    assert expected in text
    assert "None" not in text           # a missing value prints nothing, not its repr


@pytest.mark.parametrize("style", STYLES.values(), ids=STYLES.keys())
def test_skills_heading_needs_something_to_print(style):
    text = _text({**GOOD, "skills": ["Jira", "SQL"]}, style)
    assert "Skills" not in text and "SKILLS" not in text


@pytest.mark.parametrize("style", STYLES.values(), ids=STYLES.keys())
def test_valid_resume_prints_every_section(style):
    text = _text(GOOD, style)
    for fragment in ("Product leader.", "Acme", "Go, Python", "BSc CS", "Did it", "Cited"):
        assert fragment in text
