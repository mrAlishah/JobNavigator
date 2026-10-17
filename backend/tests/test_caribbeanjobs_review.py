"""caribbeanjobs review fixes: a readable Cloudflare error (run, Test, ScrapeLog/health), Test
reads at most PREVIEW_DETAIL_LIMIT detail pages, the filters re-run after a detail page renames
a job, and a local-currency salary stays text instead of reading as USD."""
import pytest

from backend.accounts.context import unscoped
from backend.models.db import Job, ScrapeLog, Search
from backend.scraper.sources import caribbeanjobs


class _Resp:
    def __init__(self, status=200, text="<html><title>Jobs</title></html>", headers=None):
        self.status_code = status
        self.text = text
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"Client error '{self.status_code}'")


CF_PAGE = ("<!DOCTYPE html><html><head><title>Just a moment...</title></head>"
           "<body><script>window._cf_chl_opt={cvId:'3'}</script></body></html>")
CF_TEXT = "CaribbeanJobs is behind a Cloudflare challenge from this network; the scraper cannot pass it"


def _job(title, company, url, **kw):
    row = {"title": title, "company": company, "url": url, "location": "Trinidad",
           "description": "A role with enough words to count as a description", "posted": None,
           "employment_type": "", "expired": False, "salary_min": None, "salary_max": None}
    row.update(kw)
    return row


def _search(db, **kw):
    kw.setdefault("name", "CJ")
    kw.setdefault("search_mode", "caribbeanjobs")
    kw.setdefault("search_term", "accountant")
    kw.setdefault("results_wanted", 50)
    kw.setdefault("title_include_keywords", [])
    kw.setdefault("title_exclude_keywords", [])
    kw.setdefault("company_filter", [])
    kw.setdefault("company_exclude", [])
    kw.setdefault("auto_scoring_depth", "off")
    s = Search(active=True, **kw)
    db.add(s)
    db.commit()
    return s


@pytest.fixture
def quiet(monkeypatch):
    async def noop_analyze(job, db=None, **kw):
        job.h1b_jd_flag = False

    async def no_sleep(_s):
        return None

    monkeypatch.setattr(caribbeanjobs, "analyze_inline", noop_analyze)
    monkeypatch.setattr(caribbeanjobs.asyncio, "sleep", no_sleep)
    monkeypatch.setattr("backend.activity.log_activity", lambda *a, **k: None)


# ── a. Cloudflare ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("resp", [
    _Resp(403, "<html>blocked</html>", {"cf-mitigated": "challenge"}),
    _Resp(403, CF_PAGE),
    _Resp(503, "<html><script src='/cdn-cgi/challenge-platform/h/b/orchestrate/chl_page/v1'></script></html>"),
    _Resp(403, "<html>Forbidden</html>", {"server": "cloudflare"}),
    _Resp(200, CF_PAGE),
])
def test_challenge_responses_are_recognised(resp):
    assert caribbeanjobs._is_cloudflare_challenge(resp) is True


@pytest.mark.parametrize("resp", [
    _Resp(200, "<html><title>Accountant jobs</title><div class='job-result'></div></html>", {"server": "cloudflare"}),
    _Resp(404, "<html>Not found</html>", {"server": "nginx"}),
    _Resp(403, "<html>Forbidden</html>", {"server": "Microsoft-IIS/10.0"}),
])
def test_ordinary_responses_are_not_a_challenge(resp):
    assert caribbeanjobs._is_cloudflare_challenge(resp) is False


def _challenge_everything(monkeypatch):
    calls = []

    async def fake_get(client, url, params=None):
        calls.append(url)
        return _Resp(403, CF_PAGE, {"cf-mitigated": "challenge", "server": "cloudflare"})

    monkeypatch.setattr(caribbeanjobs, "_get", fake_get)
    return calls


@pytest.mark.asyncio
async def test_run_reports_the_challenge_in_words(test_db, monkeypatch, quiet):
    calls = _challenge_everything(monkeypatch)
    s = _search(test_db)
    res = await caribbeanjobs.run(s)
    assert res["error"].startswith(CF_TEXT) and "HTTP 403" in res["error"]
    assert res["new_jobs"] == 0
    assert len(calls) == 1, "the first challenged listing page stops the run"


