"""still_open.py: is a job's posting still open, checked where it matters (Task 9)."""
from datetime import datetime, timedelta, timezone

import pytest

from backend.scraper._shared import still_open as so
from backend.tests.test_posting_links import _job


class Fake:
    """fetch(url) -> (status, final url, text) | None, recording the urls asked for."""

    def __init__(self, answers):
        self.answers, self.calls = answers, []

    async def __call__(self, url):
        self.calls.append(url)
        for prefix, answer in self.answers.items():
            if url.startswith(prefix):
                return answer
        return None


async def check(url, answers):
    fake = Fake(answers)
    return await so.check_link(url, fetch=fake), fake.calls


@pytest.mark.asyncio
async def test_greenhouse_asks_its_api():
    url = "https://job-boards.greenhouse.io/acme/jobs/4012345"
    api = "https://boards-api.greenhouse.io/v1/boards/acme/jobs/4012345"
    (state, reason), calls = await check(url, {api: (404, api, "")})
    assert state == "closed" and "Greenhouse" in reason and calls == [api]
    (state, _), _ = await check(url, {api: (200, api, "{}")})
    assert state == "open"


@pytest.mark.asyncio
async def test_lever_asks_its_api():
    url = "https://jobs.lever.co/acme/1cafb489-9cae-4417-b272-832617484de0"
    api = "https://api.lever.co/v0/postings/acme/1cafb489-9cae-4417-b272-832617484de0"
    (state, _), calls = await check(url, {api: (404, api, "")})
    assert state == "closed" and calls == [api]


@pytest.mark.asyncio
async def test_workday_asks_its_job_api():
    url = "https://wf.wd1.myworkdayjobs.com/en-US/WellsFargoJobs/job/CHARLOTTE-NC/Lead-Product-Manager_R-505105"
    api = "https://wf.wd1.myworkdayjobs.com/wday/cxs/wf/WellsFargoJobs/job/CHARLOTTE-NC/Lead-Product-Manager_R-505105"
    (state, _), calls = await check(url, {api: (404, api, "")})
    assert state == "closed" and calls == [api]
    (state, _), _ = await check(url, {api: (200, api, '{"jobPostingInfo": {"title": "x"}}')})
    assert state == "open"


@pytest.mark.asyncio
async def test_linkedin_reads_its_public_job_page():
    url = "https://www.linkedin.com/jobs/view/4461186967"
    api = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/4461186967"
    (state, reason), _ = await check(url, {api: (200, api, "<p>No longer accepting applications</p>")})
    assert state == "closed" and "No longer accepting applications" in reason
    (state, _), _ = await check(url, {api: (200, api, "<h2>Product Manager</h2>")})
    assert state == "open"
    (state, _), _ = await check(url, {api: (429, api, "")})
    assert state == "unknown", "throttled is not closed"


@pytest.mark.asyncio
async def test_a_page_that_is_gone_is_closed():
    url = "https://www.amazon.jobs/en/jobs/10566262/sr-pm"
    (state, reason), _ = await check(url, {url: (404, url, "")})
    assert state == "closed" and "404" in reason


@pytest.mark.asyncio
async def test_a_redirect_off_the_posting_is_closed():
    url = "https://www.amazon.jobs/en/jobs/10566262/sr-pm"
    (state, reason), _ = await check(url, {url: (200, "https://www.amazon.jobs/en/search?error=1", "<html>Search jobs</html>")})
    assert state == "closed" and "amazon.jobs/en/search" in reason


@pytest.mark.asyncio
async def test_a_redirect_to_the_same_posting_is_open():
    url = "https://www.amazon.jobs/de/jobs/10566262/sr-pm"
    (state, _), _ = await check(url, {url: (200, "https://www.amazon.jobs/en/jobs/10566262/sr-pm", "<h1>Sr PM</h1>")})
    assert state == "open"


@pytest.mark.asyncio
async def test_a_closed_phrase_on_the_page_is_closed():
    url = "https://cursor.com/careers/gtm"
    (state, reason), _ = await check(url, {url: (200, url, "<div>Sorry, this job is no longer available.</div>")})
    assert state == "closed" and "no longer available" in reason


@pytest.mark.asyncio
async def test_a_bot_wall_or_an_error_is_unknown():
    url = "https://www.indeed.com/viewjob?jk=0362e90102d8ac2c"
    wall = "<html><title>Just a moment...</title><div id='cf-challenge'>Checking your browser</div></html>"
    (state, _), _ = await check(url, {url: (403, url, wall)})
    assert state == "unknown"
    (state, _), _ = await check(url, {})
    assert state == "unknown", "no answer is not closed"


@pytest.mark.asyncio
async def test_an_ordinary_open_page_is_open():
    url = "https://cursor.com/careers/gtm"
    (state, _), _ = await check(url, {url: (200, url, "<h1>GTM Systems</h1><p>Apply now</p>")})
    assert state == "open"


