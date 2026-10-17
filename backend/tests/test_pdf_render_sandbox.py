"""PDF rendering is sandboxed (R30 D-04), and the export paths tolerate odd JSON (D-02).

Résumé and cover-letter text is printed into the templates as raw HTML (the contact line
is built as markup inside the templates, so Jinja autoescape stays off), and every
account shares the backend's Chromium. So `_render_pdf` renders in a fresh context with
JavaScript off and no network except Google Fonts, which several templates @import; the
/preview HTML carries a CSP with the same rules; and a project link with a script-capable
scheme is never printed.
"""
import logging
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.api import routes_cover_letters as rcl
from backend.api import routes_resumes as rr
from backend.models.db import CoverLetter, Resume, Setting
from backend.tests.r4_support import client  # noqa: F401


# ── which requests the renderer lets through ─────────────────────────────────

@pytest.mark.parametrize("url", [
    "https://fonts.googleapis.com/css2?family=Inter:wght@400;700&display=swap",
    "https://fonts.gstatic.com/s/inter/v13/abc.woff2",
    "https://fonts.googleapis.com:443/css2?family=Inter",
    "data:font/truetype;base64,AAAA",
    "about:blank",
])
def test_google_fonts_and_inline_data_are_allowed(url):
    assert rr._pdf_request_allowed(url) is True


@pytest.mark.parametrize("url", [
    "http://fonts.googleapis.com/css2?family=Inter",          # not https
    "https://fonts.googleapis.com.evil.example/css2",          # look-alike host
    "https://evil.example/?u=https://fonts.googleapis.com/",   # host only in the query
    "https://user@fonts.googleapis.com/css2",                  # credentials
    "https://fonts.googleapis.com:8443/css2",                  # another port
    "http://backend:8000/api/settings",
    "http://127.0.0.1:8000/api/health",
    "http://169.254.169.254/latest/meta-data/",
    "file:///etc/passwd",
    "ws://fonts.googleapis.com/",
    "https://fonts.googleapis.com:99999/",                     # urlsplit raises on the port
])
def test_everything_else_is_blocked(url):
    assert rr._pdf_request_allowed(url) is False


def _route(url):
    route = MagicMock()
    route.request.url = url
    route.continue_ = AsyncMock()
    route.abort = AsyncMock()
    return route


@pytest.mark.asyncio
async def test_route_guard_continues_fonts_and_aborts_and_logs_the_rest(caplog):
    fonts = _route("https://fonts.gstatic.com/s/x.woff2")
    internal = _route("http://backend:8000/api/settings")
    with caplog.at_level(logging.WARNING, logger="jobnavigator.resumes"):
        await rr._pdf_route_guard(fonts)
        await rr._pdf_route_guard(internal)
    fonts.continue_.assert_awaited_once()
    fonts.abort.assert_not_awaited()
    internal.abort.assert_awaited_once()
    internal.continue_.assert_not_awaited()
    assert "blocked a request to http://backend:8000/api/settings" in caplog.text


# ── _render_pdf wiring (Playwright stubbed) ──────────────────────────────────

def _fake_browser(monkeypatch, set_content_error=None):
    page = MagicMock()
    page.set_content = AsyncMock(side_effect=set_content_error)
    page.pdf = AsyncMock(return_value=b"%PDF-1.4\n/Type /Page\n")
    context = MagicMock()
    context.new_page = AsyncMock(return_value=page)
    context.route = AsyncMock()
    context.close = AsyncMock()
    browser = MagicMock()
    browser.new_context = AsyncMock(return_value=context)

    async def get_browser():
        return browser

    monkeypatch.setattr(rr, "_get_browser", get_browser)
    return browser, context, page


@pytest.mark.asyncio
async def test_render_pdf_uses_a_fresh_script_free_guarded_context(monkeypatch):
    browser, context, page = _fake_browser(monkeypatch)
    out = await rr._render_pdf("<p>x</p>", format="Letter")
    assert out.startswith(b"%PDF")
    browser.new_context.assert_awaited_once_with(java_script_enabled=False)
    context.route.assert_awaited_once_with("**/*", rr._pdf_route_guard)
    page.pdf.assert_awaited_once_with(format="Letter")
    context.close.assert_awaited_once()
    assert not browser.new_page.called


