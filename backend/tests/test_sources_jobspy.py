"""Tests for sources/jobspy.py — JobSpy multi-board keyword scrape entry point."""
import pytest
from unittest.mock import AsyncMock, MagicMock


def test_sources_jobspy_exposes_run():
    """Module has a `run` function as its public entry point."""
    from backend.scraper.sources import jobspy
    assert hasattr(jobspy, "run")


@pytest.mark.asyncio
async def test_run_handles_empty_sources():
    """Search with no `sources` configured returns an error dict without raising."""
    from backend.scraper.sources.jobspy import run

    class FakeSearch:
        id = "s-1"
        name = "Empty Search"
        sources = []
        title_include_keywords = []
        title_exclude_keywords = []
        company_exclude = []
        location = None
        distance = None
        max_results = 50
        hours_old = 168
        require_salary = False

    result = await run(FakeSearch())
    assert isinstance(result, dict)
    assert "jobs_found" in result or "error" in result


def test_structured_arrangement_reads_only_tagged_boards():
    """Indeed, Glassdoor and ZipRecruiter tag remote themselves (jobspy 1.4.1); LinkedIn and Google guess."""
    from backend.scraper.sources.jobspy import _structured_arrangement

    assert _structured_arrangement({"is_remote": True}, "indeed") is True
    assert _structured_arrangement({"is_remote": True}, "glassdoor") is True
    assert _structured_arrangement({"is_remote": True}, "zip_recruiter") is True
    assert _structured_arrangement({"is_remote": True}, "linkedin") is None
    assert _structured_arrangement({"is_remote": True}, "google") is None
    # False is "not tagged remote", not "on-site"
    assert _structured_arrangement({"is_remote": False}, "indeed") is None
    assert _structured_arrangement({"is_remote": float("nan")}, "indeed") is None
    assert _structured_arrangement({"work_from_home_type": "Hybrid", "is_remote": False}, "naukri") == "Hybrid"
