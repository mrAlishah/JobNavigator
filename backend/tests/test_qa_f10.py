"""QA F10 · JobSpy share rule, session reuse across accounts, résumé id validation,
company create race.

R4-05  JobSpy subscribers share one fetch only when their results_wanted are within
       1.5x of each other and their hours_old fall in the same bucket; otherwise the
       target is split into separate fetches (50 vs 500 → two, 50 vs 60 → one at 60).
R1-05  a session reused across an account switch (`use_account` / `acting_as` /
       `for_each_account` with `session=`) drops the other account's rows: no
       identity-map hit, no lazy load from them. The scrape fan-out gives every
       subscriber a fresh session of its own account.
T4-01  companies.selected_resume_ids holds "persona" and the caller's own base résumé
       ids only: 422 `invalid_resume_ids` otherwise; a stale id already on the row is
       dropped on PATCH.
T5-03  a concurrent same-name company create answers 409 `name_taken`, not 500.
"""
import sys
import types
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import object_session
from sqlalchemy.orm.exc import DetachedInstanceError

from backend.accounts.context import current_account, unscoped, use_account
from backend.models.db import Account, Application, Company, Job, Resume, Search
from backend.tests.r4_support import (  # noqa: F401
    ACT_AS, client, assert_clean, make_job,
)


@pytest.fixture
def accounts(test_db):
    a = test_db.query(Account).filter(Account.role == "owner").one()
    b = Account(name="Partner", role="member")
    test_db.add(b)
    test_db.commit()
    return a.id, b.id


@pytest.fixture
def offline(monkeypatch):
    import backend.analyzer.h1b_checker as h1b
    import backend.scraper.sources.company_pages as cp

    async def _no_desc(jobs):
        return []

    async def _no_h1b(job, db=None, company_lookup=None, phrases=None):
        job.h1b_jd_flag = False

    async def _instant(_s):
        return None
    monkeypatch.setattr(cp, "_fetch_descriptions_parallel", _no_desc)
    monkeypatch.setattr(h1b, "check_job_h1b", _no_h1b)
    monkeypatch.setattr(cp.asyncio, "sleep", _instant)


# ── R4-05 · JobSpy share rule ────────────────────────────────────────────────

def _sub(results=None, hours=None, account="a"):
    return SimpleNamespace(search_mode="keyword", results_wanted=results, hours_old=hours,
                           account_id=account)


def _split(*subs):
    from backend.scraper.targets import Target, split_search_targets
    t = Target(key=("keyword", ("indeed",), "pm", "", "usa", "fulltime", None),
               subscribers=list(subs))
    return split_search_targets([t])


def test_rule_50_vs_500_is_two_fetches():
    from backend.scraper.targets import search_fetch_params
    small, big = _sub(50, 24, "a"), _sub(500, 24, "b")
    out = _split(small, big)
    assert [t.subscribers for t in out] == [[small], [big]]
    assert [search_fetch_params(t.subscribers)["results_wanted"] for t in out] == [50, 500]
    assert len({t.key for t in out}) == 2


def test_rule_50_vs_60_is_one_fetch_at_60():
    from backend.scraper.targets import search_fetch_params
    a, b = _sub(50, 24, "a"), _sub(60, 24, "b")
    (t,) = _split(a, b)
    assert t.subscribers == [a, b]
    assert search_fetch_params(t.subscribers) == {"results_wanted": 60, "hours_old": 24}


def test_rule_ratio_boundary_and_defaults():
    # exactly 1.5x shares; just above does not; None means the default 50 / 24 h
    assert len(_split(_sub(50, 24), _sub(75, 24))) == 1
    assert len(_split(_sub(50, 24), _sub(76, 24))) == 2
    assert len(_split(_sub(None, None), _sub(60, 24))) == 1


def test_rule_hours_buckets():
    from backend.scraper.targets import hours_bucket
    assert hours_bucket(12) == hours_bucket(24) == hours_bucket(None) == 0
    assert hours_bucket(25) == hours_bucket(72) == 1
    assert hours_bucket(168) == 2 and hours_bucket(720) == 4 and hours_bucket(1000) == 5
    assert len(_split(_sub(50, 12), _sub(50, 24))) == 1
    assert len(_split(_sub(50, 24), _sub(50, 168))) == 2


def test_rule_groups_keep_load_order_and_cluster_greedily():
    s1, s2, s3, s4 = _sub(100, 24, "a"), _sub(50, 24, "b"), _sub(500, 24, "c"), _sub(70, 24, "d")
    out = _split(s1, s2, s3, s4)
    # 50, 70 (≤75) together; 100 starts a new group; 500 alone
    assert [t.subscribers for t in out] == [[s1], [s2, s4], [s3]]


