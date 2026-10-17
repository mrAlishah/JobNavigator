"""trulyremote under accounts (MT-8/MT-9): every trulyremote search of every account shares one
roster fetch and reads each `.md` job page once, then ingests under its own filters; the preview
reads the account's own body-exclusion phrases through get_setting."""
import json

import pytest

from backend.accounts.context import unscoped, use_account
from backend.models.db import Account, Job, ScrapeLog, Search, Setting
from backend.scraper.sources import trulyremote


@pytest.fixture
def accounts(test_db):
    a = test_db.query(Account).filter(Account.role == "owner").one()
    b = Account(name="Partner", role="member")
    test_db.add(b)
    test_db.commit()
    return a.id, b.id


def _search(db, **kw):
    kw.setdefault("name", "TRW")
    kw.setdefault("search_mode", "trulyremote")
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
    return {"title": title, "company": "Acme", "url": url, "location": "Worldwide (no restrictions)",
            "description": "A short blurb", "employment_type": "Full-time", "posted": "2026-10-16",
            "salary_min": None, "salary_max": None, "salary_text": "", "salary_usd_annual": False}


ROWS = [_row("Backend Engineer", "https://trw.test/jobs/1"),
        _row("Engineering Intern", "https://trw.test/jobs/2"),
        _row("Site Engineer", "https://trw.test/jobs/3")]


def _md(title):
    return (f"# {title}\n\n**Company:** Acme\n**Location:** Worldwide (Fully Remote)\n"
            f"**Apply:** https://acme.test/apply\n\n## Description\n\nThe full description of {title}.\n")


class _Resp:
    status_code = 200
    headers = {}

    def __init__(self, text):
        self.text = text


def _patch(monkeypatch, rows, titles, roster_calls, detail_urls):
    async def fake_roster(client):
        roster_calls.append(1)
        return [dict(r) for r in rows]

    async def fake_get(client, url, params=None):
        detail_urls.append(url)
        return _Resp(_md(titles[url[:-len(".md")]]))

    async def no_analyze(job, db=None, **kw):
        job.h1b_jd_flag = False

    async def no_sleep(_s):
        return None

    monkeypatch.setattr(trulyremote, "_fetch_roster", fake_roster)
    monkeypatch.setattr(trulyremote, "_get", fake_get)
    monkeypatch.setattr(trulyremote, "analyze_inline", no_analyze)
    monkeypatch.setattr(trulyremote.asyncio, "sleep", no_sleep)
    monkeypatch.setattr("backend.activity.log_activity", lambda *a, **k: None)


def test_every_trulyremote_search_is_one_shareable_target(test_db, accounts):
    from backend.scraper.targets import is_shareable_search, search_fetch_params, search_key
    a, b = accounts
    s1 = _search(test_db, title_include_keywords=["engineer"], results_wanted=5)
    s2 = _search(test_db, name="TRW 2", title_exclude_keywords=["intern"])
    with use_account(b):
        s3 = _search(test_db, results_wanted=200)
    assert is_shareable_search(s1)
    assert search_key(s1) == search_key(s2) == search_key(s3) == ("trulyremote",)
    assert search_fetch_params([s1, s2, s3]) == {}


@pytest.mark.asyncio
async def test_two_accounts_share_one_roster_fetch_and_each_job_page(test_db, accounts, monkeypatch):
    """A excludes "intern"; B takes everything. One roster call, each `.md` read once; the page
    renames /1 to an intern role, so A keeps it as ignored and B as new."""
    import backend.scraper.orchestrator as orch
    a, b = accounts
    roster_calls, detail_urls = [], []
    titles = {"https://trw.test/jobs/1": "Backend Engineering Intern",
              "https://trw.test/jobs/2": "Engineering Intern",
              "https://trw.test/jobs/3": "Site Engineer"}
    _patch(monkeypatch, ROWS, titles, roster_calls, detail_urls)

    _search(test_db, title_exclude_keywords=["intern"])
    with use_account(b):
        _search(test_db, results_wanted=12)

    await orch.run_all(force=True)

    assert roster_calls == [1], "one roster fetch for both accounts"
    assert sorted(detail_urls) == sorted(u + ".md" for u in titles), "each job page read once"
    with unscoped():
        test_db.expire_all()
        rows = {(j.account_id, j.title): j.status for j in test_db.query(Job).all()}
        logs = test_db.query(ScrapeLog).all()
    assert rows == {
        (a, "Backend Engineering Intern"): "ignored",      # A's own title filter, after the page
        (a, "Site Engineer"): "new",
        (b, "Backend Engineering Intern"): "new",
        (b, "Engineering Intern"): "new",
        (b, "Site Engineer"): "new",
    }
    assert len({l.run_id for l in logs}) == 1 and {l.account_id for l in logs} == {a, b}

    # A second shared run reads no page again: every URL is in both accounts' dedup sets.
    detail_urls.clear()
    await orch.run_all(force=True)
    assert detail_urls == []


@pytest.mark.asyncio
async def test_preview_reads_the_accounts_own_body_exclusion_phrases(test_db, accounts, monkeypatch):
    """A member's own row must not stand in for the owner's install value (raw
    `Setting.key ==` returns whichever row comes first)."""
    a, b = accounts
    test_db.add(Setting(key="body_exclusion_phrases", value=json.dumps([]), account_id=b))
    test_db.add(Setting(key="body_exclusion_phrases", value=json.dumps(["us citizen only"])))
    test_db.commit()

    async def fake_roster(client):
        return [{**_row("Senior Engineer", "https://trw.test/jobs/9"), "description": "US citizen only please"}]

    async def fake_details(client, candidates, cache=None):
        return {}

    monkeypatch.setattr(trulyremote, "_fetch_roster", fake_roster)
    monkeypatch.setattr(trulyremote, "_fetch_details", fake_details)

    r = await trulyremote.preview(Search(name="t", search_mode="trulyremote",
                                         title_exclude_keywords=[], company_exclude=[]), test_db)
    assert r["jobs"][0]["kept"] is False and "Body exclusion" in r["jobs"][0]["reason"]
