"""A JobSpy board that hard-fails must not look like one that found nothing; jobspy swallows per-board failures into its own loggers, so these tests pin the per-source breakdown, warning flag and run summary."""
import logging
import sys
import types

import pytest

from backend.models.db import ScrapeLog, Search


def _board_logger(name):
    """A logger shaped like jobspy's own create_logger(): propagate=False, which is why a root-only capture handler never sees a board failure."""
    logger = logging.getLogger(name)
    logger.propagate = False
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        logger.addHandler(logging.NullHandler())
    return logger


def _fake_jobspy(rows, log_errors=(), level=logging.ERROR):
    """Install a stand-in jobspy module whose scrape_jobs returns rows and emits log_errors on non-propagating loggers, like the real library does.

    `level` is the level the board reports at. The Indeed scraper reports an HTTP
    failure at INFO, so a test that passes logging.ERROR proves nothing about it.
    An entry can carry its own level as a third item.
    """
    import pandas as pd

    # loggers must exist before _capture_source_errors() enumerates them
    boards = [_board_logger(entry[0]) for entry in log_errors]

    def scrape_jobs(**kwargs):
        for logger, entry in zip(boards, log_errors):
            logger.log(entry[2] if len(entry) > 2 else level, entry[1])
        return pd.DataFrame(rows)

    mod = types.ModuleType("jobspy")
    mod.scrape_jobs = scrape_jobs
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


def _search(db, sources):
    s = Search(name="ZZ Search", search_mode="keyword", active=True, sources=sources,
               search_term="program manager", title_include_keywords=[],
               title_exclude_keywords=[], company_filter=[], company_exclude=[])
    db.add(s)
    db.commit()
    return s


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
    """The 403 ZipRecruiter logs is captured and condensed to its status code."""
    from backend.scraper.sources.jobspy import GOOGLE_BLOCKED, _run_sync

    _fake_jobspy(
        [_row("indeed", "Program Manager", "Acme", "https://indeed.test/a")],
        log_errors=[
            ("JobSpy:ZipRecruiter", "ZipRecruiter response status code 403"),
            ("JobSpy:Google", GOOGLE_CURSOR_WARNING, logging.WARNING),
        ],
    )
    search = _search(test_db, ["indeed", "zip_recruiter", "google"])

    result = _run_sync(search)

    bd = result["source_breakdown"]
    assert bd["indeed"]["seen"] == 1
    assert bd["zip_recruiter"]["error"] == "403"
    assert bd["google"]["error"] == GOOGLE_BLOCKED
    # the overall run still succeeded — the failure lives per source
    assert result["error"] is None


# ── known blocks ────────────────────────────────────────────────────────────
# The literal texts python-jobspy 1.1.82 logs. jobspy/ziprecruiter/__init__.py
# logs a refused request at ERROR; jobspy/google/__init__.py logs the cursor
# warning at WARNING.
ZIP_FORBIDDEN_AA = (
    'ZipRecruiter response status code 403 with response: {"error_code":"forbidden aa",'
    '"error_message":"forbidden aa","request_id":"CFRAY:a3c92a7f9be26e1b-IAD","status_code":403}'
)
ZIP_FORBIDDEN_CF_WAF = (
    'ZipRecruiter response status code 403 with response: '
    '{"status_code":403,"error_code":"forbidden cf-waf","error_message":"Forbidden"}'
)
# Not measured here. The error code comes from upstream issue #190, the body shape is assumed.
ZIP_GEOBLOCKED_GDPR = (
    'ZipRecruiter response status code 403 with response: '
    '{"error_code":"geoblocked-gdpr","error_message":"geoblocked-gdpr","status_code":403}'
)
ZIP_UNKNOWN_403 = "ZipRecruiter response status code 403 with response: <html>Access denied</html>"
GOOGLE_CURSOR_WARNING = "initial cursor not found, try changing your query or there was at most 10 results"


