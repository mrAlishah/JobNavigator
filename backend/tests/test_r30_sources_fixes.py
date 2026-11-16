"""R30 sources review fixes.

S-04 CaribbeanJobs no longer stores a job whose page was not read (the next run retries it).
S-05 A throttled board stops the job-page reads for every subscriber of a shared fetch, and
     CaribbeanJobs stops after a persistent 429 or a Cloudflare challenge on a job page.
S-06 "Company include · exact" is honoured by TrulyRemote, CaribbeanJobs and Apify.
S-10 Ignored rows carry no content_hash, so they never refuse the user's own save.
"""
import json

import pytest

from backend.accounts.context import unscoped, use_account
from backend.models.db import Account, Job, ScrapeLog, Search
from backend.scraper.sources import apify, caribbeanjobs, trulyremote


@pytest.fixture
def accounts(test_db):
    a = test_db.query(Account).filter(Account.role == "owner").one()
    b = Account(name="Partner", role="member")
    test_db.add(b)
    test_db.commit()
    return a.id, b.id


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    """No sleeps, no activity log; body exclusion flags a description that names a visa."""
    async def no_sleep(_s):
        return None

    async def analyze(job, db=None, **kw):
        job.h1b_jd_flag = "visa" in (job.description or "").lower()

    for mod in (trulyremote, caribbeanjobs):
        monkeypatch.setattr(mod.asyncio, "sleep", no_sleep)
        monkeypatch.setattr(mod, "analyze_inline", analyze)
    monkeypatch.setattr("backend.activity.log_activity", lambda *a, **k: None)
    trulyremote._roster_cache.update(rows=None, expires=0.0)
    yield
    trulyremote._roster_cache.update(rows=None, expires=0.0)


class _Resp:
    def __init__(self, status=200, text="", headers=None):
        self.status_code = status
        self.text = text
        self.headers = headers or {}


def _search(db, mode, **kw):
    kw.setdefault("name", mode)
    kw.setdefault("results_wanted", 10)
    kw.setdefault("title_include_keywords", [])
    kw.setdefault("title_exclude_keywords", [])
    kw.setdefault("company_filter", [])
    kw.setdefault("company_exclude", [])
    kw.setdefault("auto_scoring_depth", "off")
    s = Search(active=True, search_mode=mode, **kw)
    db.add(s)
    db.commit()
    return s


# ── CaribbeanJobs helpers ────────────────────────────────────────────────────

CF_PAGE = ("<!DOCTYPE html><html><head><title>Just a moment...</title></head>"
           "<body><script>window._cf_chl_opt={cvId:'3'}</script></body></html>")


def _cj_row(title, url, company="Acme"):
    return {"title": title, "company": company, "url": url, "location": "Jamaica",
            "description": "A short blurb", "posted": None, "employment_type": "", "expired": False,
            "salary_min": None, "salary_max": None}


def _cj_page(title, company="Acme", desc="The full description from the job page.", valid=None):
    ld = {"@type": "JobPosting", "title": title, "hiringOrganization": {"name": company},
          "description": f"<p>{desc}</p>"}
    if valid:
        ld["validThrough"] = valid
    return f'<html><script type="application/ld+json" id="jobPostingSchema">{json.dumps(ld)}</script></html>'


def _cj_serve(monkeypatch, rows, pages):
    """Listing `rows`; `pages` maps a job URL to a _Resp, an exception, or a list served in turn."""
    requested = []

    async def fake_listings(client, base, keywords, wanted):
        return [dict(r) for r in rows]

    async def fake_get(client, url, params=None):
        requested.append(url)
        resp = pages[url]
        resp = resp.pop(0) if isinstance(resp, list) else resp
        if isinstance(resp, Exception):
            raise resp
        return resp

    monkeypatch.setattr(caribbeanjobs, "_fetch_listings", fake_listings)
    monkeypatch.setattr(caribbeanjobs, "_get", fake_get)
    return requested


