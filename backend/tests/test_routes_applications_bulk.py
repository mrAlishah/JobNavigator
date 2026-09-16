"""Tests for POST /api/applications/bulk-update and /bulk-delete.

The Applications screen's selection bar moves or deletes a whole batch in one
call. Both endpoints must behave exactly like the single-row paths they stand in
for (record_transition with source "ui"; the job released back to 'saved' on a
delete) and must answer 400 — never 500 — for any malformed body.
"""
import uuid

from backend.models.db import Application, Interview, Job, Setting


def _seed_first_run(test_db):
    """Empty dashboard_api_key row → the auth middleware runs in first-run mode."""
    test_db.add(Setting(key="dashboard_api_key", value=""))
    test_db.commit()


def _make_app(test_db, title="Bulk Role", status="applied", company="Bulk Co", job_status="applied"):
    """One job + one application on it, committed; returns the Application."""
    job = Job(external_id=uuid.uuid4().hex, company=company, title=title,
              url=f"https://example.com/{uuid.uuid4().hex}", status=job_status)
    test_db.add(job)
    test_db.flush()
    app = Application(job_id=job.id, status=status, status_transitions=[])
    test_db.add(app)
    test_db.commit()
    return app


# ── bulk-update ──────────────────────────────────────────────────────────────
def test_bulk_update_moves_every_row(api_client, test_db):
    """Happy path: two applied rows → interview, both counted as updated."""
    _seed_first_run(test_db)
    a = _make_app(test_db, "Bulk A")
    b = _make_app(test_db, "Bulk B")
    resp = api_client.post("/api/applications/bulk-update",
                           json={"ids": [str(a.id), str(b.id)], "status": "interview"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"updated": 2, "skipped": 0, "not_found": []}
    test_db.expire_all()
    assert test_db.query(Application).filter(Application.id == a.id).first().status == "interview"
    assert test_db.query(Application).filter(Application.id == b.id).first().status == "interview"


def test_bulk_update_skips_rows_already_in_that_status(api_client, test_db):
    """A row already in the target status is skipped, not re-transitioned."""
    _seed_first_run(test_db)
    a = _make_app(test_db, "Bulk Applied", status="applied")
    b = _make_app(test_db, "Bulk Interview", status="interview")
    before = len(b.status_transitions or [])
    resp = api_client.post("/api/applications/bulk-update",
                           json={"ids": [str(a.id), str(b.id)], "status": "interview"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["updated"] == 1 and body["skipped"] == 1
    test_db.expire_all()
    kept = test_db.query(Application).filter(Application.id == b.id).first()
    assert len(kept.status_transitions or []) == before, "a skipped row logged a transition"


def test_bulk_update_records_transitions_with_ui_source(api_client, test_db):
    """Every move goes through record_transition(), like the single-row PATCH."""
    _seed_first_run(test_db)
    a = _make_app(test_db, "Bulk Transitions", status="applied")
    resp = api_client.post("/api/applications/bulk-update",
                           json={"ids": [str(a.id)], "status": "offer"})
    assert resp.status_code == 200, resp.text
    test_db.expire_all()
    row = test_db.query(Application).filter(Application.id == a.id).first()
    last = (row.status_transitions or [])[-1]
    assert last["from"] == "applied" and last["to"] == "offer"
    assert last["source"] == "ui" and last.get("at")


def test_bulk_update_rejects_unknown_status(api_client, test_db):
    """An unknown stage is a 400 with the valid set named — never a 500."""
    _seed_first_run(test_db)
    a = _make_app(test_db, "Bulk Bad Status")
    resp = api_client.post("/api/applications/bulk-update",
                           json={"ids": [str(a.id)], "status": "ghosted"})
    assert resp.status_code == 400, resp.text
    assert "status must be one of" in resp.json().get("detail", "")
    test_db.expire_all()
    assert test_db.query(Application).filter(Application.id == a.id).first().status == "applied"


def test_bulk_update_rejects_missing_or_non_string_status(api_client, test_db):
    """No status, and a status of the wrong shape, are both 400s."""
    _seed_first_run(test_db)
    a = _make_app(test_db, "Bulk No Status")
    assert api_client.post("/api/applications/bulk-update",
                           json={"ids": [str(a.id)]}).status_code == 400
    assert api_client.post("/api/applications/bulk-update",
                           json={"ids": [str(a.id)], "status": ["interview"]}).status_code == 400


def test_bulk_update_rejects_non_list_ids(api_client, test_db):
    """`ids` of the wrong shape is a 400, not an iteration crash."""
    _seed_first_run(test_db)
    resp = api_client.post("/api/applications/bulk-update",
                           json={"ids": "not-a-list", "status": "interview"})
    assert resp.status_code == 400, resp.text
    assert "ids must be a list" in resp.json().get("detail", "")


def test_bulk_update_reports_bad_and_missing_ids_without_aborting(api_client, test_db):
    """A malformed id and an unknown one land in not_found; the real row still moves."""
    _seed_first_run(test_db)
    a = _make_app(test_db, "Bulk Survivor")
    ghost = str(uuid.uuid4())
    resp = api_client.post("/api/applications/bulk-update",
                           json={"ids": ["not-a-uuid", ghost, str(a.id)], "status": "interview"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["updated"] == 1
    assert set(body["not_found"]) == {"not-a-uuid", ghost}
    test_db.expire_all()
    assert test_db.query(Application).filter(Application.id == a.id).first().status == "interview"


def test_bulk_update_empty_ids_is_a_no_op(api_client, test_db):
    """An empty selection answers zeroes rather than touching anything."""
    _seed_first_run(test_db)
    resp = api_client.post("/api/applications/bulk-update", json={"ids": [], "status": "rejected"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"updated": 0, "skipped": 0, "not_found": []}


# ── bulk-delete ──────────────────────────────────────────────────────────────
def test_bulk_delete_removes_rows_and_cascades_interviews(api_client, test_db):
    """Deleting a batch takes each row's interviews with it (delete-orphan)."""
    _seed_first_run(test_db)
    a = _make_app(test_db, "Bulk Del A")
    b = _make_app(test_db, "Bulk Del B")
    a_id, b_id = a.id, b.id       # read before the delete — the instances expire with it
    test_db.add(Interview(application_id=a_id, what="Screen"))
    test_db.add(Interview(application_id=a_id, what="Onsite"))
    test_db.commit()
    assert test_db.query(Interview).filter(Interview.application_id == a_id).count() == 2

    resp = api_client.post("/api/applications/bulk-delete", json={"ids": [str(a_id), str(b_id)]})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"deleted": 2, "not_found": []}
    test_db.expire_all()
    assert test_db.query(Application).count() == 0
    assert test_db.query(Interview).filter(Interview.application_id == a_id).count() == 0


def test_bulk_delete_releases_the_job_back_to_saved(api_client, test_db):
    """The rail's Applications count drops and the job returns to the feed as Saved."""
    _seed_first_run(test_db)
    a = _make_app(test_db, "Bulk Del Job", job_status="applied")
    job_id = a.job_id
    resp = api_client.post("/api/applications/bulk-delete", json={"ids": [str(a.id)]})
    assert resp.status_code == 200, resp.text
    test_db.expire_all()
    assert test_db.query(Job).filter(Job.id == job_id).first().status == "saved"
    listing = api_client.get("/api/applications")
    assert listing.status_code == 200 and listing.json()["total"] == 0


def test_bulk_delete_reports_bad_ids_and_keeps_going(api_client, test_db):
    """Same not_found contract as bulk-update; the valid row is still deleted."""
    _seed_first_run(test_db)
    a = _make_app(test_db, "Bulk Del Survivor")
    ghost = str(uuid.uuid4())
    resp = api_client.post("/api/applications/bulk-delete",
                           json={"ids": ["nope", ghost, str(a.id)]})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["deleted"] == 1
    assert set(body["not_found"]) == {"nope", ghost}
    test_db.expire_all()
    assert test_db.query(Application).count() == 0


def test_bulk_delete_rejects_non_list_ids(api_client, test_db):
    """A malformed body is a 400 here too."""
    _seed_first_run(test_db)
    resp = api_client.post("/api/applications/bulk-delete", json={"ids": {"id": 1}})
    assert resp.status_code == 400, resp.text


# ── undo leaves no trace ─────────────────────────────────────────────────────

def _transitions(db, app_id):
    from backend.models.db import Application
    db.expire_all()
    return list(db.query(Application).filter(Application.id == app_id).one().status_transitions or [])


def test_patch_undo_pops_the_transition_it_reverses(api_client, test_db):
    _seed_first_run(test_db)
    app = _make_app(test_db, status="applied")
    api_client.patch(f"/api/applications/{app.id}", json={"status": "interview"})
    assert [t["to"] for t in _transitions(test_db, app.id)][-1:] == ["interview"]
    r = api_client.patch(f"/api/applications/{app.id}", json={"status": "applied", "undo": True})
    assert r.status_code == 200
    ts = _transitions(test_db, app.id)
    assert not any(t["to"] == "interview" for t in ts)
    test_db.expire_all()
    assert test_db.get(type(app), app.id).status == "applied"


def test_patch_undo_falls_back_to_a_normal_transition_when_history_differs(api_client, test_db):
    _seed_first_run(test_db)
    app = _make_app(test_db, status="applied")
    api_client.patch(f"/api/applications/{app.id}", json={"status": "interview"})
    api_client.patch(f"/api/applications/{app.id}", json={"status": "offer"})
    # an "undo" back to applied is not the reverse of the last move (offer <- interview)
    api_client.patch(f"/api/applications/{app.id}", json={"status": "applied", "undo": True})
    tos = [t["to"] for t in _transitions(test_db, app.id)]
    assert tos[-3:] == ["interview", "offer", "applied"]


def test_bulk_undo_pops_transitions(api_client, test_db):
    _seed_first_run(test_db)
    a, b = _make_app(test_db, status="applied"), _make_app(test_db, status="applied")
    api_client.post("/api/applications/bulk-update", json={"ids": [str(a.id), str(b.id)], "status": "rejected"})
    r = api_client.post("/api/applications/bulk-update", json={"ids": [str(a.id), str(b.id)], "status": "applied", "undo": True})
    assert r.status_code == 200 and r.json()["updated"] == 2
    for app in (a, b):
        assert not any(t["to"] == "rejected" for t in _transitions(test_db, app.id))
