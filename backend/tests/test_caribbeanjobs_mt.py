"""caribbeanjobs under accounts (MT-8/MT-9): two accounts with the same search share one
listing fetch and read each detail page once, then ingest under their own filters; the
preview reads the account's own body-exclusion phrases through get_setting."""
import json

import pytest

from backend.accounts.context import unscoped, use_account
from backend.models.db import Account, Job, ScrapeLog, Search, Setting
from backend.scraper.sources import caribbeanjobs


@pytest.fixture
def accounts(test_db):
    a = test_db.query(Account).filter(Account.role == "owner").one()
    b = Account(name="Partner", role="member")
    test_db.add(b)
    test_db.commit()
    return a.id, b.id


def _search(db, **kw):
    kw.setdefault("name", "CJ")
    kw.setdefault("search_mode", "caribbeanjobs")
    kw.setdefault("search_term", "engineer")
    kw.setdefault("results_wanted", 10)
    kw.setdefault("title_include_keywords", [])
    kw.setdefault("title_exclude_keywords", [])
    kw.setdefault("company_filter", [])
    kw.setdefault("company_exclude", [])
    kw.setdefault("auto_scoring_depth", "off")
    s = Search(active=True, **kw)
    db.add(s)
    db.commit()
    return s


def _row(title, url):
    return {"title": title, "company": "Acme", "url": url, "location": "Jamaica",
            "description": "A role", "posted": None, "employment_type": "", "expired": False,
            "salary_min": None, "salary_max": None}


ROWS = [_row("Backend Engineer", "https://cj.test/1"),
        _row("Engineering Intern", "https://cj.test/2"),
        _row("Site Engineer", "https://cj.test/3")]


def test_same_query_is_one_target_and_a_different_filter_is_another(test_db, accounts):
    from backend.scraper.targets import is_shareable_search, search_key
    a, b = accounts
    url = "https://www.caribbeanjobs.com/ShowResults.aspx?Location=123&Keywords=x&Page=3"
    s1 = _search(test_db, direct_url=url, search_term="Software  Engineer, developer")
    with use_account(b):
        s2 = _search(test_db, direct_url=url.replace("Page=3", "Page=1"),
                     search_term="software engineer,developer", results_wanted=40)
        s3 = _search(test_db, direct_url=url.replace("Location=123", "Location=456"),
                     search_term="software engineer,developer")
    assert is_shareable_search(s1)
    assert search_key(s1) == search_key(s2) != search_key(s3)


@pytest.mark.asyncio
async def test_two_accounts_share_one_listing_fetch_and_each_detail_page(test_db, accounts, monkeypatch):
    import backend.scraper.orchestrator as orch
    a, b = accounts
    listing_calls, detail_urls = [], []

    async def fake_listings(client, base, keywords, wanted):
        listing_calls.append(wanted)
        return [dict(r) for r in ROWS]

    class _Resp:
        status_code = 200
        text = "<html></html>"

    async def fake_get(client, url, params=None):
        detail_urls.append(url)
        return _Resp()

    async def no_analyze(job, db=None, **kw):
        job.h1b_jd_flag = False

    async def no_sleep(_s):
        return None

    monkeypatch.setattr(caribbeanjobs, "_fetch_listings", fake_listings)
    monkeypatch.setattr(caribbeanjobs, "_get", fake_get)
    monkeypatch.setattr(caribbeanjobs, "analyze_inline", no_analyze)
    monkeypatch.setattr(caribbeanjobs.asyncio, "sleep", no_sleep)
    monkeypatch.setattr("backend.activity.log_activity", lambda *a, **k: None)

    _search(test_db, title_exclude_keywords=["intern"], results_wanted=10)
    with use_account(b):
        _search(test_db, results_wanted=12)

    await orch.run_all(force=True)

    assert listing_calls == [12], "one listing fetch, at the larger results_wanted"
    assert sorted(detail_urls) == sorted({r["url"] for r in ROWS}), "each detail page read once"
    with unscoped():
        test_db.expire_all()
        jobs = {}
        for j in test_db.query(Job).all():
            jobs.setdefault(j.account_id, set()).add(j.title)
        logs = test_db.query(ScrapeLog).all()
    assert jobs[a] == {"Backend Engineer", "Site Engineer"}          # A's own title filter
    assert jobs[b] == {r["title"] for r in ROWS}
    assert len({l.run_id for l in logs}) == 1 and {l.account_id for l in logs} == {a, b}


