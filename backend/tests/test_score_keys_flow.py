"""Job scores stored by résumé id, shown by name, end to end (3.1): scoring, the API, rename,
delete, duplicate names, Persona/Tailored, applications, the Telegram snapshot, and the
one-time migration of 3.0 name keys."""
import json

import pytest

from backend.accounts.context import use_account
from backend.analyzer import llm_client as lc
from backend.analyzer import score_keys as K
from backend.analyzer.cv_scorer import _alert_snapshot, score_single_job
from backend.models.db import Account, Application, Job, Resume, Setting
from backend.seed import SCORE_KEYS_MIGRATED_KEY, migrate_cv_score_keys
from backend.tests.r4_support import client, make_job, make_resume, set_setting  # noqa: F401

RESUME = {"header": {"name": "A"}, "summary": "Product manager.",
          "experience": [{"title": "PM", "company": "X", "bullets": ["Shipped a product"]}],
          "skills": ["product"]}


@pytest.fixture
def scoring(test_db, monkeypatch):
    set_setting(test_db, "scoring_rubric", "Score 0-100.")
    set_setting(test_db, "scoring_output_light", '{"scores": {CV_NAMES_HERE}, "best_cv": "CV_NAME"}')
    set_setting(test_db, "scoring_output_full", '{"scores": {CV_NAMES_HERE}, "best_cv": "CV_NAME"}')
    set_setting(test_db, "prompt_caching_enabled", "false")
    replies = []

    async def fake_dispatch(provider, model, api_key, prompt, system, max_tokens, cached_prefix=None, effort=""):
        text = replies.pop(0)
        seen_prompts.append((cached_prefix or "") + prompt)
        return {"text": text, "usage": {}}
    seen_prompts = []
    monkeypatch.setattr(lc, "_dispatch", fake_dispatch)
    return replies, seen_prompts


def _job(db):
    return make_job(db, description="We need a product manager. " * 20)


def _api_job(client, job):
    r = client.get(f"/api/jobs/{job.id}")
    assert r.status_code == 200, r.text
    return r.json()


# ── scoring writes ids, the API shows names ──────────────────────────────────

@pytest.mark.asyncio
async def test_scores_are_stored_by_id_and_served_by_name(test_db, client, scoring):
    replies, _ = scoring
    r = make_resume(test_db, name="PM – Platform", json_data=RESUME)
    job = _job(test_db)
    replies.append('{"scores": {"PM – Platform": 81}, "best_cv": "PM – Platform"}')
    await score_single_job(str(job.id), cv_ids=[str(r.id)], depth="light")
    test_db.expire_all()
    row = test_db.query(Job).get(job.id)
    assert row.cv_scores == {str(r.id): 81} and row.best_cv == str(r.id)
    assert row.cv_score_names == {str(r.id): "PM – Platform"}
    body = _api_job(client, job)
    assert body["cv_scores"] == {"PM – Platform": 81} and body["best_cv"] == "PM – Platform"


@pytest.mark.asyncio
async def test_a_rename_shows_everywhere_at_once_without_a_second_entry(test_db, client, scoring):
    replies, _ = scoring
    r = make_resume(test_db, name="Old name", json_data=RESUME)
    job = _job(test_db)
    replies.append('{"scores": {"Old name": 70}, "best_cv": "Old name"}')
    await score_single_job(str(job.id), cv_ids=[str(r.id)], depth="light")
    assert client.patch(f"/api/resumes/{r.id}", json={"name": "New name"}).status_code == 200
    body = _api_job(client, job)
    assert body["cv_scores"] == {"New name": 70} and body["best_cv"] == "New name"
    # a new run after the rename merges into the same entry
    replies.append('{"scores": {"New name": 75}, "best_cv": "New name"}')
    await score_single_job(str(job.id), cv_ids=[str(r.id)], depth="light")
    assert _api_job(client, job)["cv_scores"] == {"New name": 75}


@pytest.mark.asyncio
async def test_a_deleted_resume_keeps_its_name_on_old_scores(test_db, client, scoring):
    replies, _ = scoring
    r = make_resume(test_db, name="Gone soon", json_data=RESUME)
    job = _job(test_db)
    replies.append('{"scores": {"Gone soon": 60}, "best_cv": "Gone soon"}')
    await score_single_job(str(job.id), cv_ids=[str(r.id)], depth="light")
    assert client.delete(f"/api/resumes/{r.id}").status_code in (200, 204)
    assert _api_job(client, job)["cv_scores"] == {"Gone soon": 60}


