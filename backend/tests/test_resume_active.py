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
