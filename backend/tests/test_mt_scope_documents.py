"""MT-5 · Résumés, cover letters, persona, applications, interviews per account.

Requests run as the owner (A); B is a member whose rows A must never reference, change or
see. Body ids are resolved through the scoped session (backend.accounts.owned), the
Persona is one row per account, and tracer tokens stay unique install-wide without A ever
taking over B's token.
"""
import uuid

import pytest

from backend.accounts.context import unscoped, use_account
from backend.accounts.owned import get_owned, get_owned_or_404, get_persona, owned_id_or_404
from backend.models.db import Account, CoverLetter, Job, Persona, Resume, TracerLink
from backend.tests.r4_support import client, make_job, make_resume, set_setting  # noqa: F401


@pytest.fixture
def accounts(test_db):
    a = test_db.query(Account).filter(Account.role == "owner").one()
    b = Account(name="Partner", role="member")
    test_db.add(b)
    test_db.commit()
    return a.id, b.id


@pytest.fixture
def b_rows(test_db, accounts):
    _, b = accounts
    with use_account(b):
        job = make_job(test_db, company="BeeCo", title="B role")
        resume = make_resume(test_db, name="B base", json_data={"summary": "b"})
        cl = CoverLetter(name="B letter", job_id=job.id, resume_id=resume.id, json_data={})
        test_db.add(cl)
        test_db.commit()
        ids = {"job": job.id, "resume": resume.id, "cl": cl.id}
    test_db.expunge_all()
    return ids


def _unscoped_get(db, model, row_id):
    with unscoped():
        db.expire_all()
        return db.query(model).filter(model.id == row_id).first()


# ── the helper ───────────────────────────────────────────────────────────────

def test_get_owned_hides_other_accounts_rows_and_bad_ids(test_db, accounts, b_rows):
    from fastapi import HTTPException
    a, b = accounts
    with use_account(a):
        assert get_owned(test_db, Job, b_rows["job"]) is None
        assert get_owned(test_db, Job, "not-a-uuid") is None
        with pytest.raises(HTTPException) as e:
            get_owned_or_404(test_db, Resume, b_rows["resume"])
        assert e.value.status_code == 404
        assert owned_id_or_404(test_db, Job, None) is None
        assert owned_id_or_404(test_db, Job, "") is None
    with use_account(b):
        assert get_owned(test_db, Job, str(b_rows["job"])).id == b_rows["job"]


# ── résumés ──────────────────────────────────────────────────────────────────

def test_create_resume_refuses_another_accounts_job_and_parent(client, test_db, b_rows):
    r = client.post("/api/resumes", json={"name": "x", "job_id": str(b_rows["job"])})
    assert r.status_code == 404
    r = client.post("/api/resumes", json={"name": "x", "parent_id": str(b_rows["resume"])})
    assert r.status_code == 404
    with unscoped():
        assert test_db.query(Resume).filter(Resume.name == "x").count() == 0


def test_create_resume_accepts_own_job(client, test_db, accounts):
    job = make_job(test_db)
    r = client.post("/api/resumes", json={"name": "mine", "job_id": str(job.id), "is_base": False})
    assert r.status_code == 201 and r.json()["job_id"] == str(job.id)


def test_patch_resume_cannot_point_at_another_accounts_rows(client, test_db, b_rows):
    own = make_resume(test_db, name="A base")
    for field, value in (("job_id", b_rows["job"]), ("parent_id", b_rows["resume"])):
        r = client.patch(f"/api/resumes/{own.id}", json={field: str(value), "name": "changed"})
        assert r.status_code == 404
    back = _unscoped_get(test_db, Resume, own.id)
    assert back.job_id is None and back.parent_id is None and back.name == "A base"
    # clearing a link is still allowed
    assert client.patch(f"/api/resumes/{own.id}", json={"job_id": None}).status_code == 200


def test_copy_and_tailor_refuse_another_accounts_rows(client, test_db, b_rows):
    own_job = make_job(test_db, description="x" * 200)
    own_base = make_resume(test_db, name="A base", json_data={"summary": "a"})
    assert client.post("/api/resumes/copy", json={
        "base_resume_id": str(b_rows["resume"]), "job_id": str(own_job.id)}).status_code == 404
    assert client.post("/api/resumes/copy", json={
        "base_resume_id": str(own_base.id), "job_id": str(b_rows["job"])}).status_code == 404
    assert client.post("/api/resumes/tailor", json={
        "base_resume_id": str(b_rows["resume"]), "job_id": str(own_job.id)}).status_code == 404
    assert client.post("/api/resumes/tailor", json={
        "base_resume_id": str(own_base.id), "job_id": str(b_rows["job"])}).status_code == 404


# ── cover letters ────────────────────────────────────────────────────────────

def test_create_cover_letter_refuses_another_accounts_rows(client, test_db, b_rows):
    for body in ({"job_id": str(b_rows["job"])}, {"resume_id": str(b_rows["resume"])}):
        r = client.post("/api/cover-letters", json={"name": "x", **body})
        assert r.status_code == 404
    own_job = make_job(test_db)
    r = client.post("/api/cover-letters", json={"name": "ok", "job_id": str(own_job.id)})
    assert r.status_code == 201


def test_patch_cover_letter_cannot_repair_to_another_accounts_rows(client, test_db, b_rows):
    own = CoverLetter(name="A letter", json_data={})
    test_db.add(own)
    test_db.commit()
    for field, value in (("job_id", b_rows["job"]), ("resume_id", b_rows["resume"])):
        assert client.patch(f"/api/cover-letters/{own.id}", json={field: str(value)}).status_code == 404
    back = _unscoped_get(test_db, CoverLetter, own.id)
    assert back.job_id is None and back.resume_id is None


