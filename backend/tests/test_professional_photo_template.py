"""`professional-photo`: one template with a per-résumé photo side, and the two legacy ids that still work.

A résumé saved with `professional-top-left-image` / `professional-top-right-image` keeps its side with no
migration; the stored id is never rewritten by loading or saving. Run through the real routes and the real renderer.
"""
import asyncio
import copy

import pytest

from backend.api.routes_resumes import _discover_templates, _render_html
from backend.models.db import Job, Resume, Setting
from backend.tests.test_professional_docx import _header_cells
from backend.tests.test_resume_docx import DATA, _fetch
from backend.tests.test_resume_pdf_footer import _fake_browser
from backend.tests.test_resume_template_ownership import UPSTREAM

LEFT, RIGHT, CANON = "professional-top-left-image", "professional-top-right-image", "professional-photo"
PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="


def reversed_(html):
    return "flex-direction: row-reverse" in html


def html_of(template, **extra):
    return _render_html({**DATA, **extra}, template, "letter", profile_image_path=PNG, profile_image_enabled=True)


def make(test_db, template, **data):
    """A base résumé stored with `template`, in an account that has a profile picture; callable more than once per test."""
    for key, value in (("dashboard_api_key", ""), ("profile_image_path", PNG)):
        if not test_db.query(Setting).filter(Setting.key == key).first():
            test_db.add(Setting(key=key, value=value))
    resume = Resume(name="Base CV", page_format="letter", is_base=True, template=template, json_data={**copy.deepcopy(DATA), **data})
    test_db.add(resume)
    test_db.commit()
    return resume


def stored(test_db, resume):
    test_db.expire_all()
    row = test_db.query(Resume).filter(Resume.id == resume.id).one()
    return row.template, copy.deepcopy(row.json_data)


# ── 1-6: the side each id renders with ───────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("template,saved,right", [
    (LEFT, None, False), (RIGHT, None, True), (CANON, None, True),                  # legacy sides kept, canonical defaults to right
    (LEFT, "right", True), (RIGHT, "left", False), (CANON, "left", False), (CANON, "right", True),      # a saved side wins
])
def test_html_uses_the_effective_side(template, saved, right):
    html = html_of(template, **({"profile_image_position": saved} if saved else {}))
    assert '<img src="data:image/png' in html
    assert reversed_(html) is right


@pytest.mark.parametrize("legacy,side", [(LEFT, "left"), (RIGHT, "right")])
def test_a_legacy_id_renders_exactly_like_the_canonical_template_with_that_side(legacy, side):
    assert html_of(legacy) == html_of(CANON, profile_image_position=side)


def test_an_invalid_saved_side_falls_back_to_the_legacy_default_and_then_to_right():
    assert not reversed_(html_of(LEFT, profile_image_position="up"))
    assert reversed_(html_of(CANON, profile_image_position="up"))


# ── discovery and rejection ─────────────────────────────────────────────────────────────────────────────────────

def test_only_one_professional_template_is_offered_and_it_lists_its_aliases(api_client):
    listed = api_client.get("/api/resumes/templates").json()
    ids = [t["id"] for t in listed]
    assert CANON in ids and LEFT not in ids and RIGHT not in ids
    photo = next(t for t in listed if t["id"] == CANON)
    assert photo["name"] == "Professional Photo" and photo["has_profile_image"] is True
    assert photo["aliases"] == {LEFT: "left", RIGHT: "right"}
    names = [t["name"] for t in listed]
    assert names.count("Professional Photo") == 1 and not [n for n in names if "Top-Left" in n or "Top-Right" in n]
    assert [t["id"] for t in _discover_templates()] == ids


@pytest.mark.parametrize("bad", ["professional-top-up-image", "nope-template", "professional-photo-x", "no_such_template", "../inter", ""])
def test_unknown_template_ids_are_still_rejected_and_never_replaced(api_client, test_db, bad):
    assert api_client.post("/api/resumes", json={"name": "X", "template": bad}).status_code == 422
    resume = make(test_db, "inter")
    assert api_client.patch(f"/api/resumes/{resume.id}", json={"template": bad}).status_code == 422
    assert api_client.get(f"/api/resumes/{resume.id}/docx", params={"template": bad}).status_code == 422
    assert stored(test_db, resume)[0] == "inter"


@pytest.mark.parametrize("hyphenated_unknown", ["professional-top-up-image", "nope-template"])
def test_an_unknown_hyphenated_id_never_falls_back_to_another_template_when_rendering(api_client, test_db, hyphenated_unknown):
    resume = make(test_db, "inter")
    assert api_client.get(f"/api/resumes/{resume.id}/preview", params={"template": hyphenated_unknown}).status_code == 422


# ── stored résumés: load, save, preview with no migration ────────────────────────────────────────────────────

