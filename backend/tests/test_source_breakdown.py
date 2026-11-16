"""A JobSpy board that hard-fails must not look like one that found nothing. jobspy (1.4+) names
each failed board in `jobs_df.attrs["errors"]`; these tests pin the per-source breakdown, warning
flag and run summary built from it."""
import sys
import types

import pytest

from backend.models.db import ScrapeLog, Search


def _fake_jobspy(rows, errors=None, warnings=None):
    """Install a stand-in jobspy module whose scrape_jobs returns rows, with the board errors and
    warnings in `attrs` the way the real library (1.4+) reports them."""
    import pandas as pd

    # compose_location() imports jobspy.model; load the real one before the
    # stand-in shadows the package, so this file also passes when run alone.
    import jobspy.model  # noqa: F401

    def scrape_jobs(**kwargs):
        df = pd.DataFrame(rows)
        df.attrs = {"errors": dict(errors or {}), "warnings": dict(warnings or {})}
        return df

    mod = types.ModuleType("jobspy")
    mod.scrape_jobs = scrape_jobs
    mod.model = sys.modules["jobspy.model"]
    sys.modules["jobspy"] = mod
    return mod


def _row(site, title, company, url):
    return {
        "site": site, "title": title, "company": company, "job_url": url,
        "description": "A perfectly ordinary job description. " * 5,
        "location": "Remote", "min_amount": None, "max_amount": None,
    }


def _first_run_auth(db):
    """Empty dashboard_api_key triggers the middleware's first-run bypass, so endpoint tests exercise the serializer, not the 401."""
    from backend.models.db import Setting
    db.add(Setting(key="dashboard_api_key", value=""))
    db.commit()


def _search(db, sources, country=None):
    s = Search(name="ZZ Search", search_mode="keyword", active=True, sources=sources,
               search_term="program manager", title_include_keywords=[],
               title_exclude_keywords=[], company_filter=[], company_exclude=[])
    if country is not None:
        s.country = country
    db.add(s)
    db.commit()
    return s


# The literal texts python-jobspy 1.4.1 reports. jobspy/util.py check_status() raises
# "response status code <n>" and adds a hint on 403 and 429.
ZIP_REDIRECTED = "response status code 307"   # measured from an EU address
ZIP_FORBIDDEN = "response status code 403 (blocked: use a proxy)"
RATE_LIMITED = "response status code 429 (blocked: use a proxy)"
GOOGLE_NO_DATA = "no job data returned"
GOOGLE_NO_DATA_1_3 = "Google returned no job data"
LINKEDIN_JOB_TYPE_WARNING = "job_type isn't supported, ignoring it"


# ── the source module ───────────────────────────────────────────────────────

def test_breakdown_counts_each_board(test_db, monkeypatch):
    from backend.scraper.sources.jobspy import _run_sync

    _fake_jobspy([
        _row("indeed", "Program Manager", "Acme", "https://indeed.test/a"),
        _row("indeed", "Delivery Manager", "Acme", "https://indeed.test/b"),
        _row("linkedin", "Product Manager", "Beta", "https://linkedin.test/c"),
    ])
    search = _search(test_db, ["indeed", "linkedin"])

    result = _run_sync(search)

    assert result["jobs_found"] == 3
    assert result["source_breakdown"]["indeed"]["seen"] == 2
    assert result["source_breakdown"]["linkedin"]["seen"] == 1
    # every board reported a result — nothing errored
    assert not any("error" in v for v in result["source_breakdown"].values())


def test_breakdown_records_a_refused_board(test_db, monkeypatch):
    """Each failed board is recorded; an undiagnosed HTTP failure is condensed to its status code."""
    from backend.scraper.sources.jobspy import GOOGLE_BLOCKED, _run_sync

    _fake_jobspy([_row("indeed", "Program Manager", "Acme", "https://indeed.test/a")],
                 errors={"linkedin": RATE_LIMITED, "google": GOOGLE_NO_DATA})
    search = _search(test_db, ["indeed", "linkedin", "google"])

    result = _run_sync(search)

    bd = result["source_breakdown"]
    assert bd["indeed"]["seen"] == 1
    assert bd["linkedin"]["error"] == "429"
    assert bd["google"]["error"] == GOOGLE_BLOCKED
    # the overall run still succeeded — the failure lives per source
    assert result["error"] is None