@pytest.mark.asyncio
async def test_a_recent_answer_is_kept_for_a_day(test_db, monkeypatch):
    job = _job(test_db, "https://job-boards.greenhouse.io/acme/jobs/4012345", company="Acme", title="PM")
    calls = []

    async def fake_check(url, fetch=None):
        calls.append(url)
        return "closed", "Greenhouse: the posting is gone"
    monkeypatch.setattr(so, "check_link", fake_check)
    out = await so.ensure_checked(test_db, job)
    assert out["open_state"] == "closed" and out["open_reason"] == "Greenhouse: the posting is gone"
    await so.ensure_checked(test_db, job)
    assert len(calls) == 1, "fresh for 24 h"
    job.open_checked_at = datetime.now(timezone.utc) - timedelta(hours=25)
    test_db.commit()
    await so.ensure_checked(test_db, job)
    await so.ensure_checked(test_db, job, force=True)
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_the_employer_link_is_checked_when_there_is_one(test_db, monkeypatch):
    job = _job(test_db, "https://www.indeed.com/viewjob?jk=0362e90102d8ac2c",
               apply_url="https://job-boards.greenhouse.io/acme/jobs/4012345", source="jobspy_indeed")
    seen = []

    async def fake_check(url, fetch=None):
        seen.append(url)
        return "open", ""
    monkeypatch.setattr(so, "check_link", fake_check)
    await so.ensure_checked(test_db, job)
    assert seen == ["https://job-boards.greenhouse.io/acme/jobs/4012345"]


def test_the_api_checks_a_job(test_db, api_client, monkeypatch):
    from backend.models.db import Setting
    test_db.add(Setting(key="dashboard_api_key", value=""))
    job = _job(test_db, "https://job-boards.greenhouse.io/acme/jobs/4012345", company="Acme", title="PM")

    async def fake_check(url, fetch=None):
        return "closed", "Greenhouse: the posting is gone"
    monkeypatch.setattr(so, "check_link", fake_check)
    body = api_client.post(f"/api/jobs/{job.id}/open-check").json()
    assert body["open_state"] == "closed" and body["open_checked_at"]
    listed = api_client.get(f"/api/jobs/{job.id}").json()
    assert listed["open_state"] == "closed" and listed["open_reason"] == "Greenhouse: the posting is gone"
    assert job.status == "new", "a check never changes the job's status"


@pytest.mark.asyncio
async def test_a_company_site_keyed_by_workday_numbers_is_checked_by_its_page():
    """Bank of America's site shows Workday numbers (workday:ghr:<n>) but is not Workday: its
    page is checked, not a Workday API address built from it."""
    url = "https://careers.bankofamerica.com/en-us/job-detail/26036109/data-support-manager"
    (state, _), calls = await check(url, {url: (200, url, "<h1>Data Support Manager</h1>")})
    assert state == "open" and calls == [url]


@pytest.mark.asyncio
async def test_a_phrase_inside_a_script_is_not_the_page_saying_it():
    """Dayforce ships its UI strings ("This job is no longer accepting applications.") in a script
    on every page, open or closed; only the visible text counts."""
    url = "https://jobs.dayforcehcm.com/kiddeglobal/CANDIDATEPORTAL/jobs/3670"
    page = ('<script id="__NEXT_DATA__">{"applyJobUnavailable":"This job is no longer accepting applications."}</script>'
            '<style>.x{content:"position has been filled"}</style><h1>Product Manager</h1>')
    (state, _), _ = await check(url, {url: (200, url, page)})
    assert state == "open"


# ── second review ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_myworkdaysite_link_is_checked_by_its_page():
    """Review #1: wd1.myworkdaysite.com/recruiting/<tenant>/<site>/job/... is not parsed for the job API."""
    url = "https://wd1.myworkdaysite.com/en-US/recruiting/snapchat/snap/job/Los-Angeles/Senior_R0045568"
    (state, _), calls = await check(url, {url: (200, url, "<h1>Senior</h1>")})
    assert state == "open" and calls == [url]


@pytest.mark.asyncio
async def test_eu_greenhouse_and_lever_use_their_eu_apis():
    """Review #2."""
    gh = "https://job-boards.eu.greenhouse.io/acme/jobs/4012345"
    gh_api = "https://boards-api.eu.greenhouse.io/v1/boards/acme/jobs/4012345"
    (state, _), calls = await check(gh, {gh_api: (200, gh_api, "{}")})
    assert state == "open" and calls == [gh_api]
    lv = "https://jobs.eu.lever.co/acme/1cafb489-9cae-4417-b272-832617484de0"
    lv_api = "https://api.eu.lever.co/v0/postings/acme/1cafb489-9cae-4417-b272-832617484de0"
    (state, _), calls = await check(lv, {lv_api: (404, lv_api, "")})
    assert state == "closed" and calls == [lv_api]


