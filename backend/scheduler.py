"""APScheduler — reads all timing config from settings DB table. No hardcoded schedules.

Accounts (MT-7). Each job is one of:
- per account: runs once per account under `acting_as(account)` via `for_each_account()`
  (its own JobRun, ActivityLog and LlmCallLog rows, its own dedup key; one account failing
  does not stop the others): daily_digest, auto_reject, job_cleanup, email_check (Gmail
  accounts: the owner plus members with a connected token, MT-19), and the saved-job
  scoring pass of scrape_all.
- install-level, run once as the owner (`as_owner`): db_backup, h1b_refresh (reads every
  account's companies and jobs), the scrape-health system alert (owner's chat).
- shared fan-out (MT-8): the scraping part of scrape_all — one install-level run (owner's
  JobRun) that fetches each target once and ingests it per subscribing account.
LinkedIn session refresh is install-level too but has no schedule (manual trigger only).
"""
import logging
from datetime import datetime, timezone, timedelta
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger

from backend.accounts.background import for_each_account, gmail_account_ids
from backend.accounts.context import as_owner, run_in_clean_context, scheduled_job
from backend.models.db import SessionLocal
from backend.accounts.settings import get_setting as _get_setting

logger = logging.getLogger("jobnavigator.scheduler")

scheduler = AsyncIOScheduler()


def scheduler_disabled() -> bool:
    """QA F9 / T2-01: env switch JN_SCHEDULER_DISABLED=1 (also true/yes/on) keeps the
    scheduler from starting, so a throwaway copy of real data never scrapes, emails,
    digests or backs up as the real install. Manual triggers still work."""
    import os
    return os.environ.get("JN_SCHEDULER_DISABLED", "").strip().lower() in ("1", "true", "yes", "on")


def get_setting(db, key, default=None):
    """Read a setting value from DB (as the current account sees it)."""
    return _get_setting(db, key, default)


def _int_setting(db, key: str) -> int:
    """A minutes-interval setting, or 0 (= disabled) if the stored value is junk; a bad value
    must not raise, since configure_scheduler() runs inside the app's lifespan and would take
    the whole backend down with it."""
    raw = get_setting(db, key, "0")
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        logger.error(f"Setting '{key}' is not a whole number: {raw!r} — job disabled until it is fixed")
        return 0
    if value < 0:
        logger.error(f"Setting '{key}' is negative: {raw!r} — job disabled until it is fixed")
        return 0
    return value


def configure_scheduler():
    """Read all intervals/crons from settings and (re)configure scheduler jobs; called at startup
    and after any settings update. Each value is read defensively so one bad row only skips its
    own job, never the rest of the schedule."""
    db = SessionLocal()
    try:
        scrape_interval = _int_setting(db, "scrape_interval_minutes")
        email_interval = _int_setting(db, "email_check_interval_minutes")
        backup_cron = str(get_setting(db, "backup_cron", "") or "").strip()
        digest_cron = str(get_setting(db, "digest_cron", "") or "").strip()
        h1b_cron = str(get_setting(db, "h1b_cron", "") or "").strip()
        cleanup_cron = str(get_setting(db, "cleanup_cron", "") or "").strip()
        reject_cron = str(get_setting(db, "reject_cron", "") or "").strip()
    finally:
        db.close()

    # QA F4 / R1-01: add_job() wakes the scheduler with call_soon_threadsafe, which
    # captures the caller's contextvars, and every later fire inherits them. The caller
    # may be a member's settings PATCH (unscoped, owner-unlocked), so the jobs are
    # registered from an empty Context. (Each entry point also resets the account
    # context itself, see `scheduled_job`.)
    run_in_clean_context(
        _register_jobs, scrape_interval, email_interval,
        backup_cron, digest_cron, h1b_cron, cleanup_cron, reject_cron)


OVERDUE_DELAY = timedelta(minutes=2)


