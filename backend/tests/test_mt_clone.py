"""MT-20 · Clone companies/searches across accounts, LLM cost for all accounts.

`client` (r4_support) acts as the owner via X-API-Key + X-Account-Token (r4_support.ACT_AS); a member call swaps the header.
"""
import uuid
from datetime import datetime, timezone

import pytest

from backend.accounts.context import unscoped
from backend.models.db import Account, Company, Job, LlmCallLog, Search
from backend.tests.r4_support import _clear_running_state, _no_outbound, client  # noqa: F401 (fixtures)


@pytest.fixture
def owner(test_db):
    return test_db.query(Account).filter(Account.role == "owner").one()


def _member(db, name="Partner"):
    m = Account(name=name, role="member", color="#2e9e6b")
    db.add(m)
    db.commit()
    return m


def _as(c, account_id):
    c.cookies.clear()
    c.headers["X-Test-Act-As"] = str(account_id)
    return c


def _add(db, klass, account_id, **kw):
    with unscoped():
        row = klass(account_id=account_id, **kw)
        db.add(row)
        db.commit()
        return row


def _company(db, account, name="Acme", **kw):
    base = dict(name=name, tier=1, scrape_urls=["https://boards.greenhouse.io/acme"],
                selected_resume_ids=["persona", str(uuid.uuid4())], notes="private note",
                last_scraped_at=datetime.now(timezone.utc), title_include_expr="engineer")
    base.update(kw)
    return _add(db, Company, account, **base)


def _search(db, account, name="Backend roles", **kw):
    base = dict(name=name, search_mode="keyword", search_term="python", location="Remote",
                last_run_at=datetime.now(timezone.utc))
    base.update(kw)
    return _add(db, Search, account, **base)


# ── gate: at least two accounts ──────────────────────────────────────────────

def test_one_account_has_no_sources_and_cannot_clone(client, test_db, owner):
    _company(test_db, owner.id)
    assert client.get("/api/companies/clone-sources").json() == {"accounts": []}
    assert client.get("/api/searches/clone-sources").json() == {"accounts": []}
    r = client.post("/api/companies/clone", json={"source_id": str(uuid.uuid4())})
    assert (r.status_code, r.json()["detail"]) == (409, "needs_two_accounts")
    r = client.post("/api/searches/clone", json={"source_id": str(uuid.uuid4())})
    assert (r.status_code, r.json()["detail"]) == (409, "needs_two_accounts")


# ── sources visibility ───────────────────────────────────────────────────────

def test_sources_list_other_accounts_only_and_no_personal_fields(client, test_db, owner):
    m = _member(test_db)
    mine = _company(test_db, owner.id, "Mine Co")
    theirs = _company(test_db, m.id, "Theirs Co", notes="SECRET-NOTE")
    _search(test_db, owner.id, "My search")
    _search(test_db, m.id, "Their search")
    r = client.get("/api/companies/clone-sources")
    assert r.status_code == 200
    accounts = r.json()["accounts"]
    assert [a["name"] for a in accounts] == ["Partner"]
    items = accounts[0]["items"]
    assert [i["name"] for i in items] == ["Theirs Co"] and items[0]["id"] == str(theirs.id)
    assert str(mine.id) not in r.text and "SECRET-NOTE" not in r.text and "notes" not in items[0]
    assert set(items[0]) == {"id", "name", "tier", "active", "scrape_urls", "exists"}

    r = client.get("/api/searches/clone-sources").json()["accounts"]
    assert [i["name"] for i in r[0]["items"]] == ["Their search"]   # not "My search", no built-ins


def test_search_sources_skip_builtin_extension_searches(client, test_db, owner):
    m = _member(test_db)
    from backend.seed import ensure_extension_searches
    ensure_extension_searches(test_db, m.id)
    test_db.commit()
    _search(test_db, m.id, "Real one")
    items = client.get("/api/searches/clone-sources").json()["accounts"][0]["items"]
    assert [i["name"] for i in items] == ["Real one"]


def test_sources_flag_names_the_caller_already_has(client, test_db, owner):
    m = _member(test_db)
    _company(test_db, owner.id, "Acme")
    _company(test_db, m.id, "ACME")
    _company(test_db, m.id, "Other")
    items = client.get("/api/companies/clone-sources").json()["accounts"][0]["items"]
    assert {i["name"]: i["exists"] for i in items} == {"ACME": True, "Other": False}


# ── clone result ─────────────────────────────────────────────────────────────

def test_company_clone_result(client, test_db, owner):
    m = _member(test_db)
    src = _company(test_db, m.id, "Theirs Co")
    r = client.post("/api/companies/clone", json={"source_id": str(src.id)})
    assert r.status_code == 200
    body = r.json()
    assert body["id"] != str(src.id) and body["name"] == "Theirs Co"
    assert body["selected_resume_ids"] == []            # résumé ids dropped
    assert body["last_scraped_at"] is None               # due now
    assert body["notes"] is None                         # personal note not copied
    assert body["scrape_urls"] == ["https://boards.greenhouse.io/acme"] and body["title_include_expr"] == "engineer"
    with unscoped():
        row = test_db.query(Company).filter(Company.id == uuid.UUID(body["id"])).one()
        assert row.account_id == owner.id
        assert test_db.query(Company).filter(Company.id == src.id).one().account_id == m.id
        assert test_db.query(Job).count() == 0           # no jobs copied
    # it is in the caller's own list now and gone from the "exists = False" state
    assert [c["name"] for c in client.get("/api/companies").json()] == ["Theirs Co"]