@pytest.mark.parametrize("logger_name, level, message, expected", [
    ("JobSpy:ZipRecruiter", logging.ERROR, ZIP_FORBIDDEN_AA, "ZIP_RECRUITER_BLOCKED"),
    ("JobSpy:ZipRecruiter", logging.ERROR, ZIP_FORBIDDEN_CF_WAF, "ZIP_RECRUITER_BLOCKED"),
    ("JobSpy:Google", logging.WARNING, GOOGLE_CURSOR_WARNING, "GOOGLE_BLOCKED"),
    # A block nobody diagnosed keeps its current text.
    ("JobSpy:ZipRecruiter", logging.ERROR, ZIP_GEOBLOCKED_GDPR, "403"),
    ("JobSpy:ZipRecruiter", logging.ERROR, ZIP_UNKNOWN_403, "403"),
    # A signature counts only on its own board.
    ("JobSpy:Indeed", logging.WARNING, ZIP_FORBIDDEN_AA, "403"),
])
def test_a_known_block_signature_gets_a_stable_text(logger_name, level, message, expected):
    import backend.scraper.sources.jobspy as js

    _board_logger(logger_name)
    with js._capture_source_errors(["indeed", "zip_recruiter", "google"]) as capture:
        logging.getLogger(logger_name).log(level, message)
    assert list(capture.errors.values()) == [getattr(js, expected, expected)]


def test_google_that_returned_rows_records_no_error(test_db, monkeypatch):
    """jobspy logs the cursor warning also for a first page with up to 10 jobs; that board was not blocked."""
    from backend.scraper.sources.jobspy import _run_sync

    _fake_jobspy(
        [_row("google", "Program Manager", "Acme", "https://google.test/a")],
        log_errors=[("JobSpy:Google", GOOGLE_CURSOR_WARNING, logging.WARNING)],
    )
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
    from backend.scraper.sources.jobspy import GOOGLE_BLOCKED, ZIP_RECRUITER_BLOCKED

    _fake_jobspy(rows, log_errors=[
        ("JobSpy:ZipRecruiter", ZIP_FORBIDDEN_AA, logging.ERROR),
        ("JobSpy:Google", GOOGLE_CURSOR_WARNING, logging.WARNING),
    ])
    search = _search(test_db, ["indeed", "zip_recruiter", "google"])

    out = await test_search(str(search.id), db=test_db)

    assert out["source_errors"] == {
        "zip_recruiter": ZIP_RECRUITER_BLOCKED,
        "google": GOOGLE_BLOCKED,
    }
    assert out["source_breakdown"] == ({"indeed": 1} if rows else {})


# The capture handler attaches to every JobSpy logger in the process. The
# scheduler runs in the same process, so another run's board failure can reach
# this run's capture. Only the boards this search uses may be reported.

@pytest.mark.asyncio
async def test_the_test_run_ignores_a_failure_of_a_board_it_does_not_use(test_db):
    from backend.api.routes_searches import test_search
    from backend.scraper.sources.jobspy import ZIP_RECRUITER_BLOCKED, _capture_source_errors

    # The ZipRecruiter record stands for the overlapping scheduled run.
    _fake_jobspy([_row("indeed", "Program Manager", "Acme", "https://indeed.test/a")],
                 log_errors=[("JobSpy:ZipRecruiter", ZIP_FORBIDDEN_AA, logging.ERROR)])
    search = _search(test_db, ["linkedin", "indeed"])

    with _capture_source_errors(["zip_recruiter"]) as scheduled:
        out = await test_search(str(search.id), db=test_db)

    assert scheduled.errors == {"zip_recruiter": ZIP_RECRUITER_BLOCKED}
    assert out["source_errors"] == {}
    assert out["source_breakdown"] == {"indeed": 1}


def test_a_run_ignores_a_failure_of_a_board_it_does_not_use(test_db, monkeypatch):
    """The scheduled path merges captured errors with setdefault, so it would add the foreign board too."""
    from backend.scraper.sources.jobspy import ZIP_RECRUITER_BLOCKED, _capture_source_errors, _run_sync

    _fake_jobspy([_row("indeed", "Program Manager", "Acme", "https://indeed.test/a")],
                 log_errors=[("JobSpy:ZipRecruiter", ZIP_FORBIDDEN_AA, logging.ERROR)])

    with _capture_source_errors(["zip_recruiter"]) as other:
        result = _run_sync(_search(test_db, ["indeed"]))

    assert other.errors == {"zip_recruiter": ZIP_RECRUITER_BLOCKED}
    assert set(result["source_breakdown"]) == {"indeed"}
    assert "error" not in result["source_breakdown"]["indeed"]