def _last_scheduled_scrape():
    """Start of the last scheduled scrape that ran or is running (a failed one, e.g. cut off
    by a shutdown, does not count), or None."""
    from backend.accounts.context import unscoped
    from backend.models.db import JobRun
    db = SessionLocal()
    try:
        with unscoped():   # the scheduled scrape's JobRun is the owner's; this is install-level
            row = (db.query(JobRun.started_at)
                   .filter(JobRun.job_type == "scrape_all", JobRun.trigger == "scheduler",
                           JobRun.status.in_(("completed", "running")))
                   .order_by(JobRun.started_at.desc()).first())
    finally:
        db.close()
    if not row or not row[0]:
        return None
    ts = row[0]
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def next_scrape_time(interval_minutes: int, now=None):
    """The interval counts from the last scheduled scrape, not from when the backend started:
    a restart (the PC is off at night) would otherwise start the count again every time, and
    an interval longer than the uptime would never fire. Overdue (or never run): two minutes
    from now."""
    now = now or datetime.now(timezone.utc)
    try:
        last = _last_scheduled_scrape()
    except Exception as e:
        logger.error(f"Could not read the last scheduled scrape: {e}")
        last = None
    due = last + timedelta(minutes=interval_minutes) if last else now
    return max(due, now + OVERDUE_DELAY)


TELEMETRY_DELAY = timedelta(minutes=3)


def next_telemetry_time(now=None):
    """A day after the last telemetry send (restarts do not send again), or three minutes from
    now when that is past or nothing was sent yet."""
    from backend.accounts.context import unscoped
    from backend.accounts.settings import get_install_setting
    from backend.telemetry.level import LAST_SENT_KEY
    now = now or datetime.now(timezone.utc)
    last = None
    try:
        with unscoped():
            db = SessionLocal()
            try:
                raw = get_install_setting(db, LAST_SENT_KEY)
            finally:
                db.close()
        last = datetime.fromisoformat(raw) if raw else None
    except Exception as e:
        logger.error(f"Could not read the last telemetry send: {e}")
    due = last + timedelta(days=1) if last else now
    return max(due, now + TELEMETRY_DELAY)


@scheduled_job
async def run_telemetry_daily():
    from backend.telemetry.umami import send_daily
    await send_daily()


def _register_jobs(scrape_interval, email_interval, backup_cron, digest_cron, h1b_cron,
                   cleanup_cron, reject_cron):
    def _add_cron_job(func, job_id, cron_expr):
        """Parse a 5-field cron expression and add to scheduler. Empty = skip.
        Install-level jobs carry `as_owner` themselves; per-account jobs set their own
        account context for each account."""
        if not cron_expr:
            return
        try:
            parts = cron_expr.split()
            if len(parts) == 5:
                scheduler.add_job(
                    func,
                    CronTrigger(minute=parts[0], hour=parts[1], day=parts[2], month=parts[3], day_of_week=parts[4]),
                    id=job_id,
                    replace_existing=True,
                )
            else:
                logger.error(f"Invalid cron for {job_id}: '{cron_expr}' (need 5 fields) — job disabled until it is fixed")
        except Exception as e:
            logger.error(f"Invalid cron for {job_id}: '{cron_expr}': {e} — job disabled until it is fixed")

    scheduler.remove_all_jobs()

    # Interval-based jobs (0 = disabled)
    if scrape_interval > 0:
        # Scraping runs as the owner until MT-8 (shared-scrape fan-out); run_all_scrapes
        # sets its own contexts (owner scrape, then the per-account scoring pass).
        scheduler.add_job(
            run_all_scrapes,
            IntervalTrigger(minutes=scrape_interval),
            id="scrape_all",
            replace_existing=True,
            next_run_time=next_scrape_time(scrape_interval),
        )

    if email_interval > 0:
        scheduler.add_job(
            run_email_check,  # per account (Gmail accounts)
            IntervalTrigger(minutes=email_interval),
            id="email_check",
            replace_existing=True,
        )

    # Telemetry (telemetry/umami.py): sends only what the owner allowed, else does nothing.
    scheduler.add_job(
        run_telemetry_daily,
        IntervalTrigger(minutes=24 * 60),
        id="telemetry_daily",
        replace_existing=True,
        next_run_time=next_telemetry_time(),
    )

    # Cron-based jobs (empty = disabled)
    _add_cron_job(run_db_backup, "db_backup", backup_cron)                   # install-level
    _add_cron_job(send_daily_digest, "daily_digest", digest_cron)            # per account
    _add_cron_job(refresh_h1b_data, "h1b_refresh", h1b_cron)                 # install-level
    _add_cron_job(run_job_cleanup_auto, "job_cleanup", cleanup_cron)         # per account
    _add_cron_job(run_auto_reject, "auto_reject", reject_cron)               # per account

    logger.info(
        f"Scheduler configured: scrape every {scrape_interval}m, "
        f"email every {email_interval}m, {len(scheduler.get_jobs())} total jobs"
    )


