"""trulyremote review fixes (PR #32 review, R1–R13): a job whose page was not read is retried
next run, a throttling board stops the run's page reads, the detail fields come from the header,
only explicit dollars count as USD, ignored rows are counted, the remote flag is the source's,
the preview carries body_phrase_count, the roster honours Cache-Control, and `posted` stays ISO."""
import json

import pytest

from backend.models.db import Job, Search, Setting
from backend.scraper.sources import trulyremote


@pytest.fixture(autouse=True)
def _fresh_roster_cache():
    trulyremote._roster_cache.update(rows=None, expires=0.0)
    yield
    trulyremote._roster_cache.update(rows=None, expires=0.0)


def _row(title, url, company="Acme", desc="A short blurb"):
    return {"title": title, "company": company, "url": url, "location": "Worldwide (no restrictions)",
            "description": desc, "employment_type": "Full-time", "posted": "2026-10-16",
            "salary_min": None, "salary_max": None, "salary_text": "", "salary_usd_annual": False}


def _md(title, company="Acme", body="The full description.", extra_head=""):
    return (f"# {title}\n\n**Company:** {company}\n**Location:** Worldwide (Fully Remote)\n"
            f"**Posted:** Fri Oct 16 2026\n**Apply:** https://{company.lower()}.test/careers/42\n{extra_head}\n"
            f"## Summary\n\nShort.\n\n## Description\n\n{body}\n")


class _Resp:
    def __init__(self, status=200, text="", headers=None):
        self.status_code = status
        self.text = text
        self.headers = headers or {}


def _search(db, **kw):
    kw.setdefault("name", "t")
    kw.setdefault("search_mode", "trulyremote")
    kw.setdefault("results_wanted", 10)
    kw.setdefault("title_exclude_keywords", [])
    kw.setdefault("company_exclude", [])
    s = Search(**kw)
    db.add(s)
    db.commit()
    return s


