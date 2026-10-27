"""Feed scope filters: ?search_id= (Saved search) and ?company= through the tracked company's
aliases. The Feed's two new menus only send these existing params, so these tests pin what they rely on."""
import pytest

from backend.accounts.context import use_account
from backend.models.db import Account, Company, Job, Search
from backend.tests.r4_support import _clear_running_state, _no_outbound, client  # noqa: F401 (fixtures)


@pytest.fixture
def owner(test_db):
    return test_db.query(Account).filter(Account.role == "owner").one()


def _job(ext, company, status="new", search=None, title="PM"):
    return Job(external_id=ext, content_hash="h" + ext, company=company, title=title,
               url=f"https://x.test/{ext}", status=status, search_id=search.id if search else None)


@pytest.fixture
def world(test_db, owner):
    """Two searches, one tracked company with aliases, one tracked company with no jobs."""
    with use_account(owner.id):
        pm, dev = Search(name="PM"), Search(name="Dev", active=False)
        test_db.add_all([pm, dev])
        test_db.add_all([Company(name="Amazon", aliases=["Audible", "AWS"]),
                         Company(name="Idle Inc", active=False)])
        test_db.commit()
        test_db.add_all([
            _job("1", "Amazon", "new", pm), _job("2", "Audible", "saved", pm),
            _job("3", "Acme", "new", dev), _job("4", "Acme", "new", None),   # no search: its search was deleted
            _job("5", "Acme", "new", pm),
        ])
        test_db.commit()
        return {"pm": str(pm.id), "dev": str(dev.id)}


def _ext(resp):
    assert resp.status_code == 200
    return sorted(j["external_id"] for j in resp.json()["jobs"])


def test_search_id_scopes_the_list_inactive_searches_too(client, world):
    assert _ext(client.get(f"/api/jobs?status=new,saved&search_id={world['pm']}")) == ["1", "2", "5"]
    assert _ext(client.get(f"/api/jobs?status=new,saved&search_id={world['dev']}")) == ["3"]


def test_search_combines_with_company_and_status_by_and(client, world):
    assert _ext(client.get(f"/api/jobs?search_id={world['pm']}&company=Acme")) == ["5"]
    assert _ext(client.get(f"/api/jobs?search_id={world['pm']}&status=saved")) == ["2"]
    assert _ext(client.get(f"/api/jobs?search_id={world['dev']}&company=Amazon")) == []


def test_tracked_company_matches_aliases_case_insensitively_and_multiselect_is_or(client, world):
    assert _ext(client.get("/api/jobs?status=new,saved&company=amazon")) == ["1", "2"]
    assert _ext(client.get("/api/jobs?status=new,saved&company=amazon,acme")) == ["1", "2", "3", "4", "5"]
    assert _ext(client.get(f"/api/jobs?status=new,saved&company=Amazon&search_id={world['pm']}")) == ["1", "2"]


def test_pagination_stays_inside_the_scope(client, world):
    seen, total = [], None
    for off in (0, 2):
        body = client.get(f"/api/jobs?status=new,saved&search_id={world['pm']}&limit=2&offset={off}").json()
        seen += [j["external_id"] for j in body["jobs"]]
        total = body["total"]
    assert total == 3 and sorted(seen) == ["1", "2", "5"]


def test_facets_follow_the_search_and_a_tracked_company_without_jobs_stays_at_zero(client, world):
    body = client.get(f"/api/jobs/facets?status=new,saved&search_id={world['pm']}&company=Idle Inc").json()
    counts = {c["name"]: c["count"] for c in body["companies"]}
    assert counts.get("Idle Inc") == 0                        # selectable, shows no results
    assert counts["Acme"] == 1 and counts["Amazon"] == 2     # the Audible job is counted under its parent
    assert _ext(client.get(f"/api/jobs?search_id={world['pm']}&company=Idle Inc")) == []


def test_a_deleted_or_unknown_search_matches_nothing_and_keeps_its_jobs(client, test_db, owner, world):
    assert client.delete(f"/api/searches/{world['dev']}").status_code in (200, 204)
    assert _ext(client.get(f"/api/jobs?status=new,saved&search_id={world['dev']}")) == []
    assert "3" in _ext(client.get("/api/jobs?status=new,saved&company=Acme"))   # job 3 is now search-less, still in the feed
    assert _ext(client.get("/api/jobs?search_id=00000000-0000-0000-0000-000000000000")) == []


def test_another_account_sees_neither_the_searches_nor_the_jobs(client, test_db, world):
    member = Account(name="Partner", role="member", color="#2e9e6b")
    test_db.add(member)
    test_db.commit()
    client.cookies.clear()
    client.headers["X-Test-Act-As"] = str(member.id)
    assert _ext(client.get(f"/api/jobs?status=new,saved&search_id={world['pm']}")) == []
    assert client.get("/api/searches").json() == []
    assert client.get("/api/companies").json() == []