# ── Run summaries ───────────────────────────────────────────────────────────
# JobRun.result_summary is what Stats > Run history shows next to a run. These read back rows
# the run itself just wrote, so a summary can never disagree with the log.


def _scrape_summary(since, all_accounts: bool = False, exclude_accounts=()) -> str:
    """The Run-history line of a scrape run. `all_accounts=True` (the scheduled scrape_all,
    one install-level run since MT-8) reads every account's ScrapeLog rows and, when more
    than one account ran, appends per-account new counts; with one account the line is
    exactly the single-account one. `exclude_accounts`: accounts this run left out (their
    manual run's rows are not this run's)."""
    from contextlib import nullcontext
    from backend.accounts.context import unscoped
    from backend.models.db import Account, ScrapeLog, Search, Company
    db = SessionLocal()
    try:
        with (unscoped() if all_accounts else nullcontext()):
            rows = db.query(ScrapeLog).filter(ScrapeLog.ran_at >= since).all()
            if exclude_accounts:
                rows = [r for r in rows if r.account_id not in set(exclude_accounts)]
            # A paused search / inactive company is not a problem to report — it was switched off
            # deliberately. Its row still counts toward "N sources ran" but not toward attention counts.
            off_searches = {s.id for s in db.query(Search).filter(Search.active == False).all()}
            off_companies = {c.id for c in db.query(Company).filter(Company.active == False).all()}
        if not rows:
            return "No sources ran"
        per_account = {}
        for r in rows:
            per_account[r.account_id] = per_account.get(r.account_id, 0) + (r.new_jobs or 0)
        account_part = ""
        if all_accounts and len(per_account) > 1:
            names = {a.id: a.name for a in db.query(Account).all()}
            account_part = "per account: " + ", ".join(
                f"{names.get(acc, 'unknown')} +{n}" for acc, n in per_account.items())
        return _format_scrape_summary(rows, off_searches, off_companies, account_part)
    finally:
        db.close()


def _format_scrape_summary(rows, off_searches, off_companies, account_part: str = "") -> str:
    """The summary line from ScrapeLog rows (see _scrape_summary)."""
    from backend.scraper.orchestrator import empty_sources, source_errors
    if not rows:
        return "No sources ran"
    def _live(r):
        return not (
            (r.search_id is not None and r.search_id in off_searches)
            or (r.company_id is not None and r.company_id in off_companies)
        )

    live = [r for r in rows if _live(r)]
    found = sum(r.new_jobs or 0 for r in rows)
    failed = sum(1 for r in live if r.error)
    # is_warning has three causes and they are not the same thing. A refused
    # board, a board that returned nothing while the others worked, and a run
    # that found nothing each get their own count — a run with 40 jobs from
    # LinkedIn and 0 from Indeed is not "empty".
    bad_source = {r.id for r in live if not r.error and source_errors(r.source_breakdown)}
    quiet_source = {r.id for r in live if not r.error and r.id not in bad_source
                    and empty_sources(r.source_breakdown)}
    warned = sum(1 for r in live if r.is_warning and not r.error
                 and r.id not in bad_source and r.id not in quiet_source)
    parts = [f"{len(rows)} source{'' if len(rows) == 1 else 's'}", f"+{found} new"]
    if failed:
        parts.append(f"{failed} failed")
    if bad_source:
        parts.append(f"{len(bad_source)} with a failed board")
    if quiet_source:
        parts.append(f"{len(quiet_source)} with a board that returned nothing")
    if warned:
        parts.append(f"{warned} empty")
    if account_part:
        parts.append(account_part)
    return " - ".join(parts)