def test_search_clone_result(client, test_db, owner):
    m = _member(test_db)
    src = _search(test_db, m.id, "Their search")
    r = client.post("/api/searches/clone", json={"source_id": str(src.id)})
    assert r.status_code == 200
    body = r.json()
    assert body["id"] != str(src.id) and body["name"] == "Their search"
    assert body["last_run_at"] is None and body["search_term"] == "python"
    with unscoped():
        assert test_db.query(Search).filter(Search.id == uuid.UUID(body["id"])).one().account_id == owner.id


# ── name clash ───────────────────────────────────────────────────────────────

def test_company_name_clash_is_409(client, test_db, owner):
    m = _member(test_db)
    _company(test_db, owner.id, "Acme")
    src = _company(test_db, m.id, "acme")
    r = client.post("/api/companies/clone", json={"source_id": str(src.id)})
    assert (r.status_code, r.json()["detail"]) == (409, "name_taken")
    assert len(client.get("/api/companies").json()) == 1


def test_search_name_clash_gets_copy_suffix(client, test_db, owner):
    m = _member(test_db)
    _search(test_db, owner.id, "Backend roles")
    src = _search(test_db, m.id, "Backend roles")
    names = [client.post("/api/searches/clone", json={"source_id": str(src.id)}).json()["name"] for _ in range(3)]
    assert names == ["Backend roles (copy)", "Backend roles (copy 2)", "Backend roles (copy 3)"]


# ── who can clone what ───────────────────────────────────────────────────────

def test_member_can_clone_from_owner_and_owner_from_member(client, test_db, owner):
    m = _member(test_db)
    oc = _company(test_db, owner.id, "Owner Co")
    mc = _company(test_db, m.id, "Member Co")
    _as(client, m.id)
    assert client.post("/api/companies/clone", json={"source_id": str(oc.id)}).status_code == 200
    assert [c["name"] for c in client.get("/api/companies").json()] == ["Member Co", "Owner Co"]
    _as(client, owner.id)
    assert client.post("/api/companies/clone", json={"source_id": str(mc.id)}).status_code == 200
    assert sorted(c["name"] for c in client.get("/api/companies").json()) == ["Member Co", "Owner Co"]


def test_cannot_clone_own_or_unknown_or_builtin(client, test_db, owner):
    m = _member(test_db)
    own = _company(test_db, owner.id, "Mine")
    for sid in (str(own.id), str(uuid.uuid4()), "nope", None):
        r = client.post("/api/companies/clone", json={"source_id": sid})
        assert (r.status_code, r.json()["detail"]) == (404, "source_not_found"), sid
    from backend.seed import ensure_extension_searches
    ensure_extension_searches(test_db, m.id)
    test_db.commit()
    with unscoped():
        builtin = test_db.query(Search).filter(Search.account_id == m.id).first()
    r = client.post("/api/searches/clone", json={"source_id": str(builtin.id)})
    assert (r.status_code, r.json()["detail"]) == (409, "builtin_search")


# ── LLM cost, all accounts ───────────────────────────────────────────────────

def _llm(db, account, cost, purpose="scoring", model="m1"):
    return _add(db, LlmCallLog, account, purpose=purpose, provider="claude", model=model, cost_usd=cost,
                created_at=datetime.now(timezone.utc))


def test_llm_costs_default_is_the_callers_own(client, test_db, owner):
    m = _member(test_db)
    _llm(test_db, owner.id, 1.0)
    _llm(test_db, m.id, 5.0)
    body = client.get("/api/stats/llm-costs?days=7").json()
    assert body["total_calls"] == 1 and body["total_cost_usd"] == 1.0 and "by_account" not in body


def test_llm_costs_all_accounts_for_owner(client, test_db, owner):
    m = _member(test_db, "Partner")
    _llm(test_db, owner.id, 1.0)
    _llm(test_db, owner.id, 2.0, purpose="tailor")
    _llm(test_db, m.id, 5.0)
    body = client.get("/api/stats/llm-costs?days=7&all_accounts=1").json()
    assert body["total_calls"] == 3 and body["total_cost_usd"] == 8.0
    assert {p["purpose"]: p["calls"] for p in body["by_purpose"]} == {"scoring": 2, "tailor": 1}
    assert [(a["name"], a["calls"], a["cost_usd"]) for a in body["by_account"]] == [
        ("Partner", 1, 5.0), (owner.name, 2, 3.0)]


def test_llm_costs_all_accounts_is_403_for_a_member(client, test_db, owner):
    m = _member(test_db)
    _llm(test_db, owner.id, 1.0)
    _as(client, m.id)
    r = client.get("/api/stats/llm-costs?all_accounts=1")
    assert (r.status_code, r.json()["detail"]) == (403, "owner_only")
    assert client.get("/api/stats/llm-costs").status_code == 200   # own view still fine


def test_stats_endpoints_stay_per_account(client, test_db, owner):
    """Each Stats endpoint reads through the scoped session: B's rows never show in A's."""
    m = _member(test_db)
    _add(test_db, Job, m.id, external_id="x1", title="T", company="C", url="http://x/1", status="applied",
         discovered_at=datetime.now(timezone.utc), cv_scores={"R": 90}, best_cv_score=90)
    assert client.get("/api/stats").json()["total_jobs"] == 0
    assert client.get("/api/stats/timeline?days=7").json() == []
    assert client.get("/api/stats/score-distribution?detail=true").json()["scored_count"] == 0
    assert client.get("/api/stats/sankey").json() == []
    _as(client, m.id)
    assert client.get("/api/stats").json()["total_jobs"] == 1