def test_rule_leaves_other_modes_and_single_targets_alone():
    from backend.scraper.targets import Target, split_search_targets
    fh = Target(key=("freehire", ()), subscribers=[
        SimpleNamespace(search_mode="freehire", results_wanted=50, hours_old=None),
        SimpleNamespace(search_mode="freehire", results_wanted=1000, hours_old=None)])
    one = Target(key=("keyword", "x"), subscribers=[_sub(50, 24)])
    assert split_search_targets([fh, one]) == [fh, one]


def _fake_jobspy(monkeypatch, rows):
    import pandas as pd
    import jobspy.model  # noqa: F401
    calls = []
    mod = types.ModuleType("jobspy")

    def scrape_jobs(**kwargs):
        calls.append(kwargs)
        return pd.DataFrame(rows)
    mod.scrape_jobs = scrape_jobs
    monkeypatch.setitem(sys.modules, "jobspy", mod)
    return calls


def _rows(n):
    day = datetime.now(timezone.utc).date()
    return [{"site": "indeed", "title": f"Product Manager {i}", "company": f"Co{i}",
             "job_url": f"https://indeed.test/{i}", "description": "An ordinary job. " * 10,
             "location": "Remote", "min_amount": None, "max_amount": None,
             "date_posted": day} for i in range(n)]


def _search(db, **kw):
    kw.setdefault("search_mode", "keyword")
    kw.setdefault("sources", ["indeed"])
    kw.setdefault("search_term", "product manager")
    kw.setdefault("title_include_keywords", [])
    kw.setdefault("title_exclude_keywords", [])
    kw.setdefault("company_filter", [])
    kw.setdefault("company_exclude", [])
    kw.setdefault("auto_scoring_depth", "off")
    s = Search(name=kw.pop("name", "PM"), active=True, **kw)
    db.add(s)
    db.commit()
    return s


def _job_counts(db):
    with unscoped():
        db.expire_all()
        out = {}
        for j in db.query(Job).all():
            out[j.account_id] = out.get(j.account_id, 0) + 1
    return out


@pytest.mark.asyncio
async def test_two_accounts_50_vs_500_results_are_two_fetches(test_db, accounts, monkeypatch, offline):
    import backend.scraper.orchestrator as orch
    a, b = accounts
    calls = _fake_jobspy(monkeypatch, _rows(5))
    _search(test_db, results_wanted=50, hours_old=24)
    with use_account(b):
        _search(test_db, results_wanted=500, hours_old=24)
    await orch.run_all(force=True)
    assert sorted(c["results_wanted"] for c in calls) == [50, 500]


@pytest.mark.asyncio
async def test_two_accounts_50_vs_60_results_share_one_fetch_trimmed(test_db, accounts, monkeypatch, offline):
    import backend.scraper.orchestrator as orch
    a, b = accounts
    calls = _fake_jobspy(monkeypatch, _rows(60))
    _search(test_db, results_wanted=50, hours_old=24)
    with use_account(b):
        _search(test_db, results_wanted=60, hours_old=24)
    await orch.run_all(force=True)
    assert len(calls) == 1 and calls[0]["results_wanted"] == 60 and calls[0]["hours_old"] == 24
    assert _job_counts(test_db) == {a: 50, b: 60}


@pytest.mark.asyncio
async def test_different_hours_buckets_are_two_fetches(test_db, accounts, monkeypatch, offline):
    import backend.scraper.orchestrator as orch
    a, b = accounts
    calls = _fake_jobspy(monkeypatch, _rows(3))
    _search(test_db, results_wanted=50, hours_old=24)
    with use_account(b):
        _search(test_db, results_wanted=50, hours_old=720)
    await orch.run_all(force=True)
    assert sorted(c["hours_old"] for c in calls) == [24, 720]


# ── R1-05 · session reused across a switch ───────────────────────────────────

def _a_job_with_application(db, a):
    with use_account(a):
        job = make_job(db, title="A job")
        db.add(Application(job_id=job.id, status="applied"))
        db.commit()
        return job.id


def test_row_loaded_under_a_is_not_returned_under_b_on_the_same_session(test_db, accounts):
    a, b = accounts
    jid = _a_job_with_application(test_db, a)
    with use_account(a):
        loaded = test_db.query(Job).filter(Job.id == jid).one()
        test_db.expire(loaded, ["applications"])
    with use_account(b, session=test_db):
        assert test_db.get(Job, jid) is None, "identity-map hit of A's row under B"
        with pytest.raises(DetachedInstanceError):
            loaded.applications   # no lazy load of A's applications under B
    with use_account(a):
        again = test_db.get(Job, jid)
        assert again is not None and again.applications[0].status == "applied"