def own_scrape_summaries(db, windows: list) -> list:
    """QA V-03: for each (started, finished) window of an install-level scheduled
    scrape_all, the summary line of the CURRENT account's own ScrapeLog rows in it
    (scoped reads only — a member sees its own part of the run, nothing of another
    account's). `finished` None = still running (up to now)."""
    from backend.models.db import ScrapeLog, Search, Company
    if not windows:
        return []

    def _utc(ts):
        if ts is not None and ts.tzinfo is None:   # SQLite / legacy rows are naive UTC
            return ts.replace(tzinfo=timezone.utc)
        return ts

    now = datetime.now(timezone.utc)
    spans = [(_utc(s), _utc(f) or now) for s, f in windows]
    lo = min(s for s, _ in spans)
    hi = max(f for _, f in spans)
    rows = db.query(ScrapeLog).filter(ScrapeLog.ran_at >= lo, ScrapeLog.ran_at <= hi).all()
    off_searches = {s.id for s in db.query(Search).filter(Search.active == False).all()}  # noqa: E712
    off_companies = {c.id for c in db.query(Company).filter(Company.active == False).all()}  # noqa: E712
    out = []
    for s, f in spans:
        mine = [r for r in rows if r.ran_at is not None and s <= _utc(r.ran_at) <= f]
        out.append(_format_scrape_summary(mine, off_searches, off_companies))
    return out


def _activity_summary(since, log_type: str, noun: str, plural: str = None) -> str:
    """Count the activity-log rows a run produced, e.g. "1 reply" / "3 replies"; pass an explicit
    `plural` whenever it isn't just the noun plus an s."""
    from backend.models.db import ActivityLog
    db = SessionLocal()
    try:
        n = db.query(ActivityLog).filter(
            ActivityLog.created_at >= since, ActivityLog.type == log_type
        ).count()
        if not n:
            return ""
        return f"{n} {noun}" if n == 1 else f"{n} {plural or noun + 's'}"
    finally:
        db.close()


# ── Scheduled jobs ──────────────────────────────────────────────────────────
@scheduled_job
@as_owner
async def run_all_scrapes():
    """One install-level run (its JobRun is the owner's): run_all() is the shared-scrape
    fan-out over every account — one fetch per target, ingest per subscribing account
    (MT-8) — and the summary carries per-account counts. The saved-job scoring pass runs
    per account."""
    from backend.job_monitor import (
        tracked_run, JobAlreadyRunningError, SCHEDULED_SCRAPE_SCOPE, manual_scrape_scopes,
        mark_run_failed,
    )
    from backend.accounts.background import all_account_ids
    try:
        # all_accounts (QA F3 / R2-08): it ingests into every subscribing account, so an
        # account removal treats it as busy and it does not start during one.
        # QA F6 / R4-02: its own scope key, so the owner's manual Run all ("scrape_all")
        # no longer makes every member's tick skip; while it is live, manual scrapes are
        # refused with 409 scheduled_scrape_running (R4-01).
        async with tracked_run("scrape_all", "scheduler", scope_key=SCHEDULED_SCRAPE_SCOPE,
                               all_accounts=True) as run:
            logger.info("Running all scrapes...")
            started = datetime.now(timezone.utc)
            # Manual scrapes already in flight: their accounts (Run all) / companies /
            # searches are left out of this tick and named in the summary.
            busy = manual_scrape_scopes()
            ids = all_account_ids()
            # (ids == [None] when no accounts are readable: the owner fallback.)
            skipped_accounts = [a for a in ids if a in busy.accounts]
            run_ids = [a for a in ids if a not in skipped_accounts] if skipped_accounts else None
            errors: list = []
            fatal = None
            from backend.scraper.orchestrator import run_all
            try:
                if run_ids is None:
                    outcome = await (run_all(skip=busy) if busy else run_all())
                elif run_ids:
                    outcome = await run_all(account_ids=run_ids, skip=busy)
                else:
                    outcome = {}
                errors.extend((outcome or {}).get("errors") or [])
            except Exception as e:
                # Not one account's error: the fan-out itself broke. Still score, check
                # health and write the summary below, then mark the run failed.
                logger.exception(f"Scheduled scrape fan-out failed: {e}")
                fatal = e
            # CV scoring happens per-search/company based on their auto_scoring_depth setting
            # Also score any saved-but-unscored jobs (from manual saves), for every account.
            # R4-03: one account's failure is collected, never re-raised.
            if run_ids is None or run_ids:
                await for_each_account("saved-job scoring", _score_saved_jobs, run_ids,
                                       errors=errors)
            # Install-level system alert to the owner's chat; reads the owner's ScrapeLog
            # rows (one row per subscribing account since MT-8). TODO(MT-13): owner-sees-all
            # view of every account's scrape health.
            try:
                await check_scrape_health(since=started)
            except Exception as e:
                logger.exception(f"Scrape health check failed: {e}")
                errors.append({"account": "install", "where": "scrape health check",
                               "error": f"{type(e).__name__}: {e}"})
            run.summary = _scheduled_scrape_summary(started, skipped_accounts, errors)
            if fatal is not None:
                mark_run_failed(f"{type(fatal).__name__}: {fatal}")
    except JobAlreadyRunningError as e:
        logger.warning(f"Scheduler skipped: {e}")