# ── TrulyRemote helpers ──────────────────────────────────────────────────────

def _tr_row(title, url, company="Acme"):
    return {"title": title, "company": company, "url": url, "location": "Worldwide (no restrictions)",
            "description": "A short blurb", "employment_type": "Full-time", "posted": "2026-10-16",
            "salary_min": None, "salary_max": None, "salary_text": "", "salary_usd_annual": False}


def _tr_md(title, company="Acme", body="The full description."):
    return (f"# {title}\n\n**Company:** {company}\n**Location:** Worldwide (Fully Remote)\n"
            f"**Apply:** https://{company.lower()}.test/careers/{abs(hash(title)) % 1000}\n\n"
            f"## Description\n\n{body}\n")


def _tr_serve(monkeypatch, rows, page_for):
    """Roster `rows`; `page_for(job_url)` returns the _Resp for a `.md` request."""
    requested = []

    async def fake_roster(client):
        return [dict(r) for r in rows]

    async def fake_get(client, url, params=None):
        job_url = url[:-len(".md")]
        requested.append(job_url)
        return page_for(job_url)

    monkeypatch.setattr(trulyremote, "_fetch_roster", fake_roster)
    monkeypatch.setattr(trulyremote, "_get", fake_get)
    return requested


# ══ S-04: an unread CaribbeanJobs page is not stored and is retried next run ═══

@pytest.mark.asyncio
@pytest.mark.parametrize("first", [_Resp(500, "oops"), _Resp(404, "gone?"), RuntimeError("connection reset")])
async def test_caribbeanjobs_an_unread_page_is_retried_next_run(test_db, monkeypatch, first):
    search = _search(test_db, "caribbeanjobs", search_term="engineer")
    url = "https://cj.test/1"
    requested = _cj_serve(monkeypatch, [_cj_row("Backend Engineer", url)],
                          {url: [first, _Resp(200, _cj_page("Backend Engineer"))]})

    r1 = await caribbeanjobs.run(search)
    test_db.expire_all()
    assert r1["error"] is None and r1["new_jobs"] == 0
    assert r1["jobs_found"] == 1
    assert test_db.query(Job).count() == 0, "no blurb-only row, so the URL is not in the dedup set"

    r2 = await caribbeanjobs.run(search)
    test_db.expire_all()
    assert requested == [url, url]
    job = test_db.query(Job).one()
    assert r2["new_jobs"] == 1 and job.status == "new"
    assert job.description == "The full description from the job page."


@pytest.mark.asyncio
async def test_caribbeanjobs_body_exclusion_and_expiry_see_the_page_read_on_the_retry(test_db, monkeypatch):
    """The cases the blurb used to slip past: a visa line and an expired posting on the page."""
    search = _search(test_db, "caribbeanjobs", search_term="engineer")
    visa, old = "https://cj.test/visa", "https://cj.test/old"
    _cj_serve(monkeypatch, [_cj_row("Visa Role", visa), _cj_row("Old Role", old)], {
        visa: [_Resp(503, "busy"), _Resp(200, _cj_page("Visa Role", desc="We do not sponsor a visa."))],
        old: [_Resp(503, "busy"), _Resp(200, _cj_page("Old Role", valid="2020-01-01T00:00:00Z"))],
    })

    await caribbeanjobs.run(search)
    test_db.expire_all()
    assert test_db.query(Job).count() == 0

    r2 = await caribbeanjobs.run(search)
    test_db.expire_all()
    assert r2["new_jobs"] == 0
    assert {(j.title, j.status) for j in test_db.query(Job).all()} == {("Visa Role", "ignored")}


