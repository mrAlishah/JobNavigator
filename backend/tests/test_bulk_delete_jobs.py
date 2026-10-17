"""POST /jobs/bulk-delete removes a job and everything created for it (tailored résumés, cover letters, applications + interviews, their tracer links), and nothing else."""
import uuid

from backend.api.routes_jobs import bulk_delete_jobs
from backend.models.db import (
    Application, CoverLetter, Interview, Job, JobLocation, JobRun, LlmCallLog, Resume, TracerLink,
)

JOB = uuid.UUID("4d444444-44d4-44d4-8444-444444444444")
OTHER = uuid.UUID("8b888888-88b8-48b8-8888-888888888888")
BASE = uuid.UUID("5e555555-55e5-45e5-8555-555555555555")


def _job(db, jid, ext):
    db.add(Job(id=jid, external_id=ext, title="PM", company="Anthropic",
               url=f"https://x.test/{ext}", status="applied",
               cv_scores={"Tailored": 30}, best_cv="Tailored", best_cv_score=30.0))


def _dependents(db, jid, tag):
    """One tailored résumé, a cover letter sharing its tracer link, an application with an interview."""
    tailored = Resume(id=uuid.uuid4(), name=f"t-{tag}", is_base=False, parent_id=BASE, job_id=jid, json_data={})
    child = Resume(id=uuid.uuid4(), name=f"c-{tag}", is_base=False, parent_id=tailored.id, job_id=jid, json_data={})
    letter = CoverLetter(id=uuid.uuid4(), name=f"l-{tag}", job_id=jid, resume_id=tailored.id, json_data={})
    db.add_all([tailored, child, letter])
    db.flush()
    db.add(TracerLink(token=f"sh{tag}", resume_id=tailored.id, cover_letter_id=letter.id,
                      destination_url="https://x.test", source_label="cv"))
    app = Application(id=uuid.uuid4(), job_id=jid)
    db.add(app)
    db.flush()
    db.add(Interview(application_id=app.id, what="screen"))
    db.add(JobLocation(job_id=jid, country="US", is_primary=True))
    db.add(LlmCallLog(purpose="tailor", job_id=jid))
    db.add(JobRun(job_type="tailor_resume", trigger="manual", target_job_id=jid))


def _seed(db):
    db.add(Resume(id=BASE, name="Base", is_base=True, json_data={}))
    _job(db, JOB, "a")
    _job(db, OTHER, "b")
    db.flush()
    _dependents(db, JOB, "1")
    _dependents(db, OTHER, "2")
    db.commit()


def test_deletes_job_and_every_dependent(test_db):
    _seed(test_db)

    assert bulk_delete_jobs({"job_ids": [str(JOB)]}, db=test_db) == {"deleted": 1, "not_found": []}

    assert test_db.query(Job).filter(Job.id == JOB).count() == 0
    for model in (Resume, CoverLetter, Application, JobLocation, LlmCallLog):
        assert test_db.query(model).filter(model.job_id == JOB).count() == 0, model
    assert test_db.query(JobRun).filter(JobRun.target_job_id == JOB).count() == 0   # unlinked, the run row is kept
    assert test_db.query(JobRun).count() == 2
    assert test_db.query(Interview).count() == 1        # OTHER's only
    assert test_db.query(TracerLink).count() == 1       # OTHER's only


def test_other_jobs_and_base_resume_survive(test_db):
    _seed(test_db)

    bulk_delete_jobs({"job_ids": [str(JOB)]}, db=test_db)

    assert test_db.query(Job).filter(Job.id == OTHER).count() == 1
    assert test_db.query(Resume).filter(Resume.job_id == OTHER).count() == 2
    assert test_db.query(CoverLetter).filter(CoverLetter.job_id == OTHER).count() == 1
    assert test_db.query(Application).filter(Application.job_id == OTHER).count() == 1
    assert test_db.query(Resume).filter(Resume.id == BASE).count() == 1


def test_bulk_selection_and_bad_ids_do_not_abort_the_batch(test_db):
    _seed(test_db)
    missing = "9c999999-99c9-49c9-8999-999999999999"

    out = bulk_delete_jobs({"job_ids": [str(JOB), "nope", missing, str(OTHER)]}, db=test_db)

    assert out == {"deleted": 2, "not_found": ["nope", missing]}
    assert test_db.query(Job).count() == 0
    assert test_db.query(Application).count() == 0


def test_a_base_resume_pointing_at_the_job_is_kept_and_unlinked(test_db):
    _seed(test_db)
    test_db.query(Resume).filter(Resume.id == BASE).update({"job_id": JOB})
    test_db.commit()

    bulk_delete_jobs({"job_ids": [str(JOB)]}, db=test_db)

    base = test_db.query(Resume).filter(Resume.id == BASE).one()
    assert base.job_id is None
