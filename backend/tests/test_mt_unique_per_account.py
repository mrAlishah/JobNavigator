"""MT-4 · Per-account uniqueness and dedup for jobs, companies and searches.

`companies.name` and `jobs.external_id` were unique install-wide; with account scoping a
lookup in one account cannot see the other's row, so an install-wide constraint turned a
missed (correct) dedup into an IntegrityError. They are UNIQUE(account_id, ...) now.
"""
import pytest
from sqlalchemy.exc import IntegrityError

from backend.accounts.context import use_account
from backend.models.db import (
    Account, Company, Job, build_company_lookup, find_company_by_name, get_existing_external_ids,
)
from backend.scraper._shared.dedup import make_content_hash, make_external_id
from backend.tests.r4_support import client, make_company, make_job  # noqa: F401 (client is a fixture)

URL = "https://boards.example/acme/jobs/42"


@pytest.fixture
def accounts(test_db):
    a = test_db.query(Account).filter(Account.role == "owner").one()
    b = Account(name="Partner", role="member")
    test_db.add(b)
    test_db.commit()
    return a.id, b.id


def test_two_accounts_can_each_hold_a_company_with_the_same_name(test_db, accounts):
    a, b = accounts
    with use_account(a):
        make_company(test_db, name="Acme")
    with use_account(b):
        make_company(test_db, name="Acme", aliases=["Acme Corp"])
    test_db.expunge_all()
    with use_account(a):
        assert test_db.query(Company).filter(Company.name == "Acme").count() == 1
        assert find_company_by_name(test_db, "acme corp") is None          # B's alias
        assert set(build_company_lookup(test_db)) == {"acme"}
    with use_account(b):
        assert find_company_by_name(test_db, "acme corp").name == "Acme"


def test_company_name_is_still_unique_within_one_account(test_db, accounts):
    a, _ = accounts
    with use_account(a):
        make_company(test_db, name="Acme")
        test_db.add(Company(name="Acme"))
        with pytest.raises(IntegrityError):
            test_db.commit()
        test_db.rollback()


def test_two_accounts_can_each_hold_a_job_with_the_same_external_id(test_db, accounts):
    a, b = accounts
    ext = make_external_id("Acme", "PM", URL)
    with use_account(a):
        make_job(test_db, company="Acme", title="PM", url=URL)
    with use_account(b):
        make_job(test_db, company="Acme", title="PM", url=URL)
    test_db.expunge_all()
    for acc in (a, b):
        with use_account(acc):
            assert test_db.query(Job).filter(Job.external_id == ext).count() == 1
            assert get_existing_external_ids(test_db) == {ext}


def test_external_id_is_still_unique_within_one_account(test_db, accounts):
    a, _ = accounts
    with use_account(a):
        make_job(test_db, company="Acme", title="PM", url=URL)
        test_db.add(Job(external_id=make_external_id("Acme", "PM", URL), company="Acme", title="PM"))
        with pytest.raises(IntegrityError):
            test_db.commit()
        test_db.rollback()


def test_dedup_set_loaders_read_within_the_account(test_db, accounts):
    a, b = accounts
    with use_account(b):
        make_job(test_db, company="Beta", title="Only B", url="https://b.example/1")
        make_company(test_db, name="Beta")
    test_db.expunge_all()
    with use_account(a):
        assert get_existing_external_ids(test_db) == set()
        assert build_company_lookup(test_db) == {}
        assert find_company_by_name(test_db, "Beta") is None


def _save(client, title="PM", company="Acme", url=URL):
    return client.post("/api/jobs/save-from-extension",
                       json={"title": title, "company": company, "url": url})


def test_saving_a_posting_another_account_holds_creates_our_own_row(client, test_db, accounts):
    """The request runs as A (the owner); B already has the same posting."""
    a, b = accounts
    with use_account(b):
        make_job(test_db, company="Acme", title="PM", url=URL,
                 content_hash=make_content_hash("Acme", "PM"))
    test_db.expunge_all()

    r = _save(client)
    assert r.status_code == 200, r.text
    assert r.json()["new"] is True
    # Dedup inside A still works: the second save finds A's row.
    r2 = _save(client)
    assert r2.status_code == 200 and r2.json()["new"] is False
    assert r2.json()["id"] == r.json()["id"]
    for acc in (a, b):
        with use_account(acc):
            assert test_db.query(Job).count() == 1


def test_creating_a_company_another_account_holds_is_allowed(client, test_db, accounts):
    a, b = accounts
    with use_account(b):
        make_company(test_db, name="Acme")
    test_db.expunge_all()
    r = client.post("/api/companies", json={"name": "Acme"})
    assert r.status_code < 300, r.text
    # ...but a second one in A is still a conflict.
    assert client.post("/api/companies", json={"name": "acme"}).status_code == 409
    with use_account(a):
        assert test_db.query(Company).count() == 1


def test_search_test_results_are_returned_only_to_their_account(client, accounts):
    from backend.api import routes_searches as rs
    a, b = accounts
    rs._test_results["mine0001"] = {"status": "done", "result": {"jobs": []}, "account": str(a)}
    rs._test_results["theirs01"] = {"status": "done", "result": {"jobs": []}, "account": str(b)}
    try:
        assert client.get("/api/searches/test-result/mine0001").json() == {"jobs": []}
        assert client.get("/api/searches/test-result/theirs01").status_code == 404
    finally:
        rs._test_results.pop("mine0001", None)
        rs._test_results.pop("theirs01", None)


def test_linkedin_import_progress_of_another_account_reads_idle(client, accounts):
    # R5-02: progress is kept per account (QA F5)
    from backend.scraper.sources.linkedin_extension import _linkedin_import_progress
    a, b = accounts
    _linkedin_import_progress.reset_all()
    try:
        with use_account(b):
            _linkedin_import_progress.update({"status": "running", "total": 3})
        assert client.get("/api/jobs/linkedin-import/progress").json() == {"status": "idle"}
        with use_account(a):
            _linkedin_import_progress.update({"status": "running", "total": 5})
        assert client.get("/api/jobs/linkedin-import/progress").json() == {"status": "running", "total": 5}
        with use_account(b):
            assert dict(_linkedin_import_progress) == {"status": "running", "total": 3}
    finally:
        _linkedin_import_progress.reset_all()


def test_unscored_counters_count_only_the_account(client, test_db, accounts):
    a, b = accounts
    with use_account(a):
        make_job(test_db, title="A new", status="new", cv_scores={})
        make_job(test_db, title="A scored", status="new", cv_scores={"Base": 70})
    with use_account(b):
        make_job(test_db, title="B new", status="new", cv_scores={})
        make_job(test_db, title="B saved", status="saved", cv_scores={})
    test_db.expunge_all()
    assert client.get("/api/jobs/feed-stats").json()["unscored"] == 1
    ids = client.get("/api/jobs/unscored-ids").json()["ids"]
    with use_account(a):
        a_new = test_db.query(Job).filter(Job.title == "A new").one()
    assert ids == [str(a_new.id)]
