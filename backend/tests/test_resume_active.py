"""Base résumés have an active flag: inactive ones are hidden from pickers and refused as sources."""
from backend.models.db import Resume, Setting

DATA = {"summary": "s", "experience": [], "skills": {}}


def _base(db, name, active=True):
    r = Resume(name=name, is_base=True, template="inter", json_data=DATA, active=active)
    db.add(r)
    db.commit()
    return r


def test_default_active_and_patch_toggle(api_client, test_db):
    r = Resume(name="A", is_base=True, template="inter", json_data=DATA)
    test_db.add(r)
    test_db.commit()
    assert r.active is True
    resp = api_client.patch(f"/api/resumes/{r.id}", json={"active": False})
    assert resp.status_code == 200 and resp.json()["active"] is False


def test_list_and_shelf_expose_active(api_client, test_db):
    on = _base(test_db, "On")
    off = _base(test_db, "Off", active=False)   # newer, so only the sort puts it second
    ids = lambda rows: {x["id"] for x in rows}  # noqa: E731
    assert ids(api_client.get("/api/resumes?is_base=true&active=true").json()) == {str(on.id)}
    assert ids(api_client.get("/api/resumes?is_base=true&active=false").json()) == {str(off.id)}
    shelf = [(b["id"], b["active"]) for b in api_client.get("/api/resumes/shelf").json()["bases"]]
    assert shelf == [(str(on.id), True), (str(off.id), False)]   # active first even though Off was created first


def test_inactive_base_refused_as_source(api_client, test_db):
    off = _base(test_db, "Off", active=False)
    resp = api_client.post("/api/resumes/tailor", json={"base_resume_id": str(off.id), "job_description": "x"})
    assert resp.status_code == 400
    resp = api_client.post("/api/resumes/copy", json={"base_resume_id": str(off.id), "job_id": str(off.id)})
    assert resp.status_code == 400
    resp = api_client.patch("/api/settings", json={"default_resume_id": str(off.id)})
    assert resp.status_code == 422


def test_scorer_ignores_inactive(test_db):
    from backend.analyzer.cv_scorer import _get_default_resume, _get_resume_texts
    _base(test_db, "On")
    off = _base(test_db, "Off", active=False)
    assert "Off" not in _get_resume_texts(test_db)
    test_db.add(Setting(key="default_resume_id", value=str(off.id)))
    test_db.commit()
    assert _get_default_resume(test_db) == {}


# ── the rest of the places an inactive base must stay out of, and the exceptions that are meant ──────────────

def test_company_refuses_a_new_inactive_base_and_drops_a_stored_one(api_client, test_db):
    from backend.models.db import Company
    on, off = _base(test_db, "On"), _base(test_db, "Off", active=False)
    fresh = Company(name="Fresh", scrape_urls=[], aliases=[], active=True, playwright_enabled=True, selected_resume_ids=[str(on.id)])
    stored = Company(name="Stored", scrape_urls=[], aliases=[], active=True, playwright_enabled=True,
                     selected_resume_ids=[str(off.id), str(on.id)])
    test_db.add_all([fresh, stored])
    test_db.commit()
    assert api_client.patch(f"/api/companies/{fresh.id}", json={"selected_resume_ids": [str(off.id)]}).status_code == 422
    resp = api_client.patch(f"/api/companies/{stored.id}", json={"selected_resume_ids": [str(off.id), str(on.id)]})
    assert resp.status_code == 200 and resp.json()["selected_resume_ids"] == [str(on.id)]


def test_company_scoring_texts_skip_an_inactive_selection(test_db):
    from backend.analyzer.cv_scorer import _get_resume_texts_for_company
    from backend.models.db import Company
    on, off = _base(test_db, "On"), _base(test_db, "Off", active=False)
    co = Company(name="Acme", scrape_urls=[], aliases=[], active=True, playwright_enabled=True,
                 selected_resume_ids=[str(off.id), str(on.id)])
    test_db.add(co)
    test_db.commit()
    assert "Off" not in _get_resume_texts_for_company(test_db, co)


def test_default_resume_ids_ignore_an_inactive_default_until_it_is_reactivated(test_db):
    from backend.accounts.settings import default_resume_ids
    off = _base(test_db, "Off", active=False)
    test_db.add(Setting(key="default_resume_id", value=str(off.id)))
    test_db.commit()
    assert default_resume_ids(test_db) == []
    off.active = True
    test_db.commit()
    assert default_resume_ids(test_db) == [str(off.id)]


def test_reactivated_base_is_accepted_again(api_client, test_db):
    off = _base(test_db, "Off", active=False)
    assert api_client.patch("/api/settings", json={"default_resume_id": str(off.id)}).status_code == 422
    assert api_client.patch(f"/api/resumes/{off.id}", json={"active": True}).status_code == 200
    assert api_client.patch("/api/settings", json={"default_resume_id": str(off.id)}).status_code == 200


def test_inactive_base_keeps_its_tailored_copies_listed(api_client, test_db):
    off = _base(test_db, "Off", active=False)
    copy = Resume(name="Tailored", is_base=False, parent_id=off.id, template="inter", json_data=DATA)
    test_db.add(copy)
    test_db.commit()
    assert {r["id"] for r in api_client.get("/api/resumes?is_base=false").json()} == {str(copy.id)}


def test_seeding_counts_an_inactive_base_as_the_users_own(test_db):
    from backend import seed
    _base(test_db, "Mine", active=False)
    seed.seed_mock_resume(test_db)
    assert [r.name for r in test_db.query(Resume).all()] == ["Mine"]


def test_account_copy_keeps_an_inactive_base_inactive(test_db):
    import uuid
    from backend.accounts.admin import _clone
    off = _base(test_db, "Off", active=False)
    copied = _clone(off, Resume, uuid.uuid4(), skip=("created_at", "updated_at"), parent_id=None, job_id=None, id=uuid.uuid4())
    assert copied.active is False and copied.name == "Off"