@pytest.mark.asyncio
async def test_caribbeanjobs_preview_flags_a_sampled_page_that_failed(test_db, monkeypatch):
    from backend.models.db import Setting
    test_db.add(Setting(key="body_exclusion_phrases", value=json.dumps(["us citizen only"])))
    test_db.commit()
    url = "https://cj.test/1"
    _cj_serve(monkeypatch, [_cj_row("Backend Engineer", url)], {url: _Resp(500, "oops")})
    r = await caribbeanjobs.preview(Search(name="t", search_mode="caribbeanjobs", search_term="x",
                                           title_exclude_keywords=[], company_exclude=[]), test_db)
    assert r["jobs"][0]["kept"] is True and r["jobs"][0]["body_checked"] is False


# ══ S-05: throttle stops ═════════════════════════════════════════════════════

@pytest.mark.asyncio
@pytest.mark.parametrize("stop", [_Resp(429), _Resp(403, CF_PAGE, {"cf-mitigated": "challenge"})])
async def test_caribbeanjobs_a_throttled_or_challenged_page_stops_the_remaining_reads(test_db, monkeypatch, stop):
    monkeypatch.setattr(caribbeanjobs, "_DETAIL_CONCURRENCY", 1)
    search = _search(test_db, "caribbeanjobs", search_term="engineer")
    urls = [f"https://cj.test/{i}" for i in range(5)]
    pages = {u: _Resp(200, _cj_page(f"Engineer {i}")) for i, u in enumerate(urls)}
    pages[urls[1]] = stop
    requested = _cj_serve(monkeypatch, [_cj_row(f"Engineer {i}", u) for i, u in enumerate(urls)], pages)

    r = await caribbeanjobs.run(search)
    test_db.expire_all()
    assert requested == urls[:2], "nothing is requested after the stopped page"
    assert r["new_jobs"] == 1 and [j.url for j in test_db.query(Job).all()] == [urls[0]]