def _account_names() -> dict:
    from backend.accounts.context import unscoped
    from backend.models.db import Account
    db = SessionLocal()
    try:
        with unscoped():
            return {a.id: a.name for a in db.query(Account).all()}
    except Exception:
        return {}
    finally:
        db.close()


def _scheduled_scrape_summary(started, skipped_accounts: list, errors: list) -> str:
    """The scheduled scrape_all line: the usual summary, then the accounts left out
    because their manual Run all was in flight (R4-02), then "completed with errors" with
    each account's failure (R4-03). A summary must never break the run."""
    try:
        summary = _scrape_summary(started, all_accounts=True, exclude_accounts=skipped_accounts)
    except Exception as e:
        logger.exception(f"Scrape summary failed: {e}")
        summary = "Summary unavailable"
    if not skipped_accounts and not errors:
        return summary
    names = _account_names()

    def _name(acc):
        if acc == "install":
            return "install"
        if acc is None:
            return "owner"
        return names.get(acc, "unknown account")

    if skipped_accounts:
        summary += " - skipped (manual run in progress): " + ", ".join(
            _name(a) for a in skipped_accounts)
    if errors:
        shown = "; ".join(f"{_name(e.get('account'))}: {e.get('where')}: {e.get('error')}"
                          for e in errors[:5])
        if len(errors) > 5:
            shown += f"; +{len(errors) - 5} more"
        summary += f" - completed with errors ({len(errors)}): {shown}"
    return summary


async def _score_saved_jobs():
    from backend.analyzer.cv_scorer import analyze_unscored_jobs
    await analyze_unscored_jobs(status="saved")


@scheduled_job
async def run_email_check():
    """The owner plus every member with a connected Gmail token (MT-19), each with its own
    credentials and processed-id state; an invalid_grant marks only that account expired."""
    await for_each_account("email_check", _email_check_for_account, gmail_account_ids())


async def _email_check_for_account():
    from backend.job_monitor import tracked_run, JobAlreadyRunningError
    try:
        async with tracked_run("email_check", "scheduler") as run:
            logger.info("Running email check...")
            started = datetime.now(timezone.utc)
            from backend.email_monitor.gmail_client import check_emails
            await check_emails()
            run.summary = _activity_summary(started, "email", "reply", "replies") or "No new replies"
    except JobAlreadyRunningError as e:
        logger.warning(f"Scheduler skipped: {e}")


@scheduled_job
async def send_daily_digest():
    """Per account: each account's digest counts only its own jobs and applications and
    goes to that account's own chat, on its own bot or the household bot (MT-17); an
    account without a chat id is skipped."""
    await for_each_account("daily_digest", _digest_for_account)