@pytest.fixture
def quiet(monkeypatch):
    """No sleeps, no activity log, analysis recorded instead of run."""
    calls = []

    async def no_sleep(_s):
        return None

    async def fake_analyze(job, db=None, **kw):
        calls.append(kw)
        job.h1b_jd_flag = False

    monkeypatch.setattr(trulyremote.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(trulyremote, "analyze_inline", fake_analyze)
    monkeypatch.setattr("backend.activity.log_activity", lambda *a, **k: None)
    return calls


def _serve(monkeypatch, rows, pages):
    """Roster `rows`; `pages` maps a job URL to a _Resp (or a list of them, served in turn)."""
    requested = []

    async def fake_roster(client):
        return [dict(r) for r in rows]

    async def fake_get(client, url, params=None):
        job_url = url[:-len(".md")]
        requested.append(job_url)
        resp = pages[job_url]
        return resp.pop(0) if isinstance(resp, list) else resp

    monkeypatch.setattr(trulyremote, "_fetch_roster", fake_roster)
    monkeypatch.setattr(trulyremote, "_get", fake_get)
    return requested


# ── R1: a job whose page was not read is not stored, so the next run retries it ──

@pytest.mark.asyncio
@pytest.mark.parametrize("first", [
    _Resp(500, "oops"),
    _Resp(200, "<html>Error page</html>"),     # 200 without a ## Description section
])
async def test_an_unread_job_page_is_retried_next_run(monkeypatch, test_db, quiet, first):
    search = _search(test_db)
    url = "https://trw.test/jobs/1"
    requested = _serve(monkeypatch, [_row("Backend Engineer", url)], {url: [first, _Resp(200, _md("Backend Engineer"))]})

    r1 = await trulyremote.run(search)
    test_db.expire_all()
    assert r1["new_jobs"] == 0 and test_db.query(Job).count() == 0, "no blurb-only row"
    assert r1["jobs_found"] == 1

    r2 = await trulyremote.run(search)
    test_db.expire_all()
    assert requested == [url, url]
    job = test_db.query(Job).one()
    assert r2["new_jobs"] == 1
    assert job.description == "The full description."
    assert job.url == "https://acme.test/careers/42"


@pytest.mark.asyncio
async def test_a_gone_job_page_is_skipped_quietly(monkeypatch, test_db, quiet, caplog):
    search = _search(test_db)
    gone, live = "https://trw.test/jobs/gone", "https://trw.test/jobs/live"
    _serve(monkeypatch, [_row("Gone Role", gone), _row("Live Role", live)],
           {gone: _Resp(404), live: _Resp(200, _md("Live Role"))})
    with caplog.at_level("INFO", logger="jobnavigator.trulyremote"):
        r = await trulyremote.run(search)
    test_db.expire_all()
    assert r["new_jobs"] == 1 and [j.title for j in test_db.query(Job).all()] == ["Live Role"]
    assert not any(gone in rec.getMessage() and rec.levelname != "DEBUG" for rec in caplog.records)


# ── R2: one page still throttled after its retries stops the run's page reads ──

@pytest.mark.asyncio
async def test_a_throttled_page_stops_the_remaining_reads(monkeypatch, test_db, quiet):
    monkeypatch.setattr(trulyremote, "_DETAIL_CONCURRENCY", 1)
    search = _search(test_db)
    urls = [f"https://trw.test/jobs/{i}" for i in range(5)]
    pages = {u: _Resp(200, _md(f"Role {i}")) for i, u in enumerate(urls)}
    pages[urls[1]] = _Resp(429)
    requested = _serve(monkeypatch, [_row(f"Role {i}", u) for i, u in enumerate(urls)], pages)

    r = await trulyremote.run(search)
    test_db.expire_all()
    assert requested == urls[:2], "nothing is requested after the throttled page"
    assert r["new_jobs"] == 1 and test_db.query(Job).count() == 1


# ── R4: the detail fields come from the header only ──

def test_detail_fields_are_read_from_the_header_only():
    body = "We are hiring.\n\n**Location:** Berlin office\n**Salary:** €50,000\n**Apply:** https://evil.test"
    out = trulyremote._parse_detail(_md("Engineer", body=body, extra_head="**Salary:** $150k - $200k\n"),
                                    {"title": "x", "company": "", "url": "u"})
    assert out["location"] == "Worldwide (Fully Remote)"
    assert out["apply_url"] == "https://acme.test/careers/42"
    assert (out["salary_min"], out["salary_max"], out["salary_usd_annual"]) == (150000, 200000, True)
    assert "Berlin office" in out["description"]


# ── R5: only an explicit dollar amount counts as USD ──

@pytest.mark.parametrize("text,usd_annual", [
    ("$150K - $200K", True),
    ("USD 120,000 - 150,000", True),
    ("US$90,000", True),
    ("$150k+", True),
    ("4000 - 6000", False),            # no currency at all
    ("120,000 - 150,000", False),
    ("A$120,000", False), ("AU$120k", False), ("C$100,000", False), ("CA$100k", False),
    ("NZ$90k", False), ("HK$500,000", False), ("S$80,000", False), ("R$100.000", False),
    ("MX$400,000", False),
    ("₹20,00,000", False), ("¥8,000,000", False), ("€65K - €105K", False), ("£90,000", False),
    ("CAD 100,000", False), ("$100,000 AUD", False),
    ("$40 per hour", False), ("$8,000/month", False),
])
def test_only_explicit_dollars_are_stored_as_usd(text, usd_annual):
    assert trulyremote._salary_fields(text)["salary_usd_annual"] is usd_annual


# ── R6: ignored rows are counted, keep their description, and seen is not decremented ──

@pytest.mark.asyncio
async def test_ignored_after_the_page_is_counted_and_keeps_its_description(monkeypatch, test_db, quiet):
    search = _search(test_db, title_exclude_keywords=["intern"])
    url = "https://trw.test/jobs/1"
    _serve(monkeypatch, [_row("Backend Engineer", url)], {url: _Resp(200, _md("Backend Engineering Intern"))})
    r = await trulyremote.run(search)
    test_db.expire_all()
    row = test_db.query(Job).one()
    assert (r["new_jobs"], r["ignored_jobs"], r["jobs_found"]) == (0, 1, 1)
    assert row.status == "ignored" and row.description == "The full description."


# ── R7: every job is remote by the board's definition ──

@pytest.mark.asyncio
async def test_the_remote_flag_comes_from_the_source(monkeypatch, test_db, quiet):
    search = _search(test_db)
    url = "https://trw.test/jobs/1"
    _serve(monkeypatch, [_row("Backend Engineer", url)], {url: _Resp(200, _md("Backend Engineer"))})
    await trulyremote.run(search)
    assert quiet == [{"arrangement": True}]


def test_a_true_arrangement_sets_remote_whatever_the_location_says():
    from backend.analyzer.work_arrangement import apply_arrangement_to_job
    job = Job(title="Engineer", location="Worldwide (no restrictions)", description="")
    apply_arrangement_to_job(job, structured=True)
    assert job.arr_remote is True and job.remote is True


# ── R8: the preview carries body_phrase_count for the "body not checked" marker ──

@pytest.mark.asyncio
async def test_preview_has_body_phrase_count_and_marks_unread_rows(monkeypatch, test_db, quiet):
    test_db.add(Setting(key="body_exclusion_phrases", value=json.dumps(["no visa", "us citizen only"])))
    test_db.commit()
    monkeypatch.setattr(trulyremote, "PREVIEW_DETAIL_LIMIT", 1)
    a, b = "https://trw.test/jobs/a", "https://trw.test/jobs/b"
    _serve(monkeypatch, [_row("Role A", a), _row("Role B", b)], {a: _Resp(200, _md("Role A"))})
    r = await trulyremote.preview(Search(name="t", search_mode="trulyremote",
                                         title_exclude_keywords=[], company_exclude=[]), test_db)
    assert r["body_phrase_count"] == 2
    by = {j["title"]: j for j in r["jobs"]}
    assert "body_checked" not in by["Role A"]
    assert by["Role B"]["body_checked"] is False


# ── R9: the roster honours Cache-Control ──

class _RosterClient:
    def __init__(self, headers):
        self.headers = headers
        self.calls = 0

    async def get(self, url, params=None):
        self.calls += 1
        r = _Resp(200, "## Engineer at Acme\n- URL: https://trw.test/jobs/1\n\nBlurb.\n", self.headers)
        r.raise_for_status = lambda: None
        return r


@pytest.mark.asyncio
async def test_the_roster_is_reused_for_its_cache_lifetime(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(trulyremote.time, "monotonic", lambda: clock[0])
    client = _RosterClient({"Cache-Control": "public, s-maxage=3600, stale-while-revalidate=86400"})

    rows = await trulyremote._fetch_roster(client)
    rows[0]["title"] = "changed by a caller"
    again = await trulyremote._fetch_roster(client)
    assert client.calls == 1 and again[0]["title"] == "Engineer", "a cached copy, never the caller's"

    clock[0] += 3601
    await trulyremote._fetch_roster(client)
    assert client.calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [{}, {"Cache-Control": "no-store"}, {"Cache-Control": "max-age=0"}])
async def test_the_roster_is_not_cached_without_permission(headers):
    client = _RosterClient(headers)
    await trulyremote._fetch_roster(client)
    await trulyremote._fetch_roster(client)
    assert client.calls == 2


@pytest.mark.parametrize("value,ttl", [
    ("public, s-maxage=3600, stale-while-revalidate=86400", 3600),
    ("max-age=300, s-maxage=86400", 300),
    ("s-maxage=86400", 3600),           # capped at an hour
    ("no-cache, max-age=600", 0),
    ("", 0),
])
def test_cache_ttl(value, ttl):
    assert trulyremote._cache_ttl({"Cache-Control": value}) == ttl


# ── R13: posted stays ISO ──

@pytest.mark.parametrize("value,iso", [
    ("Fri Oct 16 2026", "2026-10-16"),
    ("2026-10-16", "2026-10-16"),
    ("Oct 16, 2026", "2026-10-16"),
    ("yesterday", None),
    ("", None),
])
def test_iso_date(value, iso):
    assert trulyremote._iso_date(value) == iso


def test_an_unparseable_page_date_keeps_the_roster_date():
    text = _md("Engineer").replace("Fri Oct 16 2026", "recently")
    out = trulyremote._parse_detail(text, {"title": "x", "company": "", "url": "u", "posted": "2026-10-01"})
    assert out["posted"] == "2026-10-01"