@pytest.mark.asyncio
async def test_caribbeanjobs_get_still_backs_off_before_the_stop(monkeypatch):
    """The stop comes only after _get's own bounded retries gave up."""
    waits = []

    async def fake_sleep(s):
        waits.append(s)

    class _Client:
        def __init__(self):
            self.n = 0

        async def get(self, url, params=None):
            self.n += 1
            return _Resp(429)

    monkeypatch.setattr(caribbeanjobs.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(caribbeanjobs, "_DETAIL_CONCURRENCY", 1)
    client = _Client()
    out = await caribbeanjobs._fetch_details(client, [_cj_row("A", "https://cj.test/1"),
                                                      _cj_row("B", "https://cj.test/2")])
    assert out == {}
    assert client.n == caribbeanjobs._RATE_LIMIT_TRIES, "one page's retries, then no second page"
    assert waits == [5.0, 10.0]


@pytest.mark.asyncio
async def test_trulyremote_a_throttle_stop_holds_for_every_subscriber(test_db, accounts, monkeypatch):
    """A's reads hit a persistent 429: B, fed by the same shared fetch, requests no page. The
    next run reads every page once and both accounts get the jobs."""
    import backend.scraper.orchestrator as orch
    a, b = accounts
    monkeypatch.setattr(trulyremote, "_DETAIL_CONCURRENCY", 1)
    rows = [_tr_row(f"Engineer {i}", f"https://trw.test/jobs/{i}") for i in range(3)]
    state = {"throttled": True}
    requested = _tr_serve(monkeypatch, rows, lambda u: _Resp(429) if state["throttled"]
                          else _Resp(200, _tr_md(next(r["title"] for r in rows if r["url"] == u))))
    _search(test_db, "trulyremote")
    with use_account(b):
        _search(test_db, "trulyremote")

    await orch.run_all(force=True)
    assert len(requested) == 1, "one 429 stops the reads of both subscribers"
    with unscoped():
        test_db.expire_all()
        assert test_db.query(Job).count() == 0
        assert {l.account_id for l in test_db.query(ScrapeLog).all()} == {a, b}

    state["throttled"] = False
    requested.clear()
    await orch.run_all(force=True)
    assert sorted(requested) == sorted(r["url"] for r in rows), "each page read once, next run"
    with unscoped():
        test_db.expire_all()
        by_acc = {}
        for j in test_db.query(Job).all():
            by_acc.setdefault(j.account_id, set()).add(j.title)
    assert by_acc == {a: {r["title"] for r in rows}, b: {r["title"] for r in rows}}


@pytest.mark.asyncio
async def test_two_accounts_run_alls_back_to_back_fetch_the_board_once(test_db, accounts, monkeypatch):
    """Cross-run reuse: A's Run all fetches the roster and its pages; B's Run all a minute
    later rides on that fetch (no roster or page request) and still gets its own rows."""
    import backend.scraper.orchestrator as orch
    a, b = accounts
    rows = [_tr_row(f"Engineer {i}", f"https://trw.test/jobs/{i}") for i in range(2)]
    requested = _tr_serve(monkeypatch, rows, lambda u: _Resp(200, _tr_md(next(r["title"] for r in rows if r["url"] == u))))
    rosters = []
    serve_roster = trulyremote._fetch_roster

    async def counted_roster(client):
        rosters.append(1)
        return await serve_roster(client)
    monkeypatch.setattr(trulyremote, "_fetch_roster", counted_roster)
    _search(test_db, "trulyremote")
    with use_account(b):
        _search(test_db, "trulyremote")

    await orch.run_all(force=True, account_ids=[a])
    await orch.run_all(force=True, account_ids=[b])
    assert rosters == [1], "B reused A's roster"
    assert sorted(requested) == sorted(r["url"] for r in rows), "each page read once"
    with unscoped():
        test_db.expire_all()
        by_acc = {}
        for j in test_db.query(Job).all():
            by_acc.setdefault(j.account_id, set()).add(j.title)
    assert by_acc == {a: {r["title"] for r in rows}, b: {r["title"] for r in rows}}


@pytest.mark.asyncio
async def test_caribbeanjobs_a_throttle_stop_holds_for_every_subscriber(test_db, accounts, monkeypatch):
    import backend.scraper.orchestrator as orch
    a, b = accounts
    monkeypatch.setattr(caribbeanjobs, "_DETAIL_CONCURRENCY", 1)
    rows = [_cj_row(f"Engineer {i}", f"https://cj.test/{i}") for i in range(3)]
    requested = _cj_serve(monkeypatch, rows, {r["url"]: _Resp(429) for r in rows})
    _search(test_db, "caribbeanjobs", search_term="engineer")
    with use_account(b):
        _search(test_db, "caribbeanjobs", search_term="engineer")

    await orch.run_all(force=True)
    assert len(requested) == 1, "one 429 stops the reads of both subscribers"
    with unscoped():
        test_db.expire_all()
        assert test_db.query(Job).count() == 0


@pytest.mark.asyncio
async def test_a_halt_from_an_earlier_subscriber_skips_the_reads_but_not_the_cache(monkeypatch):
    """Pages an earlier subscriber already read still come from the memo after the stop."""
    async def fake_get(client, url, params=None):
        raise AssertionError("no request after the stop")

    monkeypatch.setattr(trulyremote, "_get", fake_get)
    cached = {"title": "Read", "company": "Acme", "url": "https://trw.test/jobs/1", "description": "x"}
    out = await trulyremote._fetch_details(
        None, [_tr_row("Read", "https://trw.test/jobs/1"), _tr_row("Unread", "https://trw.test/jobs/2")],
        cache={"https://trw.test/jobs/1": cached}, halt={"reason": "still rate-limited (429) after retries"})
    assert list(out) == ["https://trw.test/jobs/1"]


# ══ S-06: Company include ════════════════════════════════════════════════════

def test_company_included_is_an_exact_case_insensitive_match():
    from backend.scraper._shared.filters import company_included
    assert company_included("GitLab", []) == (True, None)
    assert company_included("GitLab", None) == (True, None)
    assert company_included("gitlab ", ["GitLab"]) == (True, None)
    assert company_included("GitLab Inc", ["GitLab"]) == (False, "Company filter: GitLab")
    assert company_included("", ["GitLab", "Acme"]) == (False, "Company filter: GitLab, Acme")


@pytest.mark.asyncio
async def test_trulyremote_reads_only_the_included_companies_pages(test_db, monkeypatch):
    rows = [_tr_row("Backend Engineer", "https://trw.test/jobs/1", company="GitLab"),
            _tr_row("Backend Engineer", "https://trw.test/jobs/2", company="Acme")]
    requested = _tr_serve(monkeypatch, rows, lambda u: _Resp(200, _tr_md(
        "Backend Engineer", company=next(r["company"] for r in rows if r["url"] == u))))
    search = _search(test_db, "trulyremote", company_filter=["gitlab"])

    r = await trulyremote.run(search)
    test_db.expire_all()
    assert requested == ["https://trw.test/jobs/1"], "the other company's page is never read"
    assert r["jobs_found"] == 1 and [j.company for j in test_db.query(Job).all()] == ["GitLab"]

    p = await trulyremote.preview(search, test_db)
    by = {j["company"]: j for j in p["jobs"]}
    assert by["GitLab"]["kept"] is True
    assert by["Acme"]["kept"] is False and by["Acme"]["reason"] == "Company filter: gitlab"


@pytest.mark.asyncio
async def test_caribbeanjobs_honours_company_include_before_and_after_the_page(test_db, monkeypatch):
    """The listing filter skips Acme's page; a page that renames its employer away from the
    list is kept as ignored, like any job dropped after its page."""
    rows = [_cj_row("Accountant", "https://cj.test/1", company="GitLab"),
            _cj_row("Accountant", "https://cj.test/2", company="Acme"),
            _cj_row("Controller", "https://cj.test/3", company="GitLab")]
    requested = _cj_serve(monkeypatch, rows, {
        "https://cj.test/1": _Resp(200, _cj_page("Accountant", company="GitLab")),
        "https://cj.test/3": _Resp(200, _cj_page("Controller", company="GitLab Recruiting Partner")),
    })
    search = _search(test_db, "caribbeanjobs", search_term="accountant", company_filter=["GitLab"])

    await caribbeanjobs.run(search)
    test_db.expire_all()
    assert sorted(requested) == ["https://cj.test/1", "https://cj.test/3"]
    assert {(j.company, j.status) for j in test_db.query(Job).all()} == {
        ("GitLab", "new"), ("GitLab Recruiting Partner", "ignored")}

    _cj_serve(monkeypatch, rows, {u: _Resp(200, _cj_page("Accountant", company=c))
                                  for u, c in (("https://cj.test/1", "GitLab"), ("https://cj.test/2", "Acme"),
                                               ("https://cj.test/3", "GitLab"))})
    p = await caribbeanjobs.preview(search, test_db)
    reasons = {j["url"]: j["reason"] for j in p["jobs"]}
    assert reasons["https://cj.test/1"] is None
    assert reasons["https://cj.test/2"] == "Company filter: GitLab"


@pytest.mark.asyncio
async def test_apify_honours_company_include_in_run_and_preview(test_db, monkeypatch):
    def jobs():
        return [{"title": "Platform Engineer", "company": c, "url": f"https://gh/{i}", "location": "",
                 "description": "Great role", "salary_min": None, "salary_max": None, "arrangement": None,
                 "posted": None, "source": "apify_greenhouse"} for i, c in enumerate(["Stripe", "Acme"])]

    async def fake_collect(_search, limit):
        return jobs(), {"apify_greenhouse": {"seen": 0, "new": 0, "returned": 2}}

    async def no_analyze(job, db=None, arrangement=None):
        job.h1b_jd_flag = False

    monkeypatch.setattr(apify, "_collect", fake_collect)
    monkeypatch.setattr(apify, "analyze_inline", no_analyze)
    search = _search(test_db, "apify", search_term="engineer", sources=["apify_greenhouse"],
                     company_filter=["Stripe"])

    res = await apify.run(search)
    test_db.expire_all()
    assert [j.company for j in test_db.query(Job).all()] == ["Stripe"]
    assert res["source_breakdown"]["apify_greenhouse"]["filtered"] == 1

    p = await apify.preview(search, test_db)
    assert {j["company"]: j["reason"] for j in p["jobs"]} == {"Stripe": None, "Acme": "Company filter: Stripe"}


# ══ S-10: ignored rows carry no content_hash ═════════════════════════════════

def test_mark_ignored_drops_the_content_hash():
    from backend.scraper._shared.dedup import mark_ignored
    job = Job(external_id="x", content_hash="h", status="new")
    mark_ignored(job)
    assert (job.status, job.content_hash) == ("ignored", None)


@pytest.mark.asyncio
async def test_trulyremote_ignored_rows_have_no_content_hash(test_db, monkeypatch):
    rows = [_tr_row("Clean Role", "https://trw.test/jobs/0"),
            _tr_row("Visa Role", "https://trw.test/jobs/1"),
            _tr_row("Engineer", "https://trw.test/jobs/2")]
    titles = {"https://trw.test/jobs/0": ("Clean Role", "A clean role."),
              "https://trw.test/jobs/1": ("Visa Role", "We do not sponsor a visa."),
              "https://trw.test/jobs/2": ("Engineering Intern", "Renamed on its page.")}
    _tr_serve(monkeypatch, rows, lambda u: _Resp(200, _tr_md(titles[u][0], body=titles[u][1])))
    await trulyremote.run(_search(test_db, "trulyremote", title_exclude_keywords=["intern"]))
    test_db.expire_all()
    got = {j.title: (j.status, j.content_hash is None) for j in test_db.query(Job).all()}
    assert got == {"Clean Role": ("new", False), "Visa Role": ("ignored", True),
                   "Engineering Intern": ("ignored", True)}


@pytest.mark.asyncio
async def test_caribbeanjobs_ignored_rows_have_no_content_hash(test_db, monkeypatch):
    rows = [_cj_row("Clean Role", "https://cj.test/0"), _cj_row("Visa Role", "https://cj.test/1"),
            _cj_row("Engineer", "https://cj.test/2")]
    _cj_serve(monkeypatch, rows, {
        "https://cj.test/0": _Resp(200, _cj_page("Clean Role")),
        "https://cj.test/1": _Resp(200, _cj_page("Visa Role", desc="We do not sponsor a visa.")),
        "https://cj.test/2": _Resp(200, _cj_page("Engineering Intern")),
    })
    await caribbeanjobs.run(_search(test_db, "caribbeanjobs", search_term="x", title_exclude_keywords=["intern"]))
    test_db.expire_all()
    got = {j.title: (j.status, j.content_hash is None) for j in test_db.query(Job).all()}
    assert got == {"Clean Role": ("new", False), "Visa Role": ("ignored", True),
                   "Engineering Intern": ("ignored", True)}


@pytest.mark.asyncio
async def test_company_page_ignored_rows_have_no_content_hash(test_db, monkeypatch):
    import backend.analyzer.h1b_checker as h1b
    import backend.scraper.sources.company_pages as cp
    from backend.models.db import Company

    async def no_desc(jobs):
        return []

    async def flag_engineer(job, db=None, company_lookup=None, phrases=None):
        job.h1b_jd_flag = job.title == "Software Engineer"

    async def fake_dispatch(url, **kw):
        return [{"title": "Product Manager", "url": "https://jobs.lever.co/acme/1"},
                {"title": "Software Engineer", "url": "https://jobs.lever.co/acme/2"},
                {"title": "Product Manager Intern", "url": "https://jobs.lever.co/acme/3"}]

    async def instant(_s):
        return None

    monkeypatch.setattr(cp, "_fetch_descriptions_parallel", no_desc)
    monkeypatch.setattr(h1b, "check_job_h1b", flag_engineer)
    monkeypatch.setattr(cp, "_dispatch_ats", fake_dispatch)
    monkeypatch.setattr(cp.asyncio, "sleep", instant)
    test_db.add(Company(name="Acme", scrape_urls=["https://jobs.lever.co/acme"], active=True,
                        auto_scoring_depth="off", title_exclude_keywords=["intern"]))
    test_db.commit()

    await cp.scrape_career_pages(force=True)
    test_db.expire_all()
    got = {j.title: (j.status, j.content_hash is None) for j in test_db.query(Job).all()}
    assert got == {"Product Manager": ("new", False), "Software Engineer": ("ignored", True),
                   "Product Manager Intern": ("ignored", True)}


@pytest.fixture
def _no_h1b(monkeypatch):
    import backend.analyzer.h1b_checker as h1b

    async def _noop(job, db=None, **kw):
        return None

    monkeypatch.setattr(h1b, "check_job_h1b", _noop)


def _legacy_ignored(db, company="Acme", title="Product Manager", url="https://board.test/pm-1"):
    """An ignored row as scrapers wrote it before 3.0: with a content_hash."""
    from backend.scraper._shared.dedup import make_content_hash, make_external_id
    db.add(Job(external_id=make_external_id(company, title, url), content_hash=make_content_hash(company, title),
               company=company, title=title, url=url, source="trulyremote", status="ignored"))
    db.commit()


async def _save(db, url, title="Product Manager", company="Acme"):
    from backend.api.routes_jobs import save_from_extension
    return await save_from_extension({"title": title, "company": company, "url": url,
                                      "description": "An ordinary description."}, db=db)


@pytest.mark.asyncio
async def test_a_scraper_ignored_row_does_not_refuse_the_same_role_saved_from_another_site(test_db, _no_h1b):
    _search(test_db, "extension", name="Extension")
    _legacy_ignored(test_db)
    out = await _save(test_db, "https://acme.test/careers/pm")
    assert (out["new"], out["saved"], out["status"]) == (True, True, "new")
    assert "reason" not in out


@pytest.mark.asyncio
async def test_the_same_url_as_an_ignored_row_is_still_refused(test_db, _no_h1b):
    _search(test_db, "extension", name="Extension")
    _legacy_ignored(test_db)
    out = await _save(test_db, "https://board.test/pm-1")
    assert (out["new"], out["saved"], out["status"]) == (False, False, "ignored")
    assert "filtered out" in out["reason"]


@pytest.mark.asyncio
async def test_a_feed_row_still_dedups_the_same_role_from_another_url(test_db, _no_h1b):
    _search(test_db, "extension", name="Extension")
    first = await _save(test_db, "https://board.test/pm-1")
    second = await _save(test_db, "https://acme.test/careers/pm")
    assert first["new"] is True and second["new"] is False and second["id"] == first["id"]


@pytest.mark.asyncio
async def test_an_extension_filtered_row_has_no_content_hash(test_db, _no_h1b):
    _search(test_db, "extension", name="Extension", title_exclude_keywords=["intern"])
    out = await _save(test_db, "https://board.test/intern", title="Product Intern")
    assert out["status"] == "ignored"
    assert test_db.query(Job).filter(Job.title == "Product Intern").one().content_hash is None


def test_manual_add_is_not_refused_by_an_ignored_row(test_db):
    from fastapi import BackgroundTasks
    from backend.api.routes_jobs import create_manual_job
    _legacy_ignored(test_db)
    out = create_manual_job({"title": "Product Manager", "company": "Acme", "url": "https://acme.test/careers/pm"},
                            BackgroundTasks(), db=test_db)
    assert out["created"] is True