def test_without_the_session_the_gap_is_still_there(test_db, accounts):
    """Negative control: plain use_account() does not touch the identity map."""
    a, b = accounts
    jid = _a_job_with_application(test_db, a)
    with use_account(a):
        held = test_db.query(Job).filter(Job.id == jid).one()   # noqa: F841 (identity map is weak)
    with use_account(b):
        assert test_db.get(Job, jid) is held


def test_rows_of_the_inner_account_are_dropped_on_the_way_out(test_db, accounts):
    a, b = accounts
    with use_account(b):
        bjob = make_job(test_db, title="B job")
        bid = bjob.id
    with use_account(b, session=test_db):
        assert test_db.get(Job, bid) is bjob
    with use_account(a, session=test_db):
        pass
    # back in the test's own (owner) context: B's row is no longer in the session
    assert test_db.get(Job, bid) is None


def test_pending_changes_are_flushed_under_their_own_context(test_db, accounts):
    a, b = accounts
    with use_account(a):
        j = Job(external_id="ext-pending", company="Acme", title="Pending",
                url="https://x.com/p", source="test", status="new")
        test_db.add(j)
        with use_account(b, session=test_db):
            pass
        assert j.account_id == a
        test_db.commit()


@pytest.mark.asyncio
async def test_acting_as_and_for_each_account_isolate_a_shared_session(test_db, accounts):
    from backend.accounts.background import acting_as, for_each_account
    a, b = accounts
    jid = _a_job_with_application(test_db, a)
    with unscoped():
        held = test_db.query(Job).filter(Job.id == jid).one()   # loaded across accounts
    with acting_as(b, session=test_db):
        assert test_db.get(Job, jid) is None
    seen = {}

    async def body():
        seen[current_account.get()] = test_db.get(Job, jid) is not None
    with unscoped():
        held = test_db.query(Job).filter(Job.id == jid).one()   # noqa: F841
    await for_each_account("probe", body, account_ids=[b, a], session=test_db)
    assert seen == {b: False, a: True}


@pytest.mark.asyncio
async def test_search_fan_out_gives_each_subscriber_its_own_session(test_db, accounts, monkeypatch, offline):
    import backend.scraper.orchestrator as orch
    a, b = accounts
    _fake_jobspy(monkeypatch, _rows(2))
    _search(test_db, results_wanted=50)
    with use_account(b):
        _search(test_db, results_wanted=50)
    seen = []
    real = orch.run_search

    async def spy(search, **kw):
        seen.append((current_account.get(), search.account_id, object_session(search)))
        return await real(search, **kw)
    monkeypatch.setattr(orch, "run_search", spy)
    await orch.run_all(force=True)
    assert [(c, acc) for c, acc, _ in seen] == [(a, a), (b, b)]
    sessions = [s for _, _, s in seen]
    assert sessions[0] is not None and sessions[0] is not sessions[1]


@pytest.mark.asyncio
async def test_company_fan_out_gives_each_subscriber_its_own_session(test_db, accounts, monkeypatch, offline):
    import backend.scraper.sources.company_pages as cp
    a, b = accounts

    async def fake_dispatch(url, **kw):
        return [{"title": "Product Manager", "url": "https://jobs.lever.co/acme/1"}]
    monkeypatch.setattr(cp, "_dispatch_ats", fake_dispatch)
    test_db.add(Company(name="Acme", scrape_urls=["https://jobs.lever.co/acme"], active=True,
                        auto_scoring_depth="off"))
    test_db.commit()
    with use_account(b):
        test_db.add(Company(name="Acme", scrape_urls=["https://jobs.lever.co/acme"], active=True,
                            auto_scoring_depth="off"))
        test_db.commit()
    seen = []
    real = cp.scrape_single_career_page

    async def spy(company, **kw):
        seen.append((current_account.get(), company.account_id, object_session(company)))
        return await real(company, **kw)
    monkeypatch.setattr(cp, "scrape_single_career_page", spy)
    summary = await cp.scrape_career_pages(force=True)
    assert summary["scraped"] == 2
    assert [(c, acc) for c, acc, _ in seen] == [(a, a), (b, b)]
    assert seen[0][2] is not seen[1][2]


# ── T4-01 · selected_resume_ids validation ───────────────────────────────────

