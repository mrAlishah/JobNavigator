"""The tailored-PDF footer ("{Name} - {Job Title} - {Company}") only appears for a
resume tailored against a real, fully-known Job — never for a base resume or a
freeform tailor (no linked Job row), and never with a piece missing."""
import re
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.api.routes_resumes import _tailored_resume_footer_text, _footer_html_template, export_pdf
from backend.models.db import Job, Resume

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "resume_templates"

CANDIDATE = "Dana Okonkwo"
TITLE = "Senior Product Manager"
COMPANY = "Acme Corp"


def _job(**overrides):
    defaults = dict(
        id=uuid.uuid4(), external_id=uuid.uuid4().hex,
        title=TITLE, company=COMPANY,
    )
    defaults.update(overrides)
    return Job(**defaults)


def _resume(**overrides):
    defaults = dict(
        id=uuid.uuid4(), name="Tailored", is_base=False,
        json_data={"header": {"name": CANDIDATE}},
    )
    defaults.update(overrides)
    return Resume(**defaults)


def test_job_linked_tailored_resume_gets_the_footer():
    job = _job()
    resume = _resume(job_id=job.id)
    assert _tailored_resume_footer_text(resume, job) == f"{CANDIDATE} - {TITLE} - {COMPANY}"


def test_footer_uses_ascii_hyphen_only():
    job = _job()
    resume = _resume(job_id=job.id)
    text = _tailored_resume_footer_text(resume, job)
    assert " - " in text
    for dash in ("–", "—", "−"):  # en dash, em dash, minus sign
        assert dash not in text


def test_base_resume_gets_no_footer():
    """Base resumes are never job-linked, so there's nothing to print."""
    resume = _resume(is_base=True, job_id=None)
    assert _tailored_resume_footer_text(resume, None) == ""


def test_freeform_tailor_without_a_job_gets_no_footer():
    """A freeform tailor has no Job row (job_id is None) even though it is tailored."""
    resume = _resume(is_base=False, job_id=None,
                      json_data={"header": {"name": CANDIDATE},
                                 "_tailor_context": {"job_description": "some JD text", "source": "freeform"}})
    assert _tailored_resume_footer_text(resume, None) == ""


def test_missing_candidate_name_suppresses_the_footer():
    job = _job()
    resume = _resume(job_id=job.id, json_data={"header": {"name": ""}})
    assert _tailored_resume_footer_text(resume, job) == ""


def test_missing_job_title_suppresses_the_footer():
    job = _job(title=None)
    resume = _resume(job_id=job.id)
    assert _tailored_resume_footer_text(resume, job) == ""


def test_missing_company_suppresses_the_footer():
    job = _job(company="")
    resume = _resume(job_id=job.id)
    assert _tailored_resume_footer_text(resume, job) == ""


def test_job_linked_resume_with_no_job_row_found_gets_no_footer():
    """job_id is set but the row lookup came back empty (e.g. deleted job) -- fail closed."""
    resume = _resume(job_id=uuid.uuid4())
    assert _tailored_resume_footer_text(resume, None) == ""


def test_footer_html_escapes_the_candidate_and_job_text():
    job = _job(title="<script>alert(1)</script>", company=COMPANY)
    resume = _resume(job_id=job.id)
    text = _tailored_resume_footer_text(resume, job)
    html = _footer_html_template(text)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_empty_footer_text_renders_no_visible_markup():
    html = _footer_html_template("")
    assert "font-size" not in html


def _page_margin(html):
    """The last `@page` margin as [top, side, bottom, side]: what Chromium prints with. The renderer appends
    its footer band after the template's own rule (or the template, if it lays out its pages, writes it itself)."""
    return re.findall(r"@page\s*\{[^}]*?\bmargin:\s*([^;]+);", html)[-1].split()


# ── /resumes/{id}/pdf wiring ─────────────────────────────────────────────────
# These stub out Playwright itself (no real Chromium) and assert export_pdf calls
# page.pdf() with the right footer/margin for each of the three resume kinds.