# ── known blocks ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("site, message, expected", [
    ("zip_recruiter", ZIP_REDIRECTED, "ZIP_RECRUITER_REDIRECTED"),
    ("zip_recruiter", ZIP_FORBIDDEN, "ZIP_RECRUITER_BLOCKED"),
    ("google", GOOGLE_NO_DATA, "GOOGLE_BLOCKED"),
    ("google", GOOGLE_NO_DATA_1_3, "GOOGLE_BLOCKED"),
    # A failure nobody diagnosed keeps its current text.
    ("zip_recruiter", RATE_LIMITED, "429"),
    ("zip_recruiter", "Read timed out", "Read timed out"),
    # A signature counts only on its own board.
    ("indeed", ZIP_FORBIDDEN, "403"),
    ("indeed", GOOGLE_NO_DATA, GOOGLE_NO_DATA),
])
def test_a_known_block_signature_gets_a_stable_text(site, message, expected):
    import pandas as pd
    import backend.scraper.sources.jobspy as js

    df = pd.DataFrame()
    df.attrs = {"errors": {site: message}}
    assert js._errors_of(df) == {site: getattr(js, expected, expected)}


def test_errors_of_a_frame_without_attrs_is_empty():
    import pandas as pd
    from backend.scraper.sources.jobspy import _errors_of

    assert _errors_of(pd.DataFrame()) == {}
    assert _errors_of(None) == {}