@pytest.mark.asyncio
async def test_preview_reports_the_challenge_in_words(test_db, monkeypatch):
    _challenge_everything(monkeypatch)
    r = await caribbeanjobs.preview(Search(name="t", search_mode="caribbeanjobs", search_term="x",
                                           title_exclude_keywords=[], company_exclude=[]), test_db)
    assert r["error"].startswith(CF_TEXT)


@pytest.mark.asyncio
async def test_scheduled_run_stores_the_challenge_as_the_search_error(test_db, monkeypatch, quiet):
    """The ScrapeLog error is what the search card (last_error) and /health/entities show."""
    import backend.scraper.orchestrator as orch
    _challenge_everything(monkeypatch)
    _search(test_db)
    await orch.run_all(force=True)
    with unscoped():
        test_db.expire_all()
        logs = test_db.query(ScrapeLog).filter(ScrapeLog.source == "caribbeanjobs").all()
    assert logs and all((l.error or "").startswith(CF_TEXT) for l in logs)
    assert len(CF_TEXT) + len(" (HTTP 403)") <= 160, "health shows the first 160 characters"


@pytest.mark.asyncio
async def test_a_challenged_detail_page_is_not_returned_and_not_cached(monkeypatch):
    async def fake_get(client, url, params=None):
        return _Resp(403, CF_PAGE, {"cf-mitigated": "challenge"})

    async def no_sleep(_s):
        return None

    monkeypatch.setattr(caribbeanjobs, "_get", fake_get)
    monkeypatch.setattr(caribbeanjobs.asyncio, "sleep", no_sleep)
    cache = {}
    out = await caribbeanjobs._fetch_details(None, [_job("A", "Acme", "https://cj.test/1")], cache=cache)
    assert out == {} and cache == {}


# ── b. Test samples at most PREVIEW_DETAIL_LIMIT detail pages ────────────────

def _patch_listing(monkeypatch, rows):
    fetched = []

    async def fake_listings(client, base, keywords, wanted):
        return [dict(r) for r in rows]

    async def fake_details(client, candidates, cache=None):
        fetched.extend(c["url"] for c in candidates)
        return {c["url"]: dict(c, description="Full description from the job page " * 3) for c in candidates}

    monkeypatch.setattr(caribbeanjobs, "_fetch_listings", fake_listings)
    monkeypatch.setattr(caribbeanjobs, "_fetch_details", fake_details)
    return fetched


@pytest.mark.asyncio
async def test_preview_reads_at_most_the_detail_limit_and_says_so(test_db, monkeypatch):
    n = caribbeanjobs.PREVIEW_DETAIL_LIMIT + 11
    fetched = _patch_listing(monkeypatch, [_job(f"Accountant {i}", "Acme", f"https://cj.test/{i}") for i in range(n)])
    r = await caribbeanjobs.preview(Search(name="t", search_mode="caribbeanjobs", search_term="x",
                                           title_exclude_keywords=[], company_exclude=[]), test_db)
    assert len(fetched) == caribbeanjobs.PREVIEW_DETAIL_LIMIT == 20
    assert r["details_sampled"] == 20 and r["raw_count"] == n
    assert "first 20 of 31" in r["preview_note"]


@pytest.mark.asyncio
async def test_preview_under_the_limit_reads_every_page_and_adds_no_note(test_db, monkeypatch):
    fetched = _patch_listing(monkeypatch, [_job(f"Accountant {i}", "Acme", f"https://cj.test/{i}") for i in range(5)])
    r = await caribbeanjobs.preview(Search(name="t", search_mode="caribbeanjobs", search_term="x",
                                           title_exclude_keywords=[], company_exclude=[]), test_db)
    assert len(fetched) == 5 and r["preview_note"] is None


# ── c. filters re-run after the detail page renames a job ───────────────────