DIGEST_SKIPPED_SUMMARY = "Digest skipped (Telegram off / no chat)"


async def _digest_for_account():
    from backend.job_monitor import tracked_run, JobAlreadyRunningError
    try:
        async with tracked_run("daily_digest", "scheduler") as run:
            logger.info("Sending daily digest...")
            started = datetime.now(timezone.utc)
            from backend.notifier.telegram import send_digest
            sent = await send_digest()
            if sent is False:      # QA F9 / T3-02: nothing went out for this account
                run.summary = DIGEST_SKIPPED_SUMMARY
            else:
                run.summary = _activity_summary(started, "telegram", "alert") or "Digest sent"
    except JobAlreadyRunningError as e:
        logger.warning(f"Scheduler skipped: {e}")


@scheduled_job
@as_owner
async def lookup_after_scrape():
    """Install-level, after every scrape (job_monitor.on_scrape_finished): LinkedIn listings of
    every account get the employer's link through the mock account
    (scraper/_shared/linkedin_lookup.py). Nothing pending = no run."""
    from backend.accounts.context import unscoped
    from backend.job_monitor import tracked_run, JobAlreadyRunningError
    from backend.scraper._shared import linkedin_lookup as ll
    with unscoped():   # install-level: every account's rows
        db = SessionLocal()
        try:
            if not ll.pending_ids(db, limit=1) and not ll.pending_short_links(db, limit=1):
                return
        finally:
            db.close()
    try:
        async with tracked_run("linkedin_lookup", "after scrape") as run:
            out = await ll.run_lookup(per_run=ll.PER_RUN)
            run.summary = (f"{out['looked_up']} looked up - {out['employer']} employer links - "
                           f"{out['easy_apply']} Easy Apply - {out.get('closed', 0)} closed - "
                           f"{out['cached']} from earlier answers - "
                           f"{out['short_links']} short links followed"
                           + (f" - stopped: {out['stopped']}" if out["stopped"] else ""))
    except JobAlreadyRunningError as e:
        logger.warning(f"Scheduler skipped: {e}")


@scheduled_job
@as_owner
async def refresh_h1b_data():
    """Install-level: one run (owner's JobRun); refresh_all_h1b reads every account's
    companies and jobs, and VisaCache is shared."""
    from backend.job_monitor import tracked_run, JobAlreadyRunningError
    try:
        async with tracked_run("h1b_refresh", "scheduler") as run:
            logger.info("Refreshing H-1B data...")
            from backend.models.db import VisaCache
            from backend.analyzer.h1b_checker import refresh_all_h1b
            started = datetime.now(timezone.utc)
            await refresh_all_h1b()
            _db = SessionLocal()
            try:
                # H-1B metrics live in VisaCache (keyed by company name), not on Company.
                refreshed = _db.query(VisaCache).filter(VisaCache.fetched_at >= started).count()
                with_data = _db.query(VisaCache).filter(VisaCache.has_data.is_(True)).count()
                run.summary = f"{refreshed} refreshed - {with_data} compan{'y' if with_data == 1 else 'ies'} with LCA data"
            finally:
                _db.close()
    except JobAlreadyRunningError as e:
        logger.warning(f"Scheduler skipped: {e}")


@scheduled_job
async def run_auto_reject():
    """Move old non-rejected/non-offer applications to rejected after X days, per account."""
    await for_each_account("auto_reject", _auto_reject_for_account)


async def _auto_reject_for_account():
    from backend.job_monitor import tracked_run, JobAlreadyRunningError
    try:
        async with tracked_run("auto_reject", "scheduler") as run:
            db = SessionLocal()
            try:
                days = int(get_setting(db, "auto_reject_after_days") or 0)
                if days <= 0:
                    return

                from backend.models.db import Application, record_transition
                cutoff = datetime.now(timezone.utc) - timedelta(days=days)
                keep_statuses = ["rejected", "offer"]
                stale = db.query(Application).filter(
                    ~Application.status.in_(keep_statuses),
                    Application.applied_at < cutoff,
                ).all()

                count = 0
                for app in stale:
                    record_transition(app, "rejected", "scheduler")
                    count += 1

                if count:
                    db.commit()
                    logger.info(f"Auto-rejected {count} applications (>{days} days)")
                run.summary = f"{count} application{'' if count == 1 else 's'} auto-rejected (>{days}d silent)"
            finally:
                db.close()
    except JobAlreadyRunningError as e:
        logger.warning(f"Scheduler skipped: {e}")


