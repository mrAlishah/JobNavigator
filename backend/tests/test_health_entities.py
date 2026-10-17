"""GET /api/health/entities — flag active companies/searches with failing scrapes."""
from datetime import datetime, timezone, timedelta

from backend.models.db import Company, Search, ScrapeLog


def _log(company_id=None, search_id=None, error=None, is_warning=False, ago_min=0):
    return ScrapeLog(
        company_id=company_id, search_id=search_id, source="test",
        jobs_found=0, new_jobs=0, error=error, is_warning=is_warning,
        duration_seconds=0.0,
        ran_at=datetime.now(timezone.utc) - timedelta(minutes=ago_min),
    )


def _run():
    from backend.main import get_failing_entities
    return get_failing_entities()


def test_company_flagged_on_three_empty(test_db):
    c = Company(name="EmptyCo", active=True)
    test_db.add(c)
    test_db.commit()
    for i in range(3):
        test_db.add(_log(company_id=c.id, is_warning=True, ago_min=i))
    test_db.commit()

    r = _run()
    row = next((x for x in r["companies"] if x["name"] == "EmptyCo"), None)
    assert row is not None
    assert "No results" in row["reason"]


def test_company_flagged_on_three_errors_uses_latest_error(test_db):
    c = Company(name="BrokenCo", active=True)
    test_db.add(c)
    test_db.commit()
    test_db.add(_log(company_id=c.id, error="old error", ago_min=5))
    test_db.add(_log(company_id=c.id, error="mid error", ago_min=3))
    test_db.add(_log(company_id=c.id, error="latest 404", ago_min=0))
    test_db.commit()

    r = _run()
    row = next((x for x in r["companies"] if x["name"] == "BrokenCo"), None)
    assert row is not None
    assert row["reason"] == "latest 404"  # most recent actual error


def test_not_flagged_when_a_recent_scrape_succeeded(test_db):
    c = Company(name="OkCo", active=True)
    test_db.add(c)
    test_db.commit()
    test_db.add(_log(company_id=c.id, is_warning=True, ago_min=2))
    test_db.add(_log(company_id=c.id, is_warning=True, ago_min=1))
    test_db.add(_log(company_id=c.id, is_warning=False, error=None, ago_min=0))  # success
    test_db.commit()

    r = _run()
    assert all(x["name"] != "OkCo" for x in r["companies"])


def test_not_flagged_with_fewer_than_window_logs(test_db):
    c = Company(name="NewCo", active=True)
    test_db.add(c)
    test_db.commit()
    test_db.add(_log(company_id=c.id, is_warning=True, ago_min=1))
    test_db.add(_log(company_id=c.id, is_warning=True, ago_min=0))  # only 2
    test_db.commit()

    r = _run()
    assert all(x["name"] != "NewCo" for x in r["companies"])


def test_inactive_company_not_flagged(test_db):
    c = Company(name="OffCo", active=False)
    test_db.add(c)
    test_db.commit()
    for i in range(3):
        test_db.add(_log(company_id=c.id, error="boom", ago_min=i))
    test_db.commit()

    r = _run()
    assert all(x["name"] != "OffCo" for x in r["companies"])


def test_search_flagged_and_count(test_db):
    s = Search(name="DeadSearch", search_mode="keyword", active=True)
    test_db.add(s)
    test_db.commit()
    for i in range(3):
        test_db.add(_log(search_id=s.id, error="429", ago_min=i))
    test_db.commit()

    r = _run()
    assert any(x["name"] == "DeadSearch" for x in r["searches"])
    assert r["count"] == len(r["companies"]) + len(r["searches"])


# ── the reason text when one board returned nothing ─────────────────────────
# is_warning also fires for "one board returned nothing while the others
# worked". These tests pin which sentence the reason carries: the board name
# when one board was quiet across the whole window, the original
# "No results in the last N scrapes" in every other case.

def _quiet_log(search_id, quiet_boards, ago_min, jobs_found=40):
    """A run that found jobs, where each named board returned no rows."""
    row = _log(search_id=search_id, is_warning=True, ago_min=ago_min)
    row.jobs_found = jobs_found
    row.new_jobs = 0
    breakdown = {"linkedin": {"seen": jobs_found, "new": 0, "returned": jobs_found}}
    for board in quiet_boards:
        breakdown[board] = {"seen": 0, "new": 0, "returned": 0}
    row.source_breakdown = breakdown
    return row


def _reason_for(name):
    return next((x["reason"] for x in _run()["searches"] if x["name"] == name), None)


def test_reason_names_a_board_quiet_across_the_whole_window(test_db):
    s = Search(name="QuietIndeed", search_mode="keyword", active=True)
    test_db.add(s)
    test_db.commit()
    for i in range(3):
        test_db.add(_quiet_log(s.id, ["indeed"], ago_min=i))
    test_db.commit()

    assert _reason_for("QuietIndeed") == "Indeed returned nothing in the last 3 scrapes"


def test_reason_names_every_board_quiet_across_the_whole_window(test_db):
    s = Search(name="QuietTwo", search_mode="keyword", active=True)
    test_db.add(s)
    test_db.commit()
    for i in range(3):
        test_db.add(_quiet_log(s.id, ["indeed", "google"], ago_min=i))
    test_db.commit()

    reason = _reason_for("QuietTwo")
    assert "Indeed returned nothing" in reason
    assert "Google returned nothing" in reason
    assert reason.endswith("in the last 3 scrapes")