@pytest.mark.asyncio
async def test_two_resumes_with_one_name_keep_both_scores(test_db, client, scoring):
    replies, prompts = scoring
    a = make_resume(test_db, name="PM", json_data=RESUME)
    b = make_resume(test_db, name="PM", json_data={**RESUME, "summary": "Other PM."})
    job = _job(test_db)
    replies.append('{"scores": {"PM": 80, "PM (2)": 66}, "best_cv": "PM"}')
    await score_single_job(str(job.id), cv_ids=[str(a.id), str(b.id)], depth="light")
    assert "PM (2)" in prompts[0]          # the model was asked for two distinct labels
    test_db.expire_all()
    row = test_db.query(Job).get(job.id)
    assert set(row.cv_scores) == {str(a.id), str(b.id)}
    assert sorted(_api_job(client, job)["cv_scores"].values()) == [66, 80]
    assert set(_api_job(client, job)["cv_scores"]) == {"PM", "PM (2)"}


@pytest.mark.asyncio
async def test_persona_scores_keep_the_persona_key(test_db, client, scoring):
    from backend.tests.r4_support import make_persona
    replies, _ = scoring
    p = make_persona(test_db)
    p.resume_content = RESUME
    test_db.commit()
    job = _job(test_db)
    replies.append('{"scores": {"Persona": 77}, "best_cv": "Persona"}')
    await score_single_job(str(job.id), cv_ids=["persona"], depth="light")
    test_db.expire_all()
    assert test_db.query(Job).get(job.id).cv_scores == {"Persona": 77}
    assert _api_job(client, job)["cv_scores"] == {"Persona": 77}


@pytest.mark.asyncio
async def test_a_single_tailored_copy_scores_under_tailored(test_db, client, scoring):
    replies, _ = scoring
    job = _job(test_db)
    copy = make_resume(test_db, name="PM → Acme", is_base=False, json_data=RESUME, job_id=job.id)
    replies.append('{"scores": {"Tailored": 88}, "best_cv": "Tailored"}')
    await score_single_job(str(job.id), cv_ids=[str(copy.id)], depth="light")
    test_db.expire_all()
    assert test_db.query(Job).get(job.id).cv_scores == {"Tailored": 88}


# ── other readers ────────────────────────────────────────────────────────────

def test_applications_and_the_alert_show_names(test_db, client):
    r = make_resume(test_db, name="PM – Growth", json_data=RESUME)
    job = _job(test_db)
    job.cv_scores, job.best_cv, job.best_cv_score = {str(r.id): 90}, str(r.id), 90
    test_db.add(Application(job_id=job.id, status="applied"))
    test_db.commit()
    apps = client.get("/api/applications").json()["applications"]
    assert apps[0]["best_cv"] == "PM – Growth"
    assert _alert_snapshot(test_db.query(Job).get(job.id)).cv_scores == {"PM – Growth": 90}


def test_the_job_list_shows_names_and_filters_by_score(test_db, client):
    r = make_resume(test_db, name="PM", json_data=RESUME)
    job = _job(test_db)
    job.cv_scores, job.best_cv, job.best_cv_score = {str(r.id): 90}, str(r.id), 90
    test_db.commit()
    body = client.get("/api/jobs", params={"min_score": 85}).json()
    row = next(j for j in body["jobs"] if j["id"] == str(job.id))
    assert row["cv_scores"] == {"PM": 90} and row["best_cv"] == "PM"


# ── migration of 3.0 name keys ───────────────────────────────────────────────

def _owner(db):
    return db.query(Account).filter(Account.role == "owner").one().id


def test_migration_rekeys_scores_reports_and_best(test_db):
    r = make_resume(test_db, name="PM", json_data=RESUME)
    job = _job(test_db)
    job.cv_scores = {"PM": 80, "Tailored": 90, "Gone": 50, "_skipped": "x"}
    job.scoring_report = {"PM": {"summary": "s"}, "Tailored": {"summary": "t"}}
    job.best_cv = "PM"
    test_db.commit()
    assert migrate_cv_score_keys(test_db) == {str(_owner(test_db)): 1}
    test_db.expire_all()
    row = test_db.query(Job).get(job.id)
    rid = str(r.id)
    assert row.cv_scores == {rid: 80, "Tailored": 90, "Gone": 50, "_skipped": "x"}
    assert row.scoring_report == {rid: {"summary": "s"}, "Tailored": {"summary": "t"}}
    assert row.best_cv == rid and row.cv_score_names == {rid: "PM"}