@pytest.mark.parametrize("legacy,right", [(LEFT, False), (RIGHT, True)])
def test_an_old_resume_loads_and_previews_on_its_side_and_keeps_its_stored_id(api_client, test_db, legacy, right):
    resume = make(test_db, legacy)
    got = api_client.get(f"/api/resumes/{resume.id}").json()
    assert got["template"] == legacy                                         # loading rewrites nothing
    assert "profile_image_position" not in got["json_data"]
    assert reversed_(api_client.get(f"/api/resumes/{resume.id}/preview").text) is right
    assert stored(test_db, resume)[0] == legacy


@pytest.mark.parametrize("legacy,right", [(LEFT, False), (RIGHT, True)])
def test_saving_an_old_resume_does_not_lose_its_side(api_client, test_db, legacy, right):
    resume = make(test_db, legacy)
    assert api_client.patch(f"/api/resumes/{resume.id}", json={"name": "Renamed"}).status_code == 200
    data = copy.deepcopy(DATA)
    data["summary"] = "edited"                                              # the editor's autosave: the whole json_data, no side
    assert api_client.patch(f"/api/resumes/{resume.id}", json={"json_data": data}).status_code == 200
    template, saved = stored(test_db, resume)
    assert template == legacy and saved["summary"] == "edited"
    assert reversed_(api_client.get(f"/api/resumes/{resume.id}/preview").text) is right


def test_a_saved_side_survives_reloads_and_autosaves(api_client, test_db):
    resume = make(test_db, LEFT)
    data = {**copy.deepcopy(DATA), "profile_image_position": "right"}
    api_client.patch(f"/api/resumes/{resume.id}", json={"json_data": data})
    for _ in range(2):                                                       # reload: the explicit side beats the legacy default
        assert api_client.get(f"/api/resumes/{resume.id}").json()["json_data"]["profile_image_position"] == "right"
        assert reversed_(api_client.get(f"/api/resumes/{resume.id}/preview").text)
    data["summary"] = "later edit"
    api_client.patch(f"/api/resumes/{resume.id}", json={"json_data": data})
    assert stored(test_db, resume)[1]["profile_image_position"] == "right"


def test_a_new_resume_with_a_legacy_id_is_accepted_and_keeps_that_id(api_client):
    created = api_client.post("/api/resumes", json={"name": "N", "template": LEFT})
    assert created.status_code == 201 and created.json()["template"] == LEFT


# ── template switching ───────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("legacy,side", [(LEFT, "left"), (RIGHT, "right")])
def test_switching_a_legacy_resume_to_the_canonical_template_saves_its_side(api_client, test_db, legacy, side):
    resume = make(test_db, legacy)
    assert api_client.patch(f"/api/resumes/{resume.id}", json={"template": CANON}).status_code == 200
    template, data = stored(test_db, resume)
    assert template == CANON and data["profile_image_position"] == side
    assert reversed_(api_client.get(f"/api/resumes/{resume.id}/preview").text) is (side == "right")


@pytest.mark.parametrize("legacy,side", [(LEFT, "left"), (RIGHT, "right")])
def test_switching_away_and_back_does_not_forget_the_side(api_client, test_db, legacy, side):
    resume = make(test_db, legacy)
    api_client.patch(f"/api/resumes/{resume.id}", json={"template": "inter"})
    assert stored(test_db, resume)[1]["profile_image_position"] == side
    api_client.patch(f"/api/resumes/{resume.id}", json={"template": CANON})
    assert reversed_(api_client.get(f"/api/resumes/{resume.id}/preview").text) is (side == "right")


def test_a_switch_that_also_saves_the_side_keeps_the_new_choice(api_client, test_db):
    resume = make(test_db, LEFT)
    data = {**copy.deepcopy(DATA), "profile_image_position": "right"}
    api_client.patch(f"/api/resumes/{resume.id}", json={"template": CANON, "json_data": data})
    assert stored(test_db, resume)[1]["profile_image_position"] == "right"          # the explicit choice, not the legacy default


def test_the_editors_query_param_over_a_stored_legacy_row_keeps_the_legacy_side(api_client, test_db):
    """The editor asks for ?template= while the row still stores the old id."""
    resume = make(test_db, LEFT)
    assert not reversed_(api_client.get(f"/api/resumes/{resume.id}/preview", params={"template": CANON}).text)
    assert reversed_(api_client.get(f"/api/resumes/{resume.id}/preview", params={"template": RIGHT}).text)       # an old id asked for wins
    assert not reversed_(api_client.get(f"/api/resumes/{resume.id}/preview", params={"template": LEFT}).text)


def test_a_non_photo_template_is_not_touched_by_a_saved_side(api_client, test_db):
    resume = make(test_db, "inter", profile_image_position="left")
    assert api_client.patch(f"/api/resumes/{resume.id}", json={"template": "modern"}).status_code == 200
    assert stored(test_db, resume)[1]["profile_image_position"] == "left"        # carried along, never interpreted


