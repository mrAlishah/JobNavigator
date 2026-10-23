"""The Word export lists the sections in the order the template's PDF does.

The generic templates print Summary, Experience, Skills; the two Professional templates (reference ATS
style) print Summary, Skills, Experience and say so in their meta.json (`docx.titles` is written in that
order). Reading the PDF's own headings back is the one comparison that cannot drift from the template.
"""
import html
import io
import re

import docx
import pytest

from backend.api.routes_resumes import _build_docx, _discover_templates, _render_html, _xml_safe
from backend.tests.test_resume_docx_malformed import GOOD

TEMPLATES = sorted(_discover_templates(), key=lambda t: t["id"])


def _pdf_titles(template_id: str) -> list:
    page = _render_html(GOOD, template_id, "letter")
    return [html.unescape(t).strip() for t in re.findall(r'class="section-title"[^>]*>([^<]+)<', page)]


def _docx_paragraphs(style) -> list:
    blob = _build_docx(_xml_safe(GOOD), "letter", style, None, "")
    return [p.text for p in docx.Document(io.BytesIO(blob)).paragraphs]


@pytest.mark.parametrize("tpl", TEMPLATES, ids=[t["id"] for t in TEMPLATES])
def test_word_sections_follow_the_templates_pdf_order(tpl):
    pdf = _pdf_titles(tpl["id"])
    texts = _docx_paragraphs(tpl.get("docx"))
    in_word = [t for t in pdf if t in texts]            # the generic Word file has no Summary heading
    assert len(in_word) >= 5, (pdf, texts)
    assert [t for t in texts if t in in_word] == in_word


def test_generic_templates_put_experience_before_skills():
    texts = _docx_paragraphs(None)
    assert texts.index("Experience") < texts.index("Skills")


def test_professional_templates_keep_skills_before_experience():
    for tpl in TEMPLATES:
        if tpl.get("docx"):
            texts = _docx_paragraphs(tpl["docx"])
            titles = tpl["docx"]["titles"]
            assert texts.index(titles["Skills"]) < texts.index(titles["Experience"])