def test_migration_gives_a_shared_name_to_the_newest_base_and_logs_it(test_db, caplog):
    import time
    make_resume(test_db, name="PM", json_data=RESUME)
    time.sleep(0.01)
    newer = make_resume(test_db, name="PM", json_data=RESUME)
    job = _job(test_db)
    job.cv_scores = {"PM": 70}
    test_db.commit()
    migrate_cv_score_keys(test_db)
    test_db.expire_all()
    assert test_db.query(Job).get(job.id).cv_scores == {str(newer.id): 70}
    assert any("2 résumés are named 'PM'" in rec.getMessage() for rec in caplog.records)


def test_migration_ignores_tailored_copies_that_share_a_name(test_db, caplog):
    """Re-tailoring one job gives several copies one name; copies never keyed scores by name."""
    base = make_resume(test_db, name="PM", json_data=RESUME)
    for _ in range(3):
        make_resume(test_db, name="PM → Acme — PM", is_base=False, json_data=RESUME)
    job = _job(test_db)
    job.cv_scores = {"PM": 70, "PM → Acme — PM": 60}
    test_db.commit()
    migrate_cv_score_keys(test_db)
    test_db.expire_all()
    assert test_db.query(Job).get(job.id).cv_scores == {str(base.id): 70, "PM → Acme — PM": 60}
    assert not [r for r in caplog.records if r.levelname == "WARNING"]


def test_migration_runs_once(test_db):
    r = make_resume(test_db, name="PM", json_data=RESUME)
    job = _job(test_db)
    job.cv_scores = {"PM": 70}
    test_db.commit()
    migrate_cv_score_keys(test_db)
    job = test_db.query(Job).get(job.id)
    job.cv_scores = {"PM": 71}          # a hypothetical name key written later
    test_db.commit()
    assert migrate_cv_score_keys(test_db) == {}
    test_db.expire_all()
    assert test_db.query(Job).get(job.id).cv_scores == {"PM": 71}
    assert test_db.query(Setting).filter(Setting.key == SCORE_KEYS_MIGRATED_KEY).count() == 1
    assert str(r.id)


def test_migration_never_maps_to_another_account_resume(test_db):
    b = Account(name="Partner", role="member")
    test_db.add(b)
    test_db.commit()
    with use_account(b.id):
        theirs = make_resume(test_db, name="PM", json_data=RESUME)
    job = _job(test_db)                   # the owner's job, scored by a résumé that is gone
    job.cv_scores = {"PM": 55}
    test_db.commit()
    migrate_cv_score_keys(test_db)
    test_db.expire_all()
    assert test_db.query(Job).get(job.id).cv_scores == {"PM": 55}
    assert str(theirs.id) not in json.dumps(test_db.query(Job).get(job.id).cv_score_names or {})


def test_the_marker_is_hidden_and_not_writable(test_db, client):
    set_setting(test_db, SCORE_KEYS_MIGRATED_KEY, "1")
    assert SCORE_KEYS_MIGRATED_KEY not in client.get("/api/settings").json()
    assert client.patch("/api/settings", json={SCORE_KEYS_MIGRATED_KEY: ""}).status_code == 400


def test_display_handles_legacy_rows_untouched(test_db, client):
    job = _job(test_db)
    job.cv_scores, job.best_cv = {"Old CV": 40}, "Old CV"
    job.scoring_report = {"summary": "flat", "scored_with": "Old CV"}
    test_db.commit()
    body = _api_job(client, job)
    assert body["cv_scores"] == {"Old CV": 40}
    assert body["scoring_report"] == {"Old CV": {"summary": "flat"}}


def test_resume_names_is_account_scoped(test_db):
    b = Account(name="Partner", role="member")
    test_db.add(b)
    test_db.commit()
    with use_account(b.id):
        theirs = make_resume(test_db, name="Theirs", json_data=RESUME)
    mine = make_resume(test_db, name="Mine", json_data=RESUME)
    names = K.resume_names(test_db)
    assert str(mine.id) in names and str(theirs.id) not in names
    assert isinstance(Resume, type)