# ── PDF and Word use the same side ───────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("template,saved,right", [(LEFT, None, False), (RIGHT, None, True), (CANON, None, True), (CANON, "left", False), (LEFT, "right", True)])
def test_the_pdf_is_rendered_on_the_effective_side(api_client, test_db, monkeypatch, template, saved, right):
    resume = make(test_db, template, **({"profile_image_position": saved} if saved else {}))
    captured = {}
    _fake_browser(monkeypatch, captured)
    assert api_client.get(f"/api/resumes/{resume.id}/pdf").status_code == 200
    assert reversed_(captured["html"]) is right


@pytest.mark.parametrize("template,saved,image_left", [(LEFT, None, True), (RIGHT, None, False), (CANON, None, False), (CANON, "left", True), (RIGHT, "left", True), (LEFT, "right", False)])
def test_the_word_file_puts_the_picture_on_the_effective_side(api_client, test_db, template, saved, image_left):
    resume = make(test_db, template, **({"profile_image_position": saved} if saved else {}))
    _, document = _fetch(api_client, resume)
    assert _header_cells(document) == ([(True, False), (False, True)] if image_left else [(False, True), (True, False)])


def test_word_follows_the_editors_query_param_over_a_stored_legacy_row(api_client, test_db):
    resume = make(test_db, LEFT)
    _, document = _fetch(api_client, resume, f"?template={CANON}")
    assert _header_cells(document) == [(True, False), (False, True)]


def test_word_and_pdf_agree_for_every_combination(api_client, test_db, monkeypatch):
    for template in (LEFT, RIGHT, CANON):
        for saved in (None, "left", "right"):
            resume = make(test_db, template, **({"profile_image_position": saved} if saved else {}))
            captured = {}
            _fake_browser(monkeypatch, captured)
            api_client.get(f"/api/resumes/{resume.id}/pdf")
            _, document = _fetch(api_client, resume)
            assert _header_cells(document)[0][0] is (not reversed_(captured["html"])), (template, saved)


# ── tailored copies ─────────────────────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def job(test_db):
    j = Job(external_id="pp1", content_hash="pp1c", company="Acme", title="PM", description="jd")
    test_db.add(j)
    test_db.commit()
    return j


@pytest.mark.parametrize("template,saved,right", [(LEFT, None, False), (RIGHT, None, True), (CANON, "left", False)])
def test_a_copy_for_a_job_keeps_the_side_in_preview_pdf_and_word(api_client, test_db, monkeypatch, job, template, saved, right):
    base = make(test_db, template, **({"profile_image_position": saved} if saved else {}))
    copy_ = api_client.post("/api/resumes/copy", json={"base_resume_id": str(base.id), "job_id": str(job.id)}).json()
    assert copy_["template"] == template                                       # the copy keeps the stored id too
    row = test_db.query(Resume).filter(Resume.id == copy_["id"]).one()
    assert reversed_(api_client.get(f"/api/resumes/{row.id}/preview").text) is right
    captured = {}
    _fake_browser(monkeypatch, captured)
    api_client.get(f"/api/resumes/{row.id}/pdf")
    assert reversed_(captured["html"]) is right
    _, document = _fetch(api_client, row)
    assert _header_cells(document)[0][0] is (not right)


@pytest.mark.asyncio
@pytest.mark.parametrize("template,saved,right", [(LEFT, None, False), (RIGHT, None, True), (CANON, "left", False)])
async def test_a_tailored_resume_keeps_the_side(api_client, test_db, monkeypatch, job, template, saved, right):
    test_db.add(Setting(key="cv_tailor_prompt", value="p {resume_json} {job_description}"))
    test_db.add(Setting(key="tailor_auto_quick_score", value="false"))
    base = make(test_db, template, **({"profile_image_position": saved} if saved else {}))

    async def fake_call(prompt, system, max_tokens):
        return {"text": '{"summary":"tailored"}', "usage": {}}
    monkeypatch.setattr("backend.analyzer.llm_client.call_cv_tailor_llm", fake_call)
    import backend.job_monitor as mon
    monkeypatch.setattr(mon, "_running", {})
    import backend.api.routes_resumes as rr
    monkeypatch.setattr(rr, "_tailoring_semaphore", None, raising=False)
    assert api_client.post("/api/resumes/tailor", json={"base_resume_id": str(base.id), "job_id": str(job.id)}).status_code == 202
    for _ in range(50):
        await asyncio.sleep(0.1)
        test_db.expire_all()
        tailored = test_db.query(Resume).filter(Resume.parent_id == base.id).first()
        if tailored:
            break
    assert tailored is not None and tailored.template == template
    assert reversed_(api_client.get(f"/api/resumes/{tailored.id}/preview").text) is right
    _, document = _fetch(api_client, tailored)
    assert _header_cells(document)[0][0] is (not right)


# ── nothing else changes ────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("name", UPSTREAM)
def test_an_official_template_ignores_the_photo_side(name):
    assert html_of(name) == html_of(name, profile_image_position="left") == html_of(name, profile_image_position="right")


@pytest.mark.parametrize("name", UPSTREAM)
def test_an_official_template_has_no_alias_and_no_photo_side(name):
    template = next(t for t in _discover_templates() if t["id"] == name)
    assert "aliases" not in template and template["has_profile_image"] is False
