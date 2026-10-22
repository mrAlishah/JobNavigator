"""Tests for sources/levelsfyi.py."""
import asyncio


def test_exposes_run_and_scrape():
    from backend.scraper.sources import levelsfyi
    assert hasattr(levelsfyi, "run")
    # _scrape_levelsfyi is used externally by routes_searches.py:409 — keep as-is
    assert hasattr(levelsfyi, "_scrape_levelsfyi")


def test_run_is_async():
    from backend.scraper.sources import levelsfyi
    assert asyncio.iscoroutinefunction(levelsfyi.run)


def test_is_levelsfyi_detects_levels():
    from backend.scraper.sources.levelsfyi import _is_levelsfyi
    assert _is_levelsfyi("https://www.levels.fyi/jobs")


def test_is_levelsfyi_rejects_non_levels():
    from backend.scraper.sources.levelsfyi import _is_levelsfyi
    assert not _is_levelsfyi("https://boards.greenhouse.io/acme")


def test_levelsfyi_job_id_keeps_the_whole_alphanumeric_id():
    """Since 2026-10-09 levels.fyi ids are base-36 ("2ci7r6weraiqb"); reading only the leading
    digits stored "jobId=2", a 404 page that scoring could never parse."""
    from backend.scraper.sources.levelsfyi import _levelsfyi_job_id
    assert _levelsfyi_job_id("/jobs?jobId=2ci7r6weraiqb") == "2ci7r6weraiqb"
    assert _levelsfyi_job_id("/jobs?jobId=0748ffyywjm40&utm=x") == "0748ffyywjm40"
    assert _levelsfyi_job_id("https://www.levels.fyi/jobs?jobId=113365928120328902") == "113365928120328902"
    assert _levelsfyi_job_id("/jobs?standardJobId=abc") is None
    assert _levelsfyi_job_id("/jobs?jobId=") is None


def test_parse_levelsfyi_salary_parses_range():
    from backend.scraper.sources.levelsfyi import _parse_levelsfyi_salary
    # Return shape: (location, work_arrangement, salary_min, salary_max)
    result = _parse_levelsfyi_salary("San Francisco \u00b7 Remote \u00b7 $100K - $150K")
    assert isinstance(result, tuple)
    assert len(result) == 4
    location, work_arrangement, salary_min, salary_max = result
    assert location == "San Francisco"
    assert work_arrangement == "Remote"
    assert salary_min == 100000
    assert salary_max == 150000
