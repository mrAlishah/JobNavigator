"""link_kinds.py: whether a job's link reaches the employer's own site (direct-links plan)."""
import pytest

from backend.models.db import Job
from backend.scraper._shared.link_kinds import classify, link_kind


def test_an_employer_posting_is_direct():
    assert link_kind("https://job-boards.greenhouse.io/x/jobs/1234567", None) == "direct"


def test_a_company_site_without_a_known_system_is_direct():
    assert link_kind("https://cursor.com/careers/gtm-systems", None) == "direct"


@pytest.mark.parametrize("url", [
    "https://www.linkedin.com/jobs/view/4461186967",
    "https://www.indeed.com/viewjob?jk=0362e90102d8ac2c",
    "https://www.glassdoor.com/job-listing/x",
    "https://www.ziprecruiter.com/kn/AAJVrD7cHvDc1iynP",
    "https://de-ch.whatjobs.com/pub_api__cpl__1__7095",
    "https://t.me/huntmejob/38746",
    "https://www.levels.fyi/jobs?jobId=2ci7r6weraiqb",
    "https://jobright.ai/jobs/info/abc",
    "https://www.hackajob.com/job/x",
    "https://haystack.cv/jobs/x",
    "https://app.swooped.co/job-postings/x",
])
def test_board_listings_are_board(url):
    assert link_kind(url, None) == "board"


def test_a_board_row_with_an_employer_link_is_direct():
    assert link_kind("https://www.indeed.com/viewjob?jk=0362e90102d8ac2c",
                     "https://boards.greenhouse.io/acme/jobs/4012345") == "direct"


@pytest.mark.parametrize("url", ["https://grnh.se/zvej33q44us", "https://dsp.prng.co/w-ox7b",
                                 "https://click.appcast.io/t/x"])
def test_short_links_to_the_employer_are_direct(url):
    assert link_kind(url, None) == "direct"


def test_no_link_is_board():
    assert link_kind(None, None) == "board"


def test_classify_keeps_easy_apply():
    job = Job(url="https://www.linkedin.com/jobs/view/4461186967", link_kind="easy_apply")
    classify(job)
    assert job.link_kind == "easy_apply"
    job.apply_url = "https://boards.greenhouse.io/acme/jobs/4012345"
    classify(job)
    assert job.link_kind == "direct"


def _three_kinds(db):
    from backend.tests.test_posting_links import _job
    from backend.models.db import Setting
    db.add(Setting(key="dashboard_api_key", value=""))
    direct = _job(db, "https://job-boards.greenhouse.io/acme/jobs/4012345", company="Acme", title="A")
    board = _job(db, "https://www.linkedin.com/jobs/view/4461186967", company="Acme", title="B")
    easy = _job(db, "https://www.linkedin.com/jobs/view/4461186968", company="Acme", title="C")
    easy.link_kind = "easy_apply"
    db.commit()
    return direct, board, easy


def test_the_feed_filters_by_link_kind(test_db, api_client):
    direct, board, easy = _three_kinds(test_db)
    one = api_client.get("/api/jobs?status=new&link_kind=direct").json()["jobs"]
    assert [j["id"] for j in one] == [str(direct.id)] and one[0]["link_kind"] == "direct"
    two = api_client.get("/api/jobs?status=new&link_kind=board,easy_apply").json()["jobs"]
    assert {j["id"] for j in two} == {str(board.id), str(easy.id)}


def test_facets_count_link_kinds(test_db, api_client):
    _three_kinds(test_db)
    body = api_client.get("/api/jobs/facets?status=new&link_kind=direct").json()
    counts = {x["name"]: x["count"] for x in body["link_kinds"]}
    assert counts == {"direct": 1, "board": 1, "easy_apply": 1}, "the own filter is lifted"


def test_job_link_prefers_the_employer_link():
    from backend.scraper._shared.link_kinds import job_link
    gh = "https://boards.greenhouse.io/acme/jobs/4012345"
    assert job_link(Job(url="https://www.indeed.com/viewjob?jk=0362e90102d8ac2c", apply_url=gh)) == gh
    assert job_link(Job(url="https://www.indeed.com/viewjob?jk=0362e90102d8ac2c")) == \
        "https://www.indeed.com/viewjob?jk=0362e90102d8ac2c"


def test_the_api_gives_the_link_to_open(test_db, api_client):
    from backend.tests.test_posting_links import _job
    from backend.models.db import Setting
    test_db.add(Setting(key="dashboard_api_key", value=""))
    gh = "https://boards.greenhouse.io/acme/jobs/4012345"
    job = _job(test_db, "https://www.indeed.com/viewjob?jk=0362e90102d8ac2c", apply_url=gh, source="jobspy_indeed")
    body = api_client.get(f"/api/jobs/{job.id}").json()
    assert body["link"] == gh and body["url"] == "https://www.indeed.com/viewjob?jk=0362e90102d8ac2c"


def test_job_link_takes_a_stand_in_without_apply_url():
    """Description fetches pass a SimpleNamespace(id, description, url, cached_page_text)."""
    from types import SimpleNamespace
    from backend.scraper._shared.link_kinds import job_link
    assert job_link(SimpleNamespace(url="https://acme.com/jobs/9")) == "https://acme.com/jobs/9"
    assert job_link(None) is None


def test_searches_report_their_direct_rate(test_db, api_client):
    from backend.tests.test_posting_links import _job
    from backend.models.db import Search, Setting
    test_db.add(Setting(key="dashboard_api_key", value=""))
    s = Search(name="PM", search_mode="levels_fyi", sources=[])
    quiet = Search(name="Quiet", search_mode="levels_fyi", sources=[])
    test_db.add_all([s, quiet])
    test_db.commit()
    easy = _job(test_db, "https://www.linkedin.com/jobs/view/4461186969", company="Acme", title="easy")
    easy.link_kind, easy.search_id = "easy_apply", s.id   # Easy Apply counts as direct
    for url, status in (("https://job-boards.greenhouse.io/a/jobs/1000001", "new"),
                        ("https://jobs.lever.co/a/1cafb489-9cae-4417-b272-832617484de0", "skip"),
                        ("https://www.linkedin.com/jobs/view/4461186967", "new"),
                        ("https://www.linkedin.com/jobs/view/4461186968", "ignored")):
        j = _job(test_db, url, company="Acme", title=url[-7:], status=status)
        j.search_id = s.id
    test_db.commit()
    rows = {r["name"]: r for r in api_client.get("/api/searches").json()}
    assert rows["PM"]["direct_rate"] == {"direct": 3, "total": 4}
    assert rows["Quiet"]["direct_rate"] is None


def test_a_linkedin_short_link_is_a_board_link():
    """Review #4: lnkd.in lands on LinkedIn."""
    assert link_kind("https://lnkd.in/abc", None) == "board"


def test_an_employer_link_beside_a_board_listing_is_direct_even_without_a_known_system():
    assert link_kind("https://www.linkedin.com/jobs/view/4461186967", "https://cursor.com/careers/gtm") == "direct"