def _fake_browser(monkeypatch, captured: dict):
    fake_page = MagicMock()

    async def fake_set_content(html, *a, **kw):
        captured["html"] = html

    async def fake_pdf(**kwargs):
        captured.update(kwargs)
        return b"%PDF-1.4\n/Type /Page\n"

    fake_page.set_content = AsyncMock(side_effect=fake_set_content)
    fake_page.pdf = AsyncMock(side_effect=fake_pdf)
    fake_page.close = AsyncMock(return_value=None)

    # 3.0 renders in a sandboxed context (JavaScript off, a route guard), not on a bare page.
    fake_context = MagicMock()
    fake_context.new_page = AsyncMock(return_value=fake_page)
    fake_context.route = AsyncMock(return_value=None)
    fake_context.close = AsyncMock(return_value=None)
    fake_browser = MagicMock()
    fake_browser.new_context = AsyncMock(return_value=fake_context)

    async def fake_get_browser():
        return fake_browser

    import backend.api.routes_resumes as routes_resumes
    monkeypatch.setattr(routes_resumes, "_get_browser", fake_get_browser)


@pytest.mark.asyncio
async def test_pdf_export_adds_footer_for_a_job_linked_tailored_resume(test_db, monkeypatch):
    job = _job()
    test_db.add(job)
    resume = _resume(job_id=job.id)
    test_db.add(resume)
    test_db.commit()

    captured = {}
    _fake_browser(monkeypatch, captured)

    await export_pdf(str(resume.id), db=test_db)

    assert captured["display_header_footer"] is True
    assert f"{CANDIDATE} - {TITLE} - {COMPANY}" in captured["footer_template"]
    assert captured["margin"]["bottom"] != "0"
    # Regression: a Playwright page.pdf() margin only reserves paint space for the
    # footer -- it does not itself shrink the CSS layout used for pagination. Without
    # the matching @page bottom margin in the rendered HTML, a wrapped bullet/entry
    # lays out flush to the physical page edge and paints on top of the footer.
    margin = _page_margin(captured["html"])
    assert (margin[0], margin[2]) == ("0.5in", "0.4in")


@pytest.mark.asyncio
async def test_pdf_export_has_no_footer_for_a_base_resume(test_db, monkeypatch):
    resume = _resume(is_base=True, job_id=None)
    test_db.add(resume)
    test_db.commit()

    captured = {}
    _fake_browser(monkeypatch, captured)

    await export_pdf(str(resume.id), db=test_db)

    assert captured["display_header_footer"] is False
    assert captured["margin"]["bottom"] == "0"
    margin = _page_margin(captured["html"])
    assert (margin[0], margin[2]) == ("0.5in", "0in")


@pytest.mark.asyncio
async def test_pdf_export_has_no_footer_for_a_freeform_tailor(test_db, monkeypatch):
    resume = _resume(
        is_base=False, job_id=None,
        json_data={"header": {"name": CANDIDATE},
                   "_tailor_context": {"job_description": "some JD", "source": "freeform"}},
    )
    test_db.add(resume)
    test_db.commit()

    captured = {}
    _fake_browser(monkeypatch, captured)

    await export_pdf(str(resume.id), db=test_db)

    assert captured["display_header_footer"] is False
    assert captured["margin"]["bottom"] == "0"
    margin = _page_margin(captured["html"])
    assert (margin[0], margin[2]) == ("0.5in", "0in")


@pytest.mark.asyncio
async def test_pdf_export_respects_disabled_footer(test_db, monkeypatch):
    job = _job()
    test_db.add(job)
    resume = _resume(job_id=job.id, json_data={"header": {"name": CANDIDATE}, "footer_enabled": False})
    test_db.add(resume)
    test_db.commit()

    captured = {}
    _fake_browser(monkeypatch, captured)
    await export_pdf(str(resume.id), db=test_db)

    assert captured["display_header_footer"] is False
    assert captured["margin"]["bottom"] == "0"


def test_copy_for_job_does_not_inherit_base_footer_setting(api_client, test_db):
    from backend.models.db import Setting

    test_db.add(Setting(key="dashboard_api_key", value=""))
    job = _job()
    test_db.add(job)
    base = _resume(is_base=True, job_id=None, json_data={"header": {"name": CANDIDATE}, "footer_enabled": False})
    test_db.add(base)
    test_db.commit()

    response = api_client.post("/api/resumes/copy", json={"base_resume_id": str(base.id), "job_id": str(job.id)})
    assert response.status_code == 200
    assert "footer_enabled" not in response.json()["json_data"]