@pytest.mark.asyncio
async def test_run_rechecks_the_title_and_company_after_the_detail_page(test_db, monkeypatch, quiet):
    rows = [_job("Accountant", "Acme", "https://cj.test/1"),
            _job("Accountant II", "Acme", "https://cj.test/2"),
            _job("Accountant III", "Acme", "https://cj.test/3")]

    async def fake_listings(client, base, keywords, wanted):
        return [dict(r) for r in rows]

    async def fake_details(client, candidates, cache=None):
        renamed = {"https://cj.test/1": {"title": "Accounting Intern"},          # now excluded by title
                   "https://cj.test/2": {"company": "Blocked Corp"},            # now an excluded company
                   "https://cj.test/3": {}}
        return {c["url"]: {**c, **renamed[c["url"]]} for c in candidates}

    monkeypatch.setattr(caribbeanjobs, "_fetch_listings", fake_listings)
    monkeypatch.setattr(caribbeanjobs, "_fetch_details", fake_details)
    s = _search(test_db, title_exclude_keywords=["intern"], company_exclude=["blocked corp"])
    res = await caribbeanjobs.run(s)
    test_db.expire_all()
    assert [j.title for j in test_db.query(Job).filter(Job.status == "new").all()] == ["Accountant III"]
    # The two dropped after their page are kept as "ignored" (dedup only), not as new jobs.
    ignored = {j.url: j.title for j in test_db.query(Job).filter(Job.status == "ignored").all()}
    assert ignored == {"https://cj.test/1": "Accounting Intern", "https://cj.test/2": "Accountant II"}
    assert res["new_jobs"] == 1 and res["ignored_jobs"] == 2
    assert res["jobs_found"] == 3          # all three passed the listing filters; no `seen -= 1`


# ── d. a job dropped after its page is read is remembered, so the page is read once ──

LD_PAGE = ('<html><script type="application/ld+json" id="jobPostingSchema">'
           '{{"@type": "JobPosting", "title": "{title}", "hiringOrganization": {{"name": "Acme"}},'
           ' "description": "<p>The full description from the job page.</p>"}}</script></html>')


def test_detail_fetches_are_two_at_a_time_about_a_second_apart():
    assert caribbeanjobs._DETAIL_CONCURRENCY == 2
    gaps = [caribbeanjobs._detail_gap() for _ in range(200)]
    assert all(0.7 <= g <= 1.3 for g in gaps)
    assert len({round(g, 3) for g in gaps}) > 1        # jittered, not a fixed beat


