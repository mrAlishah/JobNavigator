"""The employer's own description for jobs a board only summarises.

Jobright stores its own AI summary of a job, so two copies of one role (another city, a
repost) never read word for word alike and `same_role.py` cannot prove them. When the job's
link is the employer's (not a board), fetch the description there and keep it instead; the
board's summary stays when the fetch fails or gives too little text.
"""
import asyncio
import logging

from backend.scraper._shared.link_kinds import BOARD, link_kind

logger = logging.getLogger("jobnavigator.employer_text")

MIN_CHARS = 200   # a fetch shorter than this is a shell or an error page, not a description


async def employer_descriptions(jobs: list[dict], landed: dict | None = None,
                                max_concurrent: int = 5) -> int:
    """Replace each job dict's `description` with the employer's text in place. `landed` maps a
    short link to where it lands (resolve_short_links). Returns how many were replaced."""
    from backend.scraper.ats import _descriptions

    landed = landed or {}
    sem = asyncio.Semaphore(max_concurrent)

    async def one(j: dict) -> bool:
        link = landed.get(j.get("url")) or j.get("url")
        if not link or link_kind(link, None) == BOARD:
            return False
        async with sem:
            try:
                text = await _descriptions._fetch_job_description(link, job=j)
            except Exception as e:  # noqa: BLE001 - the summary stays
                logger.info(f"employer text: {link}: {e}")
                return False
        if not text or len(text.strip()) < MIN_CHARS:
            return False
        j["description"] = text
        return True

    done = await asyncio.gather(*(one(j) for j in jobs))
    return sum(done)


MAX_MISSES = 3   # in a row: the board has stopped answering (rate limit), so stop asking


async def missing_descriptions(jobs: list[dict], pause: float = 1.5) -> int:
    """Fill `description` in place for job dicts that have none, from their own listing
    (JobSpy's LinkedIn rows: LinkedIn's public job page, no account). One at a time, `pause`
    seconds apart. Returns how many were filled."""
    from backend.scraper.ats import _descriptions

    filled = misses = 0
    todo = [j for j in jobs if not j.get("description") and j.get("url")]
    for i, j in enumerate(todo):
        if i and pause:
            await asyncio.sleep(pause)
        try:
            text = await _descriptions._fetch_job_description(j["url"])
        except Exception as e:  # noqa: BLE001 - the job keeps no description
            logger.info(f"listing text: {j['url']}: {e}")
            text = None
        if text and len(text.strip()) >= MIN_CHARS:
            j["description"] = text
            filled += 1
            misses = 0
            continue
        misses += 1
        if misses >= MAX_MISSES:
            logger.info(f"listing text: {MAX_MISSES} misses in a row, stopping after {i + 1}/{len(todo)}")
            break
    return filled