# jobspy/indeed/__init__.py reports an HTTP failure through log.info, with this
# exact wording. A WARNING threshold never saw it, so no Indeed failure could
# reach source_breakdown at all.
INDEED_INFO_FAILURE = (
    "responded with status code: 503 "
    "(submit GitHub issue if this appears to be a bug)"
)


def test_breakdown_records_an_indeed_failure_reported_at_info(test_db, monkeypatch):
    """The real Indeed failure path: INFO level, the library's own message text."""
    from backend.scraper.sources.jobspy import _run_sync

    _fake_jobspy(
        [_row("linkedin", "Product Manager", "Beta", "https://linkedin.test/c")],
        log_errors=[("JobSpy:Indeed", INDEED_INFO_FAILURE)],
        level=logging.INFO,
    )
    search = _search(test_db, ["indeed", "linkedin"])

    result = _run_sync(search)

    bd = result["source_breakdown"]
    assert bd["indeed"]["error"] == "503"
    assert bd["linkedin"]["seen"] == 1


def test_ordinary_info_chatter_stays_out_of_the_breakdown(test_db, monkeypatch):
    """Lowering the threshold must not turn every progress line into an error; jobspy logs "finished scraping" at INFO on every successful board."""
    from backend.scraper.sources.jobspy import _run_sync

    _fake_jobspy(
        [_row("indeed", "Program Manager", "Acme", "https://indeed.test/a")],
        log_errors=[("JobSpy:Indeed", "finished scraping")],
        level=logging.INFO,
    )
    search = _search(test_db, ["indeed"])

    result = _run_sync(search)

    assert "error" not in result["source_breakdown"]["indeed"]
    assert result["source_breakdown"]["indeed"]["seen"] == 1


def test_a_warning_record_still_counts_whatever_it_says(test_db, monkeypatch):
    """The content filter gates INFO only — WARNING and above keep the old contract."""
    from backend.scraper.sources.jobspy import _capture_source_errors

    _board_logger("JobSpy:ZipRecruiter")
    with _capture_source_errors(["zip_recruiter"]) as capture:
        logging.getLogger("JobSpy:ZipRecruiter").warning("bad proxy")
    assert capture.errors == {"zip_recruiter": "bad proxy"}


def test_board_logger_does_not_propagate_to_root():
    """Pins the library behavior the capture works around: while it holds, a root-only handler is a no-op against a real board failure."""
    from backend.scraper.sources.jobspy import _SourceLogCapture

    board = _board_logger("JobSpy:ZipRecruiter")
    assert board.propagate is False

    root_only = _SourceLogCapture()
    logging.getLogger().addHandler(root_only)
    try:
        board.error("ZipRecruiter response status code 403")
    finally:
        logging.getLogger().removeHandler(root_only)
    assert root_only.errors == {}


def test_capture_attaches_to_the_non_propagating_board_logger():
    """The context manager itself, exercised directly against the real condition."""
    from backend.scraper.sources.jobspy import _capture_source_errors

    _board_logger("JobSpy:ZipRecruiter")
    with _capture_source_errors(["indeed", "zip_recruiter"]) as capture:
        logging.getLogger("JobSpy:ZipRecruiter").error("ZipRecruiter response status code 403")
    assert capture.errors == {"zip_recruiter": "403"}


def test_capture_handler_is_removed_afterwards(test_db):
    """No logger — root or board — may keep collecting after the call returns."""
    from backend.scraper.sources.jobspy import _run_sync

    board = _board_logger("JobSpy:Indeed")
    before_root = len(logging.getLogger().handlers)
    before_board = len(board.handlers)

    _fake_jobspy([], log_errors=[("JobSpy:Indeed", "boom 500")])
    _run_sync(_search(test_db, ["indeed"]))

    assert len(logging.getLogger().handlers) == before_root
    assert len(board.handlers) == before_board


def test_condense_error_keeps_non_http_text():
    from backend.scraper.sources.jobspy import _condense_error, _site_key

    assert _condense_error("ZipRecruiter response status code 403") == "403"
    assert _condense_error("initial cursor not found") == "initial cursor not found"
    assert _site_key("JobSpy:ZipRecruiter") == "zip_recruiter"
    assert _site_key("JobSpy:Google") == "google"
    assert _site_key("uvicorn.error") is None


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
                "google": {"seen": 0, "new": 0, "error": "initial cursor not found"},
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
    assert "google: initial cursor not found" in summary


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