def test_generate_cover_letter_refuses_another_accounts_rows(client, test_db, b_rows):
    own_job = make_job(test_db, description="x" * 200)
    own_res = make_resume(test_db, name="A base", json_data={"summary": "a"})
    assert client.post("/api/cover-letters/generate", json={
        "resume_id": str(b_rows["resume"]), "job_id": str(own_job.id)}).status_code == 404
    assert client.post("/api/cover-letters/generate", json={
        "resume_id": str(own_res.id), "job_id": str(b_rows["job"])}).status_code == 404
    assert client.post("/api/cover-letters/generate", json={
        "resume_id": str(own_res.id), "job_id": str(own_job.id),
        "cover_letter_id": str(b_rows["cl"])}).status_code == 404


# ── persona: one per account ─────────────────────────────────────────────────

def test_each_account_gets_its_own_persona(test_db, accounts):
    a, b = accounts
    with use_account(a):
        pa = get_persona(test_db)
        pa.contact = {"first_name": "Ada"}
        test_db.commit()
    with use_account(b):
        pb = get_persona(test_db)
        assert pb.id != pa.id and pb.account_id == b
        assert pb.contact == {}
        assert get_persona(test_db).id == pb.id          # second access re-reads, no new row
    with use_account(a):
        assert get_persona(test_db).contact == {"first_name": "Ada"}
    with unscoped():
        assert test_db.query(Persona).count() == 2


def test_get_persona_without_create_returns_none(test_db, accounts):
    _, b = accounts
    with use_account(b):
        assert get_persona(test_db, create=False) is None
    with unscoped():
        assert test_db.query(Persona).filter(Persona.account_id == b).count() == 0


def test_persona_is_unique_per_account(test_db, accounts):
    from sqlalchemy.exc import IntegrityError
    a, _ = accounts
    with use_account(a):
        get_persona(test_db)
        test_db.add(Persona(contact={}))
        with pytest.raises(IntegrityError):
            test_db.commit()
        test_db.rollback()


def test_persona_endpoints_never_touch_another_accounts_persona(client, test_db, accounts):
    a, b = accounts
    with use_account(b):
        pb = get_persona(test_db)
        pb.qa_bank = [{"question": "B?", "answer": "B"}]
        test_db.commit()
        b_id = pb.id
    test_db.expunge_all()
    r = client.get("/api/persona")
    assert r.status_code == 200 and r.json()["qa_bank"] == [] and r.json()["id"] != b_id
    assert client.patch("/api/persona", json={"contact": {"first_name": "A"}}).status_code == 200
    assert client.post("/api/persona/qa-bank", json={"question": "A?", "answer": "A"}).json() == {"count": 1}
    back = _unscoped_get(test_db, Persona, b_id)
    assert back.contact == {} and back.qa_bank == [{"question": "B?", "answer": "B"}]


def test_persona_import_refuses_another_accounts_resume(client, test_db, b_rows):
    assert client.post("/api/persona/import", json={"resume_id": str(b_rows["resume"])}).status_code == 404


def test_seed_persona_only_seeds_the_owner(test_db, accounts):
    from backend.seed import seed_persona
    a, b = accounts
    with unscoped():
        with use_account(b):
            get_persona(test_db)            # a member's Persona exists, the owner's not yet
        seed_persona(test_db)
        seed_persona(test_db)
        rows = test_db.query(Persona).all()
    assert sorted(str(r.account_id) for r in rows) == sorted([str(a), str(b)])


def test_autofill_and_scoring_read_the_current_accounts_persona(test_db, accounts):
    from backend.analyzer.cv_scorer import _get_persona_text
    a, b = accounts
    with use_account(b):
        pb = get_persona(test_db)
        pb.resume_content = {"summary": "B only summary " * 5}
        test_db.commit()
    with use_account(a):
        assert _get_persona_text(test_db) == {}
    with use_account(b):
        assert "B only summary" in _get_persona_text(test_db)["Persona"]


# ── tracer tokens: unique install-wide, never taken from another account ─────

def test_tracer_token_held_by_another_account_is_avoided(test_db, accounts):
    from backend.api.routes_resumes import _rewrite_urls_with_tracers
    a, b = accounts
    set_setting(test_db, "tracer_links_enabled", "true")
    set_setting(test_db, "tracer_links_base_url", "https://t.example")
    set_setting(test_db, "tracer_links_url_style", "path_jobid")
    with use_account(b):
        rb = make_resume(test_db, name="B base")
        test_db.add(TracerLink(token="0lin", resume_id=rb.id,
                               destination_url="https://b.example", source_label="B"))
        test_db.commit()
    with use_account(a):
        ra = make_resume(test_db, name="A base")
        data = {"header": {"contact_items": [
            {"text": "LinkedIn", "url": "https://a.example", "stub": "lin"}]}}
        out = _rewrite_urls_with_tracers(data, str(ra.id), test_db)
        url = out["header"]["contact_items"][0]["url"]
        assert url.startswith("https://t.example/cv/") and not url.endswith("/0lin")
        own = test_db.query(TracerLink).filter(TracerLink.resume_id == ra.id).one()
        assert own.token != "0lin" and own.account_id == a
    with unscoped():
        test_db.expire_all()
        held = test_db.query(TracerLink).filter(TracerLink.token == "0lin").one()
        assert held.account_id == b and held.resume_id == rb.id
        assert held.destination_url == "https://b.example"