@scheduled_job
async def run_job_cleanup_auto():
    """Auto-delete old skipped jobs if job_archive_after_days > 0, per account."""
    await for_each_account("job_cleanup", _job_cleanup_for_account)


async def _job_cleanup_for_account():
    from backend.job_monitor import tracked_run, JobAlreadyRunningError
    try:
        async with tracked_run("job_cleanup", "scheduler") as run:
            db = SessionLocal()
            try:
                days = int(get_setting(db, "job_archive_after_days") or 0)
                if days <= 0:
                    return

                from backend.models.db import Job
                cutoff = datetime.now(timezone.utc) - timedelta(days=days)
                old_jobs = db.query(Job).filter(Job.status == "skip", Job.discovered_at < cutoff).all()
                count = len(old_jobs)
                for j in old_jobs:
                    db.delete(j)
                if count:
                    db.commit()
                    logger.info(f"Job cleanup: deleted {count} skipped jobs (>{days} days)")
                run.summary = f"{count} skipped job{'' if count == 1 else 's'} deleted (>{days}d old)"
            finally:
                db.close()
    except JobAlreadyRunningError as e:
        logger.warning(f"Scheduler skipped: {e}")


async def check_scrape_health(since=None):
    """Install-level system alert, sent to the owner's chat only (runs as the owner inside
    run_all_scrapes).
    Alert via Telegram if any scraper has failed 3+ times consecutively.
    With `since`, only a source that ran at or after it can alert: a source that
    stopped running (a paused search) keeps its last three rows forever."""
    from backend.models.db import ScrapeLog
    db = SessionLocal()
    try:
        q = db.query(ScrapeLog.source).distinct()
        if since is not None:
            q = q.filter(ScrapeLog.ran_at >= since)
        sources = q.all()
        alerts = []
        for (source,) in sources:
            recent = db.query(ScrapeLog).filter(
                ScrapeLog.source == source
            ).order_by(ScrapeLog.ran_at.desc()).limit(3).all()
            # `is_warning` is wider than this alert: since #17 it also marks a run where one
            # board returned nothing while the others delivered. That belongs in the run
            # summary and the health panel, not in an hourly page; here a run counts as bad
            # only when it failed outright or found nothing at all.
            if len(recent) >= 3 and all(r.error or not (r.jobs_found or 0) for r in recent):
                alerts.append(source)

        if alerts:
            try:
                # System alert: the owner's chat on the household bot only (MT-17).
                from backend.notifier.telegram import send_system_alert
                msg = "\u26a0\ufe0f Scrape health alert:\n" + "\n".join(f"\u2022 {s}: 3 consecutive failures/empty results" for s in alerts)
                await send_system_alert(msg)
            except Exception as e:
                logger.error(f"Failed to send scrape health alert: {e}")
    finally:
        db.close()


@scheduled_job
@as_owner
async def run_db_backup():
    """Install-level: run pg_dump of the whole database and keep max 5 backup files."""
    from backend.job_monitor import tracked_run, JobAlreadyRunningError
    try:
        async with tracked_run("db_backup", "scheduler") as run:
            from backend.db_backup import run_pg_dump

            try:
                b = run_pg_dump()
            except RuntimeError as e:
                logger.error(str(e))
                return

            logger.info(f"Database backup created: {b['path']}")
            if b["pruned"]:
                logger.info(f"Removed {b['pruned']} old backup(s)")
            run.summary = f"Snapshot {b['size_mb']:.0f} MB -> {b['file']}"
            if b["pruned"]:
                run.summary += f" - {b['pruned']} old pruned"
    except JobAlreadyRunningError as e:
        logger.warning(f"Scheduler skipped: {e}")