@pytest.mark.asyncio
async def test_detail_fetches_sleep_the_jittered_gap(monkeypatch):
    slept = []

    async def fake_sleep(s):
        slept.append(s)

    async def fake_get(client, url, params=None):
        return _Resp(200, LD_PAGE.format(title="Accountant"))

    monkeypatch.setattr(caribbeanjobs.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(caribbeanjobs, "_get", fake_get)
    await caribbeanjobs._fetch_details(None, [_job("A", "Acme", f"https://cj.test/{i}") for i in range(3)])
    assert len(slept) == 3 and all(0.7 <= s <= 1.3 for s in slept)


@pytest.mark.asyncio
async def test_a_job_the_detail_title_drops_is_stored_ignored_and_not_fetched_again(test_db, monkeypatch, quiet):
    rows = [_job("Accountant", "Acme", "https://cj.test/1"),
            _job("Senior Accountant", "Acme", "https://cj.test/2")]
    detail_titles = {"https://cj.test/1": "Accounting Intern",        # passes the listing, fails here
                     "https://cj.test/2": "Senior Accountant"}
    fetched = []

    async def fake_listings(client, base, keywords, wanted):
        return [dict(r) for r in rows]

    async def fake_get(client, url, params=None):
        fetched.append(url)
        return _Resp(200, LD_PAGE.format(title=detail_titles[url]))

    monkeypatch.setattr(caribbeanjobs, "_fetch_listings", fake_listings)
    monkeypatch.setattr(caribbeanjobs, "_get", fake_get)
    s = _search(test_db, title_exclude_keywords=["intern"])

    first = await caribbeanjobs.run(s)
    second = await caribbeanjobs.run(s)

    assert sorted(fetched) == ["https://cj.test/1", "https://cj.test/2"], \
        "each page read once: the second run reads no detail page again"
    test_db.expire_all()
    rows_by_url = {j.url: (j.title, j.status) for j in test_db.query(Job).all()}
    assert rows_by_url == {"https://cj.test/1": ("Accounting Intern", "ignored"),
                           "https://cj.test/2": ("Senior Accountant", "new")}
    assert (first["new_jobs"], first["ignored_jobs"], first["jobs_found"]) == (1, 1, 2)
    assert (second["new_jobs"], second["ignored_jobs"], second["jobs_found"]) == (0, 0, 2)


# ── f. salaries in a local currency stay text ───────────────────────────────

def test_currency_and_unit_are_read_from_the_salary_text():
    assert caribbeanjobs._currency_of("TT$ 8,000 - 10,000 per month") == "TTD"
    assert caribbeanjobs._currency_of("J$2,400,000") == "JMD"
    assert caribbeanjobs._currency_of("US$ 60,000") == "USD"
    assert caribbeanjobs._currency_of("USD 60,000 annually") == "USD"
    assert caribbeanjobs._currency_of("85000 - 95000") == ""
    assert caribbeanjobs._unit_of("TT$ 8,000 per month") == "MONTH"
    assert caribbeanjobs._unit_of("60,000 per annum") == "YEAR"
    assert caribbeanjobs._unit_of("85000 - 95000") == ""


def test_detail_json_ld_currency_and_unit_are_kept():
    html = """<script id="jobPostingSchema" type="application/ld+json">
        {"@type": "JobPosting", "title": "Accountant",
         "baseSalary": {"currency": "TTD", "value": {"minValue": 8000, "maxValue": 10000, "unitText": "MONTH"}}}
        </script>"""
    out = caribbeanjobs._parse_detail(html, {"title": "x", "company": "Acme", "location": ""})
    assert (out["salary_min"], out["salary_max"]) == (8000, 10000)
    assert out["salary_currency"] == "TTD" and out["salary_unit"] == "MONTH"
    assert caribbeanjobs._usd_annual(out) == (None, None)
    assert caribbeanjobs._salary_display(out) == "TTD 8,000 – 10,000 / month"


@pytest.mark.parametrize("currency,unit,stored", [
    ("USD", "YEAR", (60000, 70000)),
    ("USD", "", (60000, 70000)),
    ("USD", "MONTH", (None, None)),
    ("TTD", "YEAR", (None, None)),
    ("", "", (None, None)),
])
def test_only_usd_per_year_becomes_numbers(currency, unit, stored):
    assert caribbeanjobs._usd_annual({"salary_min": 60000, "salary_max": 70000,
                                      "salary_currency": currency, "salary_unit": unit}) == stored


@pytest.mark.asyncio
async def test_run_keeps_a_local_salary_as_text_and_drops_the_extractors_dollar_guess(test_db, monkeypatch):
    rows = [_job("Accountant", "Acme", "https://cj.test/1", description="Pays TT$8,000 - $10,000 a month",
                 salary_min=8000, salary_max=10000, salary_currency="TTD", salary_unit="MONTH"),
            _job("Controller", "Acme", "https://cj.test/2",
                 salary_min=90000, salary_max=110000, salary_currency="USD", salary_unit="YEAR")]

    async def fake_listings(client, base, keywords, wanted):
        return [dict(r) for r in rows]

    async def fake_details(client, candidates, cache=None):
        return {c["url"]: dict(c) for c in candidates}   # each page read, nothing new on it

    async def extractor_like(job, db=None, **kw):
        # what apply_salary_to_job does with "$8,000 - $10,000" in the text: read it as USD
        job.h1b_jd_flag = False
        if not (job.salary_min and job.salary_source == "posting") and "$" in (job.description or ""):
            job.salary_min, job.salary_max, job.salary_source = 8000, 10000, "posting"

    async def no_sleep(_s):
        return None

    monkeypatch.setattr(caribbeanjobs, "_fetch_listings", fake_listings)
    monkeypatch.setattr(caribbeanjobs, "_fetch_details", fake_details)
    monkeypatch.setattr(caribbeanjobs, "analyze_inline", extractor_like)
    monkeypatch.setattr(caribbeanjobs.asyncio, "sleep", no_sleep)
    monkeypatch.setattr("backend.activity.log_activity", lambda *a, **k: None)

    await caribbeanjobs.run(_search(test_db))
    test_db.expire_all()
    by = {j.title: j for j in test_db.query(Job).all()}
    local = by["Accountant"]
    assert local.salary_min is None and local.salary_max is None and local.salary_source == "unknown"
    assert local.description.endswith("Salary (as posted): TTD 8,000 – 10,000 / month")
    usd = by["Controller"]
    assert (usd.salary_min, usd.salary_max, usd.salary_source) == (90000, 110000, "posting")
    assert "Salary (as posted)" not in (usd.description or "")


@pytest.mark.asyncio
async def test_run_reads_only_an_explicit_usd_salary_from_the_description(test_db, monkeypatch):
    """R30 S-02/S-03: with no structured salary, the real extractor runs on the description; a
    bare "$" there is usually the island's dollar, so only a figure named as USD is stored."""
    from backend.analyzer.salary_extractor import apply_salary_to_job
    rows = [_job("Bare", "Acme", "https://cj.test/1", description="Salary: $120,000 - $150,000 per annum"),
            _job("Prefixed", "Acme", "https://cj.test/2", description="Salary: TT$180,000 per annum"),
            _job("Explicit", "Acme", "https://cj.test/3", description="Salary: US$90,000 - US$110,000 per year"),
            _job("Monthly", "Acme", "https://cj.test/4", salary_min=4000, salary_max=6000,
                 salary_currency="USD", salary_unit=caribbeanjobs._unit_of("USD 4,000 - 6,000 a month"))]

    async def fake_listings(client, base, keywords, wanted):
        return [dict(r) for r in rows]

    async def fake_details(client, candidates, cache=None):
        return {c["url"]: dict(c) for c in candidates}   # each page read, nothing new on it

    async def real_salary(job, db=None, **kw):
        job.h1b_jd_flag = False
        apply_salary_to_job(job)

    async def no_sleep(_s):
        return None

    monkeypatch.setattr(caribbeanjobs, "_fetch_listings", fake_listings)
    monkeypatch.setattr(caribbeanjobs, "_fetch_details", fake_details)
    monkeypatch.setattr(caribbeanjobs, "analyze_inline", real_salary)
    monkeypatch.setattr(caribbeanjobs.asyncio, "sleep", no_sleep)
    monkeypatch.setattr("backend.activity.log_activity", lambda *a, **k: None)

    await caribbeanjobs.run(_search(test_db))
    test_db.expire_all()
    by = {j.title: (j.salary_min, j.salary_max, j.salary_source) for j in test_db.query(Job).all()}
    assert by["Bare"] == (None, None, "unknown")
    assert by["Prefixed"] == (None, None, "unknown")
    assert by["Explicit"] == (90000, 110000, "posting")
    assert by["Monthly"] == (None, None, "unknown")
    monthly = test_db.query(Job).filter(Job.title == "Monthly").one()
    assert monthly.description.endswith("Salary (as posted): USD 4,000 – 6,000 / month")


@pytest.mark.asyncio
async def test_preview_shows_the_salary_with_its_currency(test_db, monkeypatch):
    _patch_listing(monkeypatch, [_job("Accountant", "Acme", "https://cj.test/1", salary_min=8000,
                                      salary_max=10000, salary_currency="TTD", salary_unit="MONTH")])
    r = await caribbeanjobs.preview(Search(name="t", search_mode="caribbeanjobs", search_term="x",
                                           title_exclude_keywords=[], company_exclude=[]), test_db)
    assert r["jobs"][0]["salary"] == "TTD 8,000 – 10,000 / month"
