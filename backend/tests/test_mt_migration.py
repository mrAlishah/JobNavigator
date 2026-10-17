"""MT-1 · the Account model and the account_id backfill in seed.run_migrations.

The fixture DB is SQLite: the Postgres ALTERs are skipped there (create_all already
built the column from the model), but the owner creation and the backfill UPDATEs
are dialect-neutral and run for real.
"""
import pytest

import backend.seed as seed
from backend.models.db import (
    Account, ActivityLog, Application, Company, CoverLetter, Interview, Job, JobRun,
    LlmCallLog, Persona, Resume, ScrapeLog, Search, TracerLink, get_owner_id,
)
from backend.tests.r4_support import make_job

# These tests start from a database without an owner (MT-2's test_db creates one).
pytestmark = pytest.mark.mt_no_owner


def _seed_one_of_each(db):
    """One account-less row in every table the migration covers."""
    job = make_job(db)
    app = Application(job_id=job.id)
    resume = Resume(name="R")
    db.add_all([Search(name="S"), Company(name="C"), app, resume,
                CoverLetter(name="L"), JobRun(job_type="t", trigger="manual"),
                ScrapeLog(), LlmCallLog(purpose="p"), ActivityLog(type="t", message="m")])
    db.commit()
    db.add_all([Interview(application_id=app.id, what="Call"),
                TracerLink(token="abc", destination_url="u", source_label="s", resume_id=resume.id)])
    if not db.query(Persona).first():
        db.add(Persona(id=1))
    db.commit()


def _null_counts(db):
    return {t: db.execute(seed.text(f"select count(*) from {t} where account_id is null")).scalar()
            for t in seed.ACCOUNT_TABLES}


def test_every_listed_table_has_the_column_in_the_model(test_db):
    from backend.models.db import Base
    for t in seed.ACCOUNT_TABLES:
        assert "account_id" in Base.metadata.tables[t].c, t


def test_rows_without_an_account_get_the_owner(test_db):
    _seed_one_of_each(test_db)
    assert all(n > 0 for n in _null_counts(test_db).values())

    seed.run_migrations(test_db)

    assert _null_counts(test_db) == {t: 0 for t in seed.ACCOUNT_TABLES}
    owners = test_db.query(Account).filter(Account.role == "owner").all()
    assert len(owners) == 1
    owner_id = get_owner_id(test_db)
    assert owner_id == owners[0].id
    assert test_db.query(Job).first().account_id == owner_id
    assert test_db.query(Persona).first().account_id == owner_id


def test_second_run_is_a_no_op(test_db):
    _seed_one_of_each(test_db)
    seed.run_migrations(test_db)
    first = get_owner_id(test_db)

    seed.run_migrations(test_db)

    assert test_db.query(Account).count() == 1
    assert get_owner_id(test_db) == first
    assert _null_counts(test_db) == {t: 0 for t in seed.ACCOUNT_TABLES}


def test_owner_name_comes_from_the_persona(test_db):
    test_db.add(Persona(id=1, contact={"first_name": "Ada", "last_name": "Lovelace"}))
    test_db.commit()
    seed.run_migrations(test_db)
    assert test_db.query(Account).one().name == "Ada Lovelace"


def test_owner_name_falls_back_to_owner(test_db):
    seed.run_migrations(test_db)
    owner = test_db.query(Account).one()
    assert owner.name == "Owner"
    assert owner.role == "owner"
    assert owner.pin_hash is None


def test_rows_that_already_belong_to_an_account_are_left_alone(test_db):
    other = Account(name="Partner", role="member")
    test_db.add(other)
    test_db.commit()
    mine = Search(name="Theirs", account_id=other.id)
    test_db.add_all([mine, Search(name="Orphan")])
    test_db.commit()

    seed.run_migrations(test_db)

    owner_id = get_owner_id(test_db)
    assert owner_id != other.id
    assert test_db.query(Search).filter(Search.name == "Theirs").one().account_id == other.id
    assert test_db.query(Search).filter(Search.name == "Orphan").one().account_id == owner_id


def test_get_owner_id_is_none_before_the_migration(test_db):
    assert get_owner_id(test_db) is None