def test_reason_names_no_board_when_the_quiet_one_changes(test_db):
    """Indeed, then Google, then Indeed: no board was quiet throughout.

    The choice: name none, and fall back to the original sentence. A board that
    did deliver rows must never be named. The fallback can be false about a run
    that found 40 jobs; the user accepts that cost and keeps the wording.
    """
    s = Search(name="Alternating", search_mode="keyword", active=True)
    test_db.add(s)
    test_db.commit()
    test_db.add(_quiet_log(s.id, ["indeed"], ago_min=2))
    test_db.add(_quiet_log(s.id, ["google"], ago_min=1))
    test_db.add(_quiet_log(s.id, ["indeed"], ago_min=0))
    test_db.commit()

    reason = _reason_for("Alternating")
    assert reason == "No results in the last 3 scrapes"
    assert "Indeed" not in reason and "Google" not in reason


def test_reason_keeps_the_old_wording_without_board_evidence(test_db):
    """A row with no source_breakdown is every non-JobSpy source and every row an older build wrote."""
    s = Search(name="PlainEmpty", search_mode="levels_fyi", active=True)
    test_db.add(s)
    test_db.commit()
    for i in range(3):
        test_db.add(_log(search_id=s.id, is_warning=True, ago_min=i))
    test_db.commit()

    assert _reason_for("PlainEmpty") == "No results in the last 3 scrapes"


def test_a_refused_board_still_wins_over_the_quiet_wording(test_db):
    """A board that refused the request is reported on the last run alone — that branch runs first and must keep doing so."""
    s = Search(name="RefusedBoard", search_mode="keyword", active=True)
    test_db.add(s)
    test_db.commit()
    row = _quiet_log(s.id, ["indeed"], ago_min=0)
    row.source_breakdown["zip_recruiter"] = {"seen": 0, "new": 0, "returned": 0, "error": "403"}
    test_db.add(row)
    test_db.commit()

    assert _reason_for("RefusedBoard") == "ZipRecruiter failed (403) on the last run"


def test_inactive_search_not_flagged(test_db):
    """A paused search was switched off deliberately, so its failed history must not drive the rail dot or header count."""
    s = Search(name="OffSearch", search_mode="keyword", active=False)
    test_db.add(s)
    test_db.commit()
    for i in range(3):
        test_db.add(_log(search_id=s.id, error="boom", ago_min=i))
    test_db.commit()

    r = _run()
    assert all(x["name"] != "OffSearch" for x in r["searches"])
    assert r["count"] == 0


# ── acknowledgement ──────────────────────────────────────────────────────────

def test_acknowledged_company_not_flagged(test_db):
    c = Company(name="AckCo", active=True)
    test_db.add(c)
    test_db.commit()
    for i in range(3):
        test_db.add(_log(company_id=c.id, error="404", ago_min=i + 1))
    test_db.commit()
    # acknowledged after the newest of those runs
    c.warning_acknowledged_at = datetime.now(timezone.utc)
    test_db.commit()

    r = _run()
    assert all(x["name"] != "AckCo" for x in r["companies"])


def test_acknowledged_search_not_flagged(test_db):
    s = Search(name="AckSearch", search_mode="keyword", active=True)
    test_db.add(s)
    test_db.commit()
    for i in range(3):
        test_db.add(_log(search_id=s.id, error="429", ago_min=i + 1))
    test_db.commit()
    s.warning_acknowledged_at = datetime.now(timezone.utc)
    test_db.commit()

    r = _run()
    assert all(x["name"] != "AckSearch" for x in r["searches"])


def test_failed_run_after_acknowledge_flags_again(test_db):
    """No expiry timer: the warning comes back the moment a run *newer* than the acknowledgement fails."""
    c = Company(name="ReAckCo", active=True)
    test_db.add(c)
    test_db.commit()
    for i in range(3):
        test_db.add(_log(company_id=c.id, error="404", ago_min=i + 10))
    test_db.commit()
    c.warning_acknowledged_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    test_db.commit()
    assert all(x["name"] != "ReAckCo" for x in _run()["companies"]), "acknowledged, so quiet"

    test_db.add(_log(company_id=c.id, error="still 404", ago_min=0))   # newer than the ack
    test_db.commit()

    row = next((x for x in _run()["companies"] if x["name"] == "ReAckCo"), None)
    assert row is not None
    assert row["reason"] == "still 404"


def test_acknowledge_also_covers_a_failed_board_on_the_last_run(test_db):
    """The single-board branch (e.g. "ZipRecruiter failed") honours the acknowledgement too, so it doesn't stay amber forever."""
    s = Search(name="BoardSearch", search_mode="keyword", active=True)
    test_db.add(s)
    test_db.commit()
    log = _log(search_id=s.id, ago_min=1)
    log.source_breakdown = {"zip_recruiter": {"error": "403"}}
    test_db.add(log)
    test_db.commit()
    assert any(x["name"] == "BoardSearch" for x in _run()["searches"])

    s.warning_acknowledged_at = datetime.now(timezone.utc)
    test_db.commit()
    assert all(x["name"] != "BoardSearch" for x in _run()["searches"])
