"""Professional Word export styling and image placement."""
import re

from backend.tests.test_resume_docx import DATA, _fetch, _resume

# ── Professional templates: template-styled Word file ───────────────────────────
def _prof(api_client, test_db, tpl="professional-top-left-image", **kw):
    return _fetch(api_client, _resume(test_db, template=tpl, **kw), f"?template={tpl}")[1]


def test_professional_docx_takes_font_margins_and_section_style_from_meta_json(api_client, test_db):
    d = _prof(api_client, test_db)
    assert d.styles["Normal"].font.name == "Arial"
    sec = d.sections[0]
    assert abs(sec.left_margin.inches - 0.53) < 0.01 and abs(sec.top_margin.inches - 0.41) < 0.01
    title = next(p for p in d.paragraphs if p.text == "PROFESSIONAL EXPERIENCE")
    assert title.runs[0].bold and str(title.runs[0].font.color.rgb) == "1F4E79"
    assert re.search(r'<w:bottom [^>]*w:color="1F4E79"', title._p.xml)      # the blue rule under the title


def test_professional_docx_uses_reference_titles_and_title_dash_company_entries(api_client, test_db):
    d = _prof(api_client, test_db)
    text = [p.text for p in d.paragraphs]
    assert text[text.index("PROFESSIONAL SUMMARY") + 1] == "Product leader."
    assert "CORE SKILLS" in text and "EDUCATION & TRAINING" in text
    i = text.index("PM - Acme")
    assert text[i + 1] == "Berlin | 2020 - 2024"
    desc = d.paragraphs[i + 2]
    assert desc.text == "Ran the platform." and desc.runs[0].italic
    assert text[i + 3] == "\u2022 Shipped Go services"                       # literal bullet, hanging indent
    assert d.paragraphs[i + 3].paragraph_format.first_line_indent < 0


def test_professional_docx_contact_items_are_pipe_separated(api_client, test_db):
    d = _prof(api_client, test_db)
    assert next(p.text for p in d.paragraphs if "dana@example.com" in p.text) == "dana@example.com | Berlin"


def test_generic_template_docx_is_unchanged_by_the_professional_styling(api_client, test_db):
    d = _fetch(api_client, _resume(test_db, image=True))[1]
    assert "Summary" not in [p.text for p in d.paragraphs]
    assert len(d.inline_shapes) == 0 and not d.tables


def _header_cells(d):
    row = d.tables[0].rows[0]
    return [("pic:pic" in c._tc.xml, "Dana Okonkwo" in c.text) for c in row.cells]


def test_professional_left_template_puts_the_profile_image_left_of_the_name(api_client, test_db):
    d = _prof(api_client, test_db, image=True)
    assert len(d.inline_shapes) == 1
    assert abs(d.inline_shapes[0].width.inches - 1.25) < 0.01
    assert _header_cells(d) == [(True, False), (False, True)]


def test_professional_right_template_puts_the_profile_image_right_of_the_name(api_client, test_db):
    d = _prof(api_client, test_db, tpl="professional-top-right-image", image=True)
    assert _header_cells(d) == [(False, True), (True, False)]


def test_professional_docx_has_no_image_when_disabled_or_unset(api_client, test_db):
    d = _prof(api_client, test_db, image=True, data={**DATA, "profile_image_enabled": False})
    assert len(d.inline_shapes) == 0 and not d.tables and d.paragraphs[0].text == "Dana Okonkwo"


def test_professional_docx_content_is_larger_than_headers_stay_put(api_client, test_db):
    d = _prof(api_client, test_db)
    assert d.styles["Normal"].font.size.pt == 9.5
    by_text = {p.text: p for p in d.paragraphs}
    size = lambda text: by_text[text].runs[0].font.size.pt
    assert size("Berlin | 2020 - 2024") == 9 and size("Ran the platform.") == 9      # meta + description: content
    assert size("PM - Acme") == 9.5 and size("PROFESSIONAL EXPERIENCE") == 11.5     # entry title + section title: unchanged
    assert by_text["Dana Okonkwo"].runs[0].font.size.pt == 19