def _resume(db, name, is_base=True):
    r = Resume(name=name, is_base=is_base, json_data={"summary": name})
    db.add(r)
    db.commit()
    return r


def test_create_accepts_own_base_resumes_and_persona(client, test_db):
    mine = _resume(test_db, "Mine")
    r = assert_clean(client.post("/api/companies", json={
        "name": "Own", "selected_resume_ids": [str(mine.id), "persona"]}), 200)
    assert r.json()["selected_resume_ids"] == [str(mine.id), "persona"]


def test_create_rejects_another_accounts_resume(client, test_db, accounts):
    a, b = accounts
    with use_account(b):
        theirs = _resume(test_db, "Theirs")
    r = assert_clean(client.post("/api/companies", json={
        "name": "Foreign", "selected_resume_ids": [str(theirs.id)]}), 422)
    assert r.json()["detail"] == "invalid_resume_ids"
    assert test_db.query(Company).filter(Company.name == "Foreign").first() is None


@pytest.mark.parametrize("bad", ["abc", str(uuid.uuid4()), "tailored"])
def test_create_rejects_unknown_garbage_and_tailored_ids(client, test_db, bad):
    if bad == "tailored":
        bad = str(_resume(test_db, "Tailored", is_base=False).id)
    r = assert_clean(client.post("/api/companies", json={
        "name": "Bad", "selected_resume_ids": [bad]}), 422)
    assert r.json()["detail"] == "invalid_resume_ids"


def test_member_create_rejects_the_owners_resume(client, test_db, accounts):
    a, b = accounts
    owners = _resume(test_db, "Owner CV")
    r = assert_clean(client.post("/api/companies", headers={ACT_AS: str(b)}, json={
        "name": "Member Co", "selected_resume_ids": [str(owners.id)]}), 422)
    assert r.json()["detail"] == "invalid_resume_ids"


def test_patch_rejects_foreign_ids_and_keeps_the_row(client, test_db, accounts):
    a, b = accounts
    mine = _resume(test_db, "Mine")
    with use_account(b):
        theirs = _resume(test_db, "Theirs")
    co = Company(name="Acme", selected_resume_ids=[str(mine.id)])
    test_db.add(co)
    test_db.commit()
    for bad in ([str(theirs.id)], "abc", [1, 2]):
        r = assert_clean(client.patch(f"/api/companies/{co.id}",
                                      json={"selected_resume_ids": bad, "notes": "x"}), 422)
        assert r.json()["detail"] == "invalid_resume_ids"
    test_db.expire_all()
    assert co.selected_resume_ids == [str(mine.id)] and co.notes in (None, "")


def test_patch_drops_a_stale_stored_id_silently(client, test_db):
    mine = _resume(test_db, "Mine")
    gone = str(uuid.uuid4())   # a résumé deleted since it was selected
    co = Company(name="Acme", selected_resume_ids=[gone, str(mine.id)])
    test_db.add(co)
    test_db.commit()
    r = assert_clean(client.patch(f"/api/companies/{co.id}", json={
        "selected_resume_ids": [gone, str(mine.id), "persona"]}), 200)
    assert r.json()["selected_resume_ids"] == [str(mine.id), "persona"]


# ── T5-03 · concurrent same-name create ──────────────────────────────────────

def test_duplicate_name_is_409_name_taken(client):
    assert_clean(client.post("/api/companies", json={"name": "Acme"}), 200)
    r = assert_clean(client.post("/api/companies", json={"name": "ACME"}), 409)
    assert r.json()["detail"] == "name_taken" and "ACME" in r.json()["message"]


def test_concurrent_same_name_create_is_409_not_500(client, test_db, monkeypatch):
    """The second request passed the name check before the first one committed: the
    unique constraint catches it, and the answer is 409 `name_taken`."""
    import backend.api.routes_companies as rc
    import backend.models.db as db_mod
    real = rc._checked_resume_ids

    def racing(db, value, stored=()):
        other = db_mod.SessionLocal()   # the other tab's request commits first
        try:
            other.add(Company(name="Race Co"))
            other.commit()
        finally:
            other.close()
        return real(db, value, stored)
    monkeypatch.setattr(rc, "_checked_resume_ids", racing)
    r = assert_clean(client.post("/api/companies", json={"name": "Race Co"}), 409)
    assert r.json()["detail"] == "name_taken"
    monkeypatch.setattr(rc, "_checked_resume_ids", real)
    with unscoped():
        test_db.expire_all()
        assert test_db.query(Company).filter(Company.name == "Race Co").count() == 1
    # the session is usable afterwards
    assert_clean(client.post("/api/companies", json={"name": "After Race"}), 200)