# ── Template pagination safety (regressions for two footer/content overlaps) ────
# A resume tailored against a real job and rendered with Playwright's footerTemplate
# reproduced content painting on top of the footer in production: a page.pdf()
# margin only reserves paint space for the footer, it does not itself shrink the
# CSS layout Chromium paginates against, so a wrapped bullet/entry landed flush with
# the physical page edge -- right where the footer sits. Every template must (a) key
# its own @page bottom margin off footer_reserved_in so the two stay in lockstep, and
# (b) never let a bullet/entry be split across the resulting page boundary.
#
# Separately (independent of the footer): .page is one continuous flowing block for
# the whole résumé, not one div per physical page, so its own top padding only ever
# wraps the very start of that flow -- page 2+ of ANY multi-page résumé (footer or
# not) sat flush against the physical top edge with no margin at all. The fix moves
# that spacing into @page's own top margin, which Chromium re-applies on every page.

def _template_names():
    return sorted(p.name for p in TEMPLATES_DIR.iterdir()
                  if (p / "template.html.j2").exists())


def _page_top_margin_in(name):
    """The template's own vertical .page padding (its first `padding: TOPin ...`
    value), which @page's top margin must now match so page 1's spacing is unchanged."""
    src = (TEMPLATES_DIR / name / "template.html.j2").read_text()
    m = re.search(r"padding:\s*([\d.]+)in\s+[\d.]+in;", src)
    assert m, f"{name}: couldn't find .page's padding declaration"
    return m.group(1)


def _render_with_footer_margin(name, footer_reserved_in):
    """The renderer's output for a template. footer_reserved_in=None omits the kwarg, as the preview and
    base/freeform PDF renders call _render_html, so the default (no band) is what's under test. The official
    templates are upstream's own files; the band and the whole-entries rule are added to their output
    (backend/resume_template_overrides.py), the fork's templates write them themselves."""
    from backend.api.routes_resumes import _render_html
    kwargs = {} if footer_reserved_in is None else {"footer_reserved_in": footer_reserved_in}
    return _render_html({"header": {"name": CANDIDATE, "contact_items": []}}, name, "letter", **kwargs)


@pytest.mark.parametrize("name", _template_names())
def test_template_page_margin_reserves_the_footer_band(name):
    margin = _page_margin(_render_with_footer_margin(name, 0.4))
    assert (margin[0], margin[2]) == (f"{_page_top_margin_in(name)}in", "0.4in")


@pytest.mark.parametrize("name", _template_names())
def test_template_page_margin_is_zero_without_a_footer(name):
    """No footer_reserved_in passed (preview, base/freeform PDFs) -- no bottom band,
    but every page (not just page 1) still gets its top margin."""
    margin = _page_margin(_render_with_footer_margin(name, None))
    assert (margin[0], margin[2]) == (f"{_page_top_margin_in(name)}in", "0in")


@pytest.mark.parametrize("name", _template_names())
def test_template_reapplies_top_margin_on_every_physical_page(name):
    """Regression: .page's own top padding only wraps the start of the whole résumé
    flow, so page 2+ previously had zero top margin regardless of the footer. The top
    spacing lives in @page (reapplied by Chromium per physical page) and .page has no
    vertical padding in print, so page 1 isn't double-padded."""
    html = _render_with_footer_margin(name, None)
    assert _page_margin(html)[0] == f"{_page_top_margin_in(name)}in"
    assert re.search(r"@media print\s*\{[^}]*\.page\s*\{[^}]*padding(-top)?:\s*0", html)


@pytest.mark.parametrize("name", _template_names())
def test_template_keeps_bullets_and_entries_whole_across_a_page_break(name):
    html = _render_with_footer_margin(name, 0.4)
    blocks = [html[m.start():html.find("</style>", m.start())] for m in re.finditer("@media print", html)]
    assert any("ul.bullets li" in block and "break-inside: avoid" in block for block in blocks)
