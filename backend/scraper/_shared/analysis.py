"""Shared inline-analysis helpers: consolidates check_job_h1b + apply_salary_to_job + apply_arrangement_to_job with non-fatal error handling so every scraper source calls them consistently."""
import logging
from backend.analyzer.h1b_checker import check_job_h1b, prefetch_company_h1b
from backend.analyzer.salary_extractor import apply_salary_to_job
from backend.analyzer.work_arrangement import apply_arrangement_to_job
from backend.analyzer.location import apply_location_to_job

logger = logging.getLogger("jobnavigator.scraper.analysis")


async def prefetch_h1b(companies) -> None:
    """Call before an ingest loop writes its first row (S2-01).

    The per-job H-1B check makes a live lookup only while the ingest session holds no
    uncommitted rows (an await with rows held could freeze the event loop), so the
    lookups for a batch's new companies happen here, up front, and the checks in the
    loop are cache hits. Non-fatal: on failure the jobs keep what the cache has."""
    try:
        await prefetch_company_h1b([c for c in companies if c])
    except Exception as e:
        logger.warning("H-1B prefetch failed, the jobs keep what the cache has: %s", e)


async def analyze_inline(job, db=None, h1b_median=None, arrangement=None) -> None:
    """Run h1b check + salary extraction + work-arrangement extraction inline on a Job; non-fatal, each step's errors are logged and swallowed independently. `arrangement` is the source's own structured field, when it has one."""
    try:
        await check_job_h1b(job, db=db)
    except Exception as e:
        logger.warning(
            "analyze_inline: h1b check failed for %s: %s",
            getattr(job, "id", "?"), e,
        )

    # Median salary comes from the VisaCache entry check_job_h1b just resolved and
    # stashed on the job, avoiding an extra lookup.
    if h1b_median is None:
        h1b_median = getattr(job, "_h1b_median", None)

    try:
        # Positional, like every other caller: the parameter is `company_h1b_median`, and the
        # old `h1b_median=` keyword raised TypeError on every job (logged, then swallowed).
        apply_salary_to_job(job, h1b_median)
    except Exception as e:
        logger.warning(
            "analyze_inline: salary extraction failed for %s: %s",
            getattr(job, "id", "?"), e,
        )

    try:
        apply_arrangement_to_job(job, structured=arrangement)
    except Exception as e:
        logger.warning(
            "analyze_inline: arrangement extraction failed for %s: %s",
            getattr(job, "id", "?"), e,
        )

    try:
        apply_location_to_job(job)
    except Exception as e:
        logger.warning(
            "analyze_inline: location parsing failed for %s: %s",
            getattr(job, "id", "?"), e,
        )