@pytest.mark.asyncio
async def test_a_job_ignored_after_its_page_is_kept_in_its_own_account(test_db, accounts, monkeypatch):
    """Shared run: the detail page renames /1 to an intern role. A (excludes "intern") keeps it
    as ignored in its own account, B keeps it as new; a second run reads no page again."""
    import backend.scraper.orchestrator as orch
    a, b = accounts
    detail_urls = []
    ld = ('<html><script type="application/ld+json" id="jobPostingSchema">'
          '{{"@type": "JobPosting", "title": "{t}", "hiringOrganization": {{"name": "Acme"}}}}'
          '</script></html>')
    titles = {"https://cj.test/1": "Backend Engineering Intern", "https://cj.test/3": "Site Engineer"}

    async def fake_listings(client, base, keywords, wanted):
        return [dict(r) for r in ROWS if r["url"] in titles]

    class _Resp:
        status_code = 200
        headers = {}

        def __init__(self, text):
            self.text = text

    async def fake_get(client, url, params=None):
        detail_urls.append(url)
        return _Resp(ld.format(t=titles[url]))

    async def no_analyze(job, db=None, **kw):
        job.h1b_jd_flag = False

    async def no_sleep(_s):
        return None

    monkeypatch.setattr(caribbeanjobs, "_fetch_listings", fake_listings)
    monkeypatch.setattr(caribbeanjobs, "_get", fake_get)
    monkeypatch.setattr(caribbeanjobs, "analyze_inline", no_analyze)
    monkeypatch.setattr(caribbeanjobs.asyncio, "sleep", no_sleep)
    monkeypatch.setattr("backend.activity.log_activity", lambda *a, **k: None)

    _search(test_db, title_exclude_keywords=["intern"])
    with use_account(b):
        _search(test_db)

    await orch.run_all(force=True)
    await orch.run_all(force=True)

    assert sorted(detail_urls) == sorted(titles), "each page read once across both runs"
    with unscoped():
        test_db.expire_all()
        rows = {(j.account_id, j.url): (j.title, j.status) for j in test_db.query(Job).all()}
        logs = test_db.query(ScrapeLog).all()
    assert rows == {
        (a, "https://cj.test/1"): ("Backend Engineering Intern", "ignored"),
        (a, "https://cj.test/3"): ("Site Engineer", "new"),
        (b, "https://cj.test/1"): ("Backend Engineering Intern", "new"),
        (b, "https://cj.test/3"): ("Site Engineer", "new"),
    }
    new_by_account = {}
    for log in logs:
        new_by_account.setdefault(log.account_id, []).append(log.new_jobs)
    assert sorted(new_by_account[a]) == [0, 1]       # the ignored row never counts as new
    assert sorted(new_by_account[b]) == [0, 2]


@pytest.mark.asyncio
async def test_preview_reads_the_accounts_own_body_exclusion_phrases(test_db, accounts, monkeypatch):
    """A member's own row must not stand in for the owner's install value (raw
    `Setting.key ==` returns whichever row comes first)."""
    a, b = accounts
    test_db.add(Setting(key="body_exclusion_phrases", value=json.dumps([]), account_id=b))
    test_db.add(Setting(key="body_exclusion_phrases", value=json.dumps(["us citizen only"])))
    test_db.commit()

    async def fake_listings(client, base, keywords, wanted):
        return [{**_row("Senior Engineer", "https://cj.test/9"), "description": "US citizen only please"}]

    async def fake_details(client, candidates, cache=None):
        return {}

    monkeypatch.setattr(caribbeanjobs, "_fetch_listings", fake_listings)
    monkeypatch.setattr(caribbeanjobs, "_fetch_details", fake_details)

    r = await caribbeanjobs.preview(Search(name="t", search_mode="caribbeanjobs", search_term="x",
                                           title_exclude_keywords=[], company_exclude=[]), test_db)
    assert r["jobs"][0]["kept"] is False and "Body exclusion" in r["jobs"][0]["reason"]