def test_google_that_returned_rows_records_no_error(test_db, monkeypatch):
    """A Google board that returned rows was not blocked; a "no job data" error came from a later page."""
    from backend.scraper.sources.jobspy import _run_sync

    _fake_jobspy([_row("google", "Program Manager", "Acme", "https://google.test/a")],
                 errors={"google": GOOGLE_NO_DATA})
    result = _run_sync(_search(test_db, ["google"]))

    google = result["source_breakdown"]["google"]
    assert "error" not in google
    assert google["returned"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("rows", [
    [],  # every board failed: the early return
    [_row("indeed", "Program Manager", "Acme", "https://indeed.test/a")],
])
async def test_the_test_run_reports_each_failed_board(test_db, rows):
    """Before this, a failed board simply disappeared from the Test run result."""
    from backend.api.routes_searches import test_search
    from backend.scraper.sources.jobspy import GOOGLE_BLOCKED, ZIP_RECRUITER_REDIRECTED

    _fake_jobspy(rows, errors={"zip_recruiter": ZIP_REDIRECTED, "google": GOOGLE_NO_DATA})
    search = _search(test_db, ["indeed", "zip_recruiter", "google"])

    out = await test_search(str(search.id), db=test_db)

    assert out["source_errors"] == {
        "zip_recruiter": ZIP_RECRUITER_REDIRECTED,
        "google": GOOGLE_BLOCKED,
    }
    assert out["source_breakdown"] == ({"indeed": 1} if rows else {})


@pytest.mark.asyncio
async def test_the_test_run_reports_only_boards_it_asked_for(test_db):
    from backend.api.routes_searches import test_search

    _fake_jobspy([_row("indeed", "Program Manager", "Acme", "https://indeed.test/a")],
                 errors={"zip_recruiter": ZIP_FORBIDDEN})
    search = _search(test_db, ["linkedin", "indeed"])

    out = await test_search(str(search.id), db=test_db)

    assert out["source_errors"] == {}
    assert out["source_breakdown"] == {"indeed": 1}


def test_a_run_reports_only_boards_it_asked_for(test_db, monkeypatch):
    """The scheduled path merges errors with setdefault, so it would add a foreign board too."""
    from backend.scraper.sources.jobspy import _run_sync

    _fake_jobspy([_row("indeed", "Program Manager", "Acme", "https://indeed.test/a")],
                 errors={"zip_recruiter": ZIP_FORBIDDEN})

    result = _run_sync(_search(test_db, ["indeed"]))

    assert set(result["source_breakdown"]) == {"indeed"}
    assert "error" not in result["source_breakdown"]["indeed"]


def test_breakdown_records_an_indeed_failure(test_db, monkeypatch):
    from backend.scraper.sources.jobspy import _run_sync

    _fake_jobspy([_row("linkedin", "Product Manager", "Beta", "https://linkedin.test/c")],
                 errors={"indeed": "response status code 503"})
    search = _search(test_db, ["indeed", "linkedin"])

    result = _run_sync(search)

    bd = result["source_breakdown"]
    assert bd["indeed"]["error"] == "503"
    assert bd["linkedin"]["seen"] == 1


def test_a_board_warning_does_not_fail_it(test_db, monkeypatch):
    """A warning is advice or per-job trouble (LinkedIn says it on every run that sets job_type)."""
    from backend.scraper.sources.jobspy import _run_sync

    _fake_jobspy(
        [_row("linkedin", "Product Manager", "Beta", "https://linkedin.test/c"),
         _row("indeed", "Program Manager", "Acme", "https://indeed.test/a")],
        warnings={"linkedin": [LINKEDIN_JOB_TYPE_WARNING, "empty page at start=25"]},
    )
    result = _run_sync(_search(test_db, ["indeed", "linkedin"]))

    linkedin = result["source_breakdown"]["linkedin"]
    assert "error" not in linkedin
    assert linkedin["returned"] == 1
    assert not any("error" in v for v in result["source_breakdown"].values())


def test_condense_error_keeps_non_http_text():
    from backend.scraper.sources.jobspy import _condense_error

    assert _condense_error(ZIP_FORBIDDEN) == "403"
    assert _condense_error("Google returned no job data") == "Google returned no job data"


# ── a country jobspy no longer knows ────────────────────────────────────────

def test_a_run_with_a_dropped_country_says_so(test_db, monkeypatch):
    """jobspy 1.4 dropped ten countries with no Indeed site; such a search fails with a reason."""
    from backend.scraper.sources.jobspy import _run_sync

    _fake_jobspy([_row("indeed", "Program Manager", "Acme", "https://indeed.test/a")])
    result = _run_sync(_search(test_db, ["indeed"], country="bulgaria"))

    assert result["new_jobs"] == 0
    assert "no site for the country 'bulgaria'" in result["error"]


@pytest.mark.asyncio
async def test_the_test_run_with_a_dropped_country_is_a_400(test_db):
    from fastapi import HTTPException
    from backend.api.routes_searches import test_search

    _fake_jobspy([])
    search = _search(test_db, ["indeed"], country="bulgaria")
    with pytest.raises(HTTPException) as exc:
        await test_search(str(search.id), db=test_db)
    assert exc.value.status_code == 400


# ── the ScrapeLog row ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_scrape_log_flags_a_failed_source(test_db, monkeypatch):
    import backend.scraper.orchestrator as orch

    search = _search(test_db, ["indeed", "zip_recruiter"])

    async def fake_run_search(s, proxy_url=None):
        return {
            "jobs_found": 9, "new_jobs": 0, "error": None, "duration": 1.0,
            "source_breakdown": {
                "indeed": {"seen": 9, "new": 0},
                "zip_recruiter": {"seen": 0, "new": 0, "error": "403"},
            },
        }

    monkeypatch.setattr(orch, "run_search", fake_run_search)
    await orch._run_search_by_id(str(search.id), auto_score=False)

    log = test_db.query(ScrapeLog).filter(ScrapeLog.search_id == search.id).one()
    assert log.is_warning is True
    assert log.source_breakdown["zip_recruiter"]["error"] == "403"
    assert log.source_breakdown["indeed"]["seen"] == 9


@pytest.mark.asyncio
async def test_scrape_log_flags_a_board_that_returned_nothing(test_db, monkeypatch):
    """The silent zero: Indeed answers 200 with an empty list while LinkedIn works.

    is_warning used to be read off the TOTAL, so 40 + 0 was recorded as healthy.
    """
    import backend.scraper.orchestrator as orch

    search = _search(test_db, ["indeed", "linkedin"])

    async def fake_run_search(s, proxy_url=None):
        return {
            "jobs_found": 40, "new_jobs": 12, "error": None, "duration": 1.0,
            "source_breakdown": {
                "indeed": {"seen": 0, "new": 0, "returned": 0},
                "linkedin": {"seen": 40, "new": 12, "returned": 40},
            },
        }

    monkeypatch.setattr(orch, "run_search", fake_run_search)
    await orch._run_search_by_id(str(search.id), auto_score=False)

    log = test_db.query(ScrapeLog).filter(ScrapeLog.search_id == search.id).one()
    assert log.is_warning is True
    # The empty board is not a failed one — the breakdown keeps the distinction.
    assert "error" not in log.source_breakdown["indeed"]


def test_empty_sources_reads_the_unfiltered_count():
    """`returned` is the only truthful test; `seen` and `filtered` both read 0 for a board whose rows were rejected and already stored."""
    from backend.scraper.orchestrator import empty_sources

    # the board delivered 7 rows; the title filter dropped them all
    assert empty_sources({"indeed": {"seen": 0, "new": 0, "returned": 7}}) == []
    # …and on the next run those rows dedup away, so `filtered` is 0 too
    assert empty_sources({"indeed": {"seen": 0, "new": 0, "filtered": 0, "returned": 7}}) == []
    assert empty_sources({"indeed": {"seen": 0, "new": 0, "returned": 0, "error": "403"}}) == []
    assert empty_sources({"indeed": {"seen": 0, "new": 0, "returned": 0}}) == ["indeed"]
    # A row an older build wrote holds no evidence either way.
    assert empty_sources({"indeed": {"seen": 0, "new": 0}}) == []
    assert empty_sources(None) == []


def test_a_board_whose_rows_the_filter_rejects_is_not_reported_empty(test_db, monkeypatch):
    """The regression this guard exists for, through the real source module.

    Run 1 stores the rejected rows as `ignored`. Run 2 dedups them, so `filtered`
    stops counting and only `returned` still shows the board delivered rows.
    """
    from backend.scraper.orchestrator import empty_sources
    from backend.scraper.sources.jobspy import _run_sync

    rows = [_row("google", f"Recruiter {i}", "Acme", f"https://google.test/{i}") for i in range(12)]
    _fake_jobspy(rows)
    search = _search(test_db, ["google"])
    search.title_include_keywords = ["program manager"]     # rejects all 12
    test_db.commit()

    first = _run_sync(search)
    second = _run_sync(search)

    assert first["source_breakdown"]["google"] == {"seen": 0, "new": 0, "returned": 12, "filtered": 12}
    # run 2: every rejected row is already stored, so `filtered` never increments
    assert second["source_breakdown"]["google"] == {"seen": 0, "new": 0, "returned": 12}
    assert empty_sources(second["source_breakdown"]) == []


def test_a_frame_without_a_site_column_reports_no_evidence(test_db, monkeypatch):
    """Rows but no `site` column: the run cannot say which board sent what.

    Writing `returned: 0` for every board would make the run report "no rows
    from indeed, linkedin" while it stored jobs. No key at all puts the run in
    the bucket empty_sources() already skips.
    """
    import pandas as pd
    from backend.scraper.orchestrator import empty_sources
    from backend.scraper.sources.jobspy import _run_sync

    mod = _fake_jobspy([])
    rows = [_row("indeed", "Program Manager", "Acme", "https://indeed.test/a"),
            _row("linkedin", "Product Manager", "Beta", "https://linkedin.test/b")]
    frame = pd.DataFrame(rows).drop(columns=["site"])
    mod.scrape_jobs = lambda **kwargs: frame
    search = _search(test_db, ["indeed", "linkedin"])

    result = _run_sync(search)

    assert result["jobs_found"] == 2, "the rows are still scraped and stored"
    for key, entry in result["source_breakdown"].items():
        assert "returned" not in entry, f"{key} must carry no evidence: {entry}"
    assert empty_sources(result["source_breakdown"]) == []


def test_an_empty_frame_is_evidence_that_every_board_returned_nothing(test_db, monkeypatch):
    """The control: an empty frame is a real answer, so every board gets 0."""
    from backend.scraper.orchestrator import empty_sources
    from backend.scraper.sources.jobspy import _run_sync

    _fake_jobspy([])
    search = _search(test_db, ["indeed", "linkedin"])

    result = _run_sync(search)

    assert result["source_breakdown"] == {
        "indeed": {"seen": 0, "new": 0, "returned": 0},
        "linkedin": {"seen": 0, "new": 0, "returned": 0},
    }
    assert sorted(empty_sources(result["source_breakdown"])) == ["indeed", "linkedin"]


def test_a_board_that_returned_nothing_is_still_reported_empty(test_db, monkeypatch):
    """The control: the same shape, with a board that really delivered no rows."""
    from backend.scraper.orchestrator import empty_sources
    from backend.scraper.sources.jobspy import _run_sync

    _fake_jobspy([_row("linkedin", "Program Manager", "Beta", "https://linkedin.test/a")])
    search = _search(test_db, ["indeed", "linkedin"])

    result = _run_sync(search)

    assert result["source_breakdown"]["indeed"]["returned"] == 0
    assert result["source_breakdown"]["linkedin"]["returned"] == 1
    assert empty_sources(result["source_breakdown"]) == ["indeed"]


@pytest.mark.asyncio
async def test_run_summary_names_a_board_that_returned_nothing(test_db, monkeypatch):
    import backend.api.routes_searches as rs
    import backend.scraper.orchestrator as orch

    search = _search(test_db, ["indeed", "linkedin"])

    async def fake_run(search_id, auto_score=None):
        return {
            "jobs_found": 40, "new_jobs": 12, "error": None, "duration": 1.0,
            "source_breakdown": {
                "indeed": {"seen": 0, "new": 0, "returned": 0},
                "linkedin": {"seen": 40, "new": 12, "returned": 40},
            },
        }

    monkeypatch.setattr(orch, "_run_search_by_id", fake_run)

    captured = {}
    monkeypatch.setattr("backend.job_monitor.launch_background",
                        lambda job_type, coro_func, **kw: captured.setdefault("coro", coro_func) and "r")
    await rs.trigger_search(str(search.id), db=test_db)

    summary = await captured["coro"]()
    assert "no rows from indeed" in summary


@pytest.mark.asyncio
async def test_scrape_log_unchanged_when_every_source_is_fine(test_db, monkeypatch):
    import backend.scraper.orchestrator as orch

    search = _search(test_db, ["indeed", "linkedin"])

    async def fake_run_search(s, proxy_url=None):
        return {
            "jobs_found": 9, "new_jobs": 3, "error": None, "duration": 1.0,
            "source_breakdown": {
                "indeed": {"seen": 6, "new": 2},
                "linkedin": {"seen": 3, "new": 1},
            },
        }

    monkeypatch.setattr(orch, "run_search", fake_run_search)
    await orch._run_search_by_id(str(search.id), auto_score=False)

    log = test_db.query(ScrapeLog).filter(ScrapeLog.search_id == search.id).one()
    assert log.is_warning is False
    assert log.error is None
    assert log.source_breakdown["indeed"]["new"] == 2


# ── the run summary ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_run_summary_names_the_failed_sources(test_db, monkeypatch):
    import backend.api.routes_searches as rs
    import backend.scraper.orchestrator as orch

    search = _search(test_db, ["indeed", "zip_recruiter", "google"])

    async def fake_run(search_id, auto_score=None):
        return {
            "jobs_found": 9, "new_jobs": 0, "error": None, "duration": 1.0,
            "source_breakdown": {
                "indeed": {"seen": 9, "new": 0},
                "zip_recruiter": {"seen": 0, "new": 0, "error": "403"},
                "google": {"seen": 0, "new": 0, "error": "Google returned no job data"},
            },
        }

    monkeypatch.setattr(orch, "_run_search_by_id", fake_run)

    captured = {}

    def fake_launch(job_type, coro_func, **kw):
        captured["coro"] = coro_func
        return "run-1"

    monkeypatch.setattr("backend.job_monitor.launch_background", fake_launch)
    await rs.trigger_search(str(search.id), db=test_db)

    summary = await captured["coro"]()
    assert summary.startswith("ZZ Search - 9 seen, +0 new · ")
    assert "zip_recruiter: 403" in summary
    assert "google: Google returned no job data" in summary


@pytest.mark.asyncio
async def test_run_summary_unchanged_when_sources_are_clean(test_db, monkeypatch):
    import backend.api.routes_searches as rs
    import backend.scraper.orchestrator as orch

    search = _search(test_db, ["indeed"])

    async def fake_run(search_id, auto_score=None):
        return {"jobs_found": 12, "new_jobs": 4, "error": None, "duration": 1.0,
                "source_breakdown": {"indeed": {"seen": 12, "new": 4}}}

    monkeypatch.setattr(orch, "_run_search_by_id", fake_run)

    captured = {}
    monkeypatch.setattr("backend.job_monitor.launch_background",
                        lambda job_type, coro_func, **kw: captured.setdefault("coro", coro_func) and "r")
    await rs.trigger_search(str(search.id), db=test_db)
    assert await captured["coro"]() == "ZZ Search - 12 seen, +4 new"


# ── the API surface the Searches screen reads ───────────────────────────────

def test_search_dict_exposes_last_source_errors(test_db):
    from backend.api.routes_searches import _search_to_dict

    search = _search(test_db, ["indeed", "zip_recruiter"])
    log = ScrapeLog(search_id=search.id, source="jobspy", jobs_found=9, new_jobs=0,
                    is_warning=True, duration_seconds=1.0,
                    source_breakdown={"indeed": {"seen": 9, "new": 0},
                                      "zip_recruiter": {"seen": 0, "new": 0, "error": "403"}})
    test_db.add(log)
    test_db.commit()

    d = _search_to_dict(search, last_log=log)
    assert d["last_source_errors"] == [
        {"source": "zip_recruiter", "label": "ZipRecruiter", "error": "403"}
    ]


def test_scrape_log_endpoint_serializes_source_breakdown(test_db, api_client):
    """The run-history endpoint has to carry the per-board outcome — a populated row is still useless if the endpoint drops the column on the way out."""
    _first_run_auth(test_db)
    search = _search(test_db, ["indeed", "zip_recruiter"])
    test_db.add(ScrapeLog(
        search_id=search.id, source="jobspy", jobs_found=9, new_jobs=0,
        is_warning=True, duration_seconds=1.0,
        source_breakdown={"indeed": {"seen": 9, "new": 0},
                          "zip_recruiter": {"seen": 0, "new": 0, "error": "403"}},
    ))
    test_db.commit()

    rows = api_client.get("/api/scrape-log").json()
    row = next(r for r in rows if r["search_id"] == str(search.id))
    assert row["source_breakdown"]["zip_recruiter"]["error"] == "403"
    assert row["source_breakdown"]["indeed"]["seen"] == 9


def test_scrape_log_endpoint_source_breakdown_is_null_when_absent(test_db, api_client):
    _first_run_auth(test_db)
    search = _search(test_db, ["indeed"])
    test_db.add(ScrapeLog(search_id=search.id, source="jobspy", jobs_found=1, new_jobs=1,
                          is_warning=False, duration_seconds=1.0))
    test_db.commit()

    rows = api_client.get("/api/scrape-log").json()
    row = next(r for r in rows if r["search_id"] == str(search.id))
    assert row["source_breakdown"] is None


def test_search_dict_has_no_source_errors_on_a_clean_run(test_db):
    from backend.api.routes_searches import _search_to_dict

    search = _search(test_db, ["indeed"])
    log = ScrapeLog(search_id=search.id, source="jobspy", jobs_found=9, new_jobs=2,
                    is_warning=False, duration_seconds=1.0,
                    source_breakdown={"indeed": {"seen": 9, "new": 2}})
    test_db.add(log)
    test_db.commit()
    assert _search_to_dict(search, last_log=log)["last_source_errors"] == []