@pytest.mark.asyncio
async def test_render_pdf_closes_the_context_when_the_render_fails(monkeypatch):
    _, context, _ = _fake_browser(monkeypatch, set_content_error=RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        await rr._render_pdf("<p>x</p>")
    context.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_cover_letter_pdf_goes_through_the_sandboxed_renderer(test_db, monkeypatch):
    browser, _, page = _fake_browser(monkeypatch)
    cl = CoverLetter(id=uuid.uuid4(), name="Letter", template=sorted(rr.template_paths(rcl.TEMPLATES_DIR))[0],
                     json_data={"header": {"name": "Dana", "contact_items": [{"text": None, "url": 5}]},
                                "body_paragraphs": ["<script>document.title='x'</script>"]})
    test_db.add(cl)
    test_db.commit()
    response = await rcl.export_pdf(str(cl.id), db=test_db)
    assert response.status_code == 200
    browser.new_context.assert_awaited_once_with(java_script_enabled=False)
    page.set_content.assert_awaited_once()


# ── a real Chromium: script never runs, internal requests never leave ────────

SAFE_TEXT = "Plain résumé text"
PAYLOAD = (
    f"<p>{SAFE_TEXT}</p>"
    "<script>document.body.innerHTML = 'PWNED-BY-SCRIPT'; document.title = 'pwned'</script>"
    "<iframe src=\"javascript:parent.document.body.innerHTML='PWNED-BY-IFRAME'\"></iframe>"
    "<img src=\"http://127.0.0.1:9/internal-probe.png\">"
    "<link rel=\"stylesheet\" href=\"http://169.254.169.254/latest/meta-data/\">"
)


async def _chromium_or_skip():
    try:
        from playwright.async_api import async_playwright
        pw = await async_playwright().start()
    except Exception as error:   # noqa: BLE001
        pytest.skip(f"Playwright unavailable: {error}")
    try:
        return pw, await pw.chromium.launch(headless=True)
    except Exception as error:   # noqa: BLE001
        await pw.stop()
        pytest.skip(f"Chromium unavailable: {error}")


def _pdf_text(pdf: bytes) -> str:
    import io

    import pdfplumber
    with pdfplumber.open(io.BytesIO(pdf)) as doc:
        return "\n".join(page.extract_text() or "" for page in doc.pages)


@pytest.mark.asyncio
async def test_injected_script_does_not_run_and_internal_requests_are_blocked(monkeypatch, caplog):
    pw, browser = await _chromium_or_skip()
    try:
        # Control: the same payload in an ordinary page does run, so the check below means something.
        page = await browser.new_page()
        await page.set_content(PAYLOAD)
        assert "PWNED" in await page.inner_text("body")
        await page.close()

        async def get_browser():
            return browser

        monkeypatch.setattr(rr, "_get_browser", get_browser)
        with caplog.at_level(logging.WARNING, logger="jobnavigator.resumes"):
            pdf = await rr._render_pdf(f"<html><body>{PAYLOAD}</body></html>", format="Letter")
        text = _pdf_text(pdf)
        assert SAFE_TEXT in text
        assert "PWNED" not in text
        assert "blocked a request to http://127.0.0.1:9/internal-probe.png" in caplog.text
        assert "blocked a request to http://169.254.169.254/latest/meta-data/" in caplog.text
    finally:
        await browser.close()
        await pw.stop()


# ── the links a template prints ──────────────────────────────────────────────

@pytest.mark.parametrize("url", ["javascript:alert(1)", " JAVASCRIPT:alert(1)", "java\tscript:alert(1)",
                                 "vbscript:x", "data:text/html,<script>x</script>", "file:///etc/passwd"])
def test_a_script_capable_project_link_is_not_printed(url):
    out = rr._render_safe_links({"projects": [{"name": "p", "url": url}],
                                 "publications": [{"title": "q", "url": url}]})
    assert out["projects"][0]["url"] == "" and out["publications"][0]["url"] == ""


@pytest.mark.parametrize("url", ["https://zoe.example/side", "zoe.dev:8080/x", "github.com/zoe", "mailto:a@b.c"])
def test_ordinary_project_links_are_kept(url):
    assert rr._render_safe_links({"projects": [{"name": "p", "url": url}]})["projects"][0]["url"] == url


def test_contact_items_are_normalised_for_the_templates():
    out = rr._render_safe_links({"header": {"name": "D", "contact_items": [
        {"text": None, "url": "https://x.example"}, {"text": 5, "url": 7}, "loose string", None,
        {"text": "a", "url": None}]}})
    assert out["header"]["contact_items"] == [
        {"text": "", "url": "https://x.example"}, {"text": "5", "url": "7"}, {"text": "a", "url": ""}]
    assert rr._render_safe_links({"header": None}) == {"header": None}
    assert rr._render_safe_links({"header": {"contact_items": None}})["header"]["contact_items"] == []


ODD = [
    {"header": None},
    {"header": "Dana"},
    {"header": {"name": "D", "contact_items": None}},
    {"header": {"name": "D", "contact_items": [{"text": None, "url": "https://x.example"}]}},
    {"header": {"name": "D", "contact_items": [{"text": 5, "url": 5}]}},
    {"header": {"name": "D", "contact_items": ["https://x.example", None]}},
    {"header": {"name": "D"}, "projects": [{"name": "p", "url": "javascript:alert(2)"}]},
]


@pytest.mark.parametrize("template", sorted(rr.template_paths(rr.TEMPLATES_DIR)))
def test_every_resume_template_renders_odd_shapes(template):
    for data in ODD:
        html = rr._render_html(data, template, "letter")
        assert 'href="javascript:' not in html
        assert ">None<" not in html


@pytest.mark.parametrize("template", sorted(rr.template_paths(rcl.TEMPLATES_DIR)))
def test_every_cover_letter_template_renders_odd_shapes(template):
    for data in ODD:
        html = rcl._render_html(data, template, "letter")
        assert ">None<" not in html


# ── the routes with tracer links on (R30 D-02) ───────────────────────────────

def _tracers_on(test_db):
    for key, value in (("tracer_links_enabled", "true"), ("tracer_links_base_url", "https://t.example"),
                       ("tracer_links_url_style", "path")):
        test_db.add(Setting(key=key, value=value))
    test_db.commit()


def _resume(test_db, data):
    resume = Resume(id=uuid.uuid4(), name="Base", is_base=True, template="inter", page_format="letter", json_data=data)
    test_db.add(resume)
    test_db.commit()
    return resume


@pytest.mark.parametrize("data", ODD)
def test_pdf_and_preview_survive_odd_shapes_with_tracers_on(client, test_db, monkeypatch, data):
    _tracers_on(test_db)
    _fake_browser(monkeypatch)
    resume = _resume(test_db, data)
    for path in ("pdf", "preview"):
        r = client.get(f"/api/resumes/{resume.id}/{path}")
        assert r.status_code == 200, (path, r.status_code, r.text)


def test_preview_carries_a_script_free_csp(client, test_db):
    resume = _resume(test_db, {"header": {"name": "D"}, "summary": "<script>x()</script>"})
    r = client.get(f"/api/resumes/{resume.id}/preview")
    assert r.status_code == 200
    csp = r.headers["content-security-policy"]
    assert csp.startswith("sandbox;") and "default-src 'none'" in csp and "script-src" not in csp


def test_pdf_filename_keeps_a_non_ascii_name(client, test_db, monkeypatch):
    _fake_browser(monkeypatch)
    resume = _resume(test_db, {"header": {"name": "José Müller"}})
    r = client.get(f"/api/resumes/{resume.id}/pdf")
    assert r.status_code == 200
    disposition = r.headers["content-disposition"]
    assert 'filename="JoseMuller_Base_Resume.pdf"' in disposition
    assert "filename*=UTF-8''Jos%C3%A9M%C3%BCller_Base_Resume.pdf" in disposition


# ── cover-letter download filename: the same two-name header as the résumé ──

def _letter(test_db, name, **kw):
    cl = CoverLetter(id=uuid.uuid4(), name=kw.pop("label", "Letter"), template=sorted(rr.template_paths(rcl.TEMPLATES_DIR))[0],
                     json_data={"header": {"name": name}, "body_paragraphs": ["Hello."]}, **kw)
    test_db.add(cl)
    test_db.commit()
    return cl


@pytest.mark.parametrize("name,company,ascii_stem,utf8_stem", [
    ("José Müller", "Acme", "JoseMuller_Acme_CoverLetter_77", "JoséMüller_Acme_CoverLetter_77"),
    ("Dana Okonkwo", "Acme", "DanaOkonkwo_Acme_CoverLetter_77", "DanaOkonkwo_Acme_CoverLetter_77"),
    ("Дана", "Ромашка", "CoverLetter_77", "Дана_Ромашка_CoverLetter_77"),
    ("王小明", "Acme", "Acme_CoverLetter_77", "王小明_Acme_CoverLetter_77"),
])
def test_cover_letter_pdf_filename_keeps_a_non_ascii_name(client, test_db, monkeypatch, name, company, ascii_stem, utf8_stem):
    from urllib.parse import quote, unquote
    from backend.tests.r4_support import make_job

    _fake_browser(monkeypatch)
    job = make_job(test_db, company=company, short_id=77)
    cl = _letter(test_db, name, job_id=job.id)
    r = client.get(f"/api/cover-letters/{cl.id}/pdf")
    assert r.status_code == 200
    disposition = r.headers["content-disposition"]
    assert f'filename="{ascii_stem}.pdf"' in disposition
    assert f"filename*=UTF-8''{quote(utf8_stem + '.pdf', safe='')}" in disposition
    assert unquote(disposition.split("UTF-8''", 1)[1]) == f"{utf8_stem}.pdf"
    disposition.encode("latin-1")   # a header value must stay encodable


def test_cover_letter_pdf_filename_cannot_break_the_header(client, test_db, monkeypatch):
    _fake_browser(monkeypatch)
    cl = _letter(test_db, 'Zoë "x";\r\nSet-Cookie: a=b')
    r = client.get(f"/api/cover-letters/{cl.id}/pdf")
    assert r.status_code == 200
    disposition = r.headers["content-disposition"]
    assert "\r" not in disposition and "\n" not in disposition
    assert disposition.startswith('attachment; filename="Zoe')
    assert disposition.count('"') == 2   # only the two around the ASCII name