@pytest.mark.asyncio
async def test_no_database_transaction_is_held_while_a_site_answers(test_db, monkeypatch):
    """Review #4: the check releases its connection before the outside fetch."""
    job = _job(test_db, "https://job-boards.greenhouse.io/acme/jobs/4012345", company="Acme", title="PM")
    seen = []

    async def fake_check(url, fetch=None):
        seen.append(test_db.in_transaction())
        return "open", ""
    monkeypatch.setattr(so, "check_link", fake_check)
    out = await so.ensure_checked(test_db, job)
    assert seen == [False] and out["open_state"] == "open"


@pytest.mark.asyncio
async def test_a_redirect_that_keeps_the_job_id_is_open():
    """Review #6: ?gh_jid=123 -> /jobs/123-title loses the key but not the posting."""
    url = "https://www.acme.com/careers?gh_jid=4012345"
    final = "https://www.acme.com/jobs/4012345-product-manager"
    (state, _), _ = await check(url, {url: (200, final, "<h1>PM</h1>")})
    assert state == "open"


@pytest.mark.asyncio
async def test_a_bot_wall_served_with_404_is_unknown():
    """Review #7."""
    url = "https://www.amazon.jobs/en/jobs/10566262/sr-pm"
    wall = "<html><title>Just a moment...</title><div id='cf-challenge'>Checking your browser</div></html>"
    (state, _), _ = await check(url, {url: (404, url, wall)})
    assert state == "unknown"


@pytest.mark.asyncio
async def test_workday_permission_denied_is_a_removed_job():
    """Workday answers a removed posting with 403 {"errorCode": "S22", "message": "permission denied"}
    (Cisco's Seattle copy, closed by hand-check); its open Phoenix copy answers 200."""
    url = "https://cisco.wd5.myworkdayjobs.com/Cisco_Careers/job/Seattle-Washington-US/Solutions-Development-Architect_2025218-1"
    api = "https://cisco.wd5.myworkdayjobs.com/wday/cxs/cisco/Cisco_Careers/job/Seattle-Washington-US/Solutions-Development-Architect_2025218-1"
    body = '{"errorCode":"S22","errorCaseId":"x","httpStatus":403,"message":"permission denied","messageParams":{}}'
    (state, reason), calls = await check(url, {api: (403, api, body)})
    assert state == "closed" and "Workday" in reason and calls == [api]


@pytest.mark.asyncio
async def test_workday_never_falls_back_to_its_page():
    """A Workday page is an app shell that always loads: when the API cannot answer, the state is unknown."""
    url = "https://cisco.wd5.myworkdayjobs.com/Cisco_Careers/job/Seattle-Washington-US/X_2025218-1"
    (state, _), calls = await check(url, {})
    assert state == "unknown" and len(calls) == 1


def test_the_feed_filters_and_counts_by_posting_state(test_db, api_client):
    """Task 11: Posting filter — closed = checked closed; open = everything else."""
    from backend.models.db import Setting
    test_db.add(Setting(key="dashboard_api_key", value=""))
    closed = _job(test_db, "https://job-boards.greenhouse.io/acme/jobs/4012345", company="Acme", title="A")
    _job(test_db, "https://job-boards.greenhouse.io/acme/jobs/4012346", company="Acme", title="B")
    _job(test_db, "https://job-boards.greenhouse.io/acme/jobs/4012347", company="Acme", title="C")
    closed.open_state = "closed"
    test_db.commit()
    got = api_client.get("/api/jobs?status=new&open_state=closed").json()["jobs"]
    assert [j["id"] for j in got] == [str(closed.id)]
    assert len(api_client.get("/api/jobs?status=new&open_state=open").json()["jobs"]) == 2
    counts = {x["name"]: x["count"] for x in api_client.get("/api/jobs/facets?status=new").json()["open_states"]}
    assert counts == {"open": 2, "closed": 1}


@pytest.mark.asyncio
async def test_the_check_asks_like_a_browser(monkeypatch):
    """Meta answers 400 to a request without Accept-Language and the Sec-Fetch headers."""
    seen = {}

    class Resp:
        status_code, url, text = 404, "https://www.metacareers.com/jobs/position-not-available/", ""

    async def safe_get(url, **kw):
        seen.update(kw.get("headers") or {})
        return Resp()
    monkeypatch.setattr("backend.scraper._shared.url_safety.safe_get", safe_get)
    assert await so.check_link("https://www.metacareers.com/v2/jobs/1051197994374282/") == (so.CLOSED, "the page returns 404")
    assert seen.get("Accept-Language") and seen.get("Sec-Fetch-Mode") == "navigate"
