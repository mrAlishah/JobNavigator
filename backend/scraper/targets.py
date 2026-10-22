"""Shared-scrape targets (MT-8).

Every account keeps its own searches and companies, but two of them that would fetch
exactly the same thing are one *target*: the scheduler fetches it once and hands the raw
postings to each subscriber, whose own ingest (title/company filters, excludes, dedup,
H-1B and salary checks, auto-score) runs under its own account context.

Target keys
- Company: the set of normalised scrape URLs (+ the ATS each one is detected as),
  max_pages and wait_for_selector. A different URL, e.g. a city filter in the query
  string, is a different target.
- Search, by mode:
  - keyword (JobSpy): boards, term, location, country, job type, remote flag.
    results_wanted and hours_old are NOT part of the key: the shared fetch uses the
    largest value among subscribers and each account trims its copy afterwards — but
    only among subscribers whose values are close (`split_search_targets`, QA F10 /
    R4-05), so one account's 500 rows / 30 days never inflates another's 50 rows / 1 day.
  - levels_fyi: normalised direct_url + max_pages.
  - freehire: the API query the search builds (direct_url filters + term); results_wanted
    is taken as the max and trimmed per account.
  - caribbeanjobs: the URL filters + the keyword alternatives in order; results_wanted as
    for freehire. Detail pages are read once per shared fetch (`SharedFetch.memo`).
  - trulyremote: one key for every search. The roster (`llms-full.txt`) is the whole corpus
    with no query, so all trulyremote searches of all accounts share one fetch; results_wanted
    caps each subscriber's new job pages after its own dedup, so it is not a fetch parameter.
    The `.md` job pages are read once per shared fetch (`SharedFetch.memo`). The parsed roster
    is also kept in-process for as long as the board's Cache-Control allows (at most an hour;
    `trulyremote._fetch_roster`), which the memo rule allows: it is raw board data, the same
    for every account.
  - linkedin_personal, jobright and anything unknown read the account's own session, so
    they are never shared: their key carries the search id.
  - apify is never shared either (deliberately, not by omission): every run is paid with
    the token of the account it runs for, so a shared fetch would bill one subscriber for
    another's results. Sharing it would need the payer in the key (the owner's token for
    the owner and members on 'inherit', the member's own otherwise); deferred past 3.0.

Due-ness: a target is due when any subscriber is due under its own interval (custom, or
the global default), so the effective interval is the shortest among subscribers. Time is
measured from the start of the batch that last ran it (the scheduled batch stamps its own
start, QA V-02) and a target runs on the scheduler tick nearest its due time. A due
target feeds every subscriber. A manual "run now" on one search/company does not use this
module: it fetches for, and feeds, only the account that pressed it.

Everything here is pure (no DB writes); loading rows across accounts is the caller's job
(`load_active`), done inside `unscoped()`.
"""
import asyncio
import copy
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from backend.accounts.context import unscoped

logger = logging.getLogger("jobnavigator.scraper.targets")

# Which modes may share a fetch is `search_modes.SearchMode.shareable`.


# ── Shared fetch ─────────────────────────────────────────────────────────────

class SharedFetch:
    """The one fetch of a shared target, memoised for its subscribers.

    The first subscriber's run performs the fetch through `get()` / `get_sync()`; later
    subscribers get a deep copy of the same postings (or the same exception), so an ingest
    can never change what the next account sees. Within one run the subscribers go one
    after another.

    `params` holds the fetch-side values that differ from a subscriber's own, e.g.
    {"results_wanted": 200, "hours_old": 72} (the max among subscribers).

    `memo` is scratch space a source may keep across its subscribers for follow-up fetches
    that are the same for everyone (caribbeanjobs: detail pages by URL; trulyremote: `.md`
    job pages by URL; both: the board's throttle stop, `<source>_halt`, so once one subscriber
    was stopped by a 429 the later ones request no page that run). Only raw, account-
    independent page data goes in it, never anything an ingest filtered or wrote.

    A later run can hold the same instance (`shared_fetch_for`, cross-run reuse), so the
    fetch itself is locked: the first caller fetches, the others wait and get copies."""

    def __init__(self, key=None, params: Optional[dict] = None):
        self.key = key
        self.params = dict(params or {})
        self.memo: dict = {}
        self.done = False
        self.calls = 0           # how many times the fetch really ran (0 or 1)
        self.duration = 0.0
        self.finished_at: Optional[float] = None   # time.monotonic() when the fetch ended
        self._value = None
        self._error: Optional[BaseException] = None
        # Two runs can hold the same fetch (`shared_fetch_for`), so the first caller fetches
        # and the others wait for it: an asyncio lock for `get`, a thread lock for `get_sync`
        # (JobSpy runs in a worker thread).
        self._thread_lock = threading.Lock()
        self._async_lock: Optional[asyncio.Lock] = None
        self._lock_loop = None

    def _result(self):
        if self._error is not None:
            raise self._error
        return copy.deepcopy(self._value)

    def _finish(self, start: float):
        self.duration = time.time() - start
        self.finished_at = time.monotonic()
        self.done = True

    def _lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._lock_loop is not loop:     # one loop in the app; tests use a fresh one each
            self._async_lock, self._lock_loop = asyncio.Lock(), loop
        return self._async_lock

    async def get(self, fetch: Callable[[], Any]):
        if not self.done:
            async with self._lock():
                if not self.done:
                    start = time.time()
                    self.calls += 1
                    try:
                        self._value = await fetch()
                    except Exception as e:
                        self._error = e
                    self._finish(start)
        return self._result()

    def get_sync(self, fetch: Callable[[], Any]):
        if not self.done:
            with self._thread_lock:
                if not self.done:
                    start = time.time()
                    self.calls += 1
                    try:
                        self._value = fetch()
                    except Exception as e:
                        self._error = e
                    self._finish(start)
        return self._result()

    def covers(self, params: Optional[dict]) -> bool:
        """Whether this fetch serves a run that asks for `params`: every numeric value
        (results_wanted, hours_old) at least as large, anything else equal."""
        for k, v in (params or {}).items():
            mine = self.params.get(k)
            if isinstance(v, (int, float)) and isinstance(mine, (int, float)):
                if mine < v:
                    return False
            elif mine != v:
                return False
        return True

    def reusable(self, now: float) -> bool:
        """In flight, or finished within REUSE_SECONDS with postings and no error. A failed or
        empty fetch (a 429, a board that was down) is shared within its own run only."""
        if not self.done:
            return True
        return (self._error is None and _has_postings(self._value)
                and now - (self.finished_at or 0) <= REUSE_SECONDS)


# Cross-run reuse. Runs go one after another (the scrape gate), so two accounts' Run alls,
# or a manual Run all and the scheduled scrape, would each download the same board minutes
# apart. A run asks `shared_fetch_for(key, params)` and gets the earlier run's fetch when it
# is still going or ended less than REUSE_SECONDS ago and covers the request. What is shared
# is raw board data only; each account's ingest still filters, dedups and writes its own rows.
REUSE_SECONDS = 600
_recent: dict = {}
_recent_lock = threading.Lock()


def _has_postings(value) -> bool:
    """A fetch result that carries postings. Sources return a list/DataFrame, or a tuple
    whose first item is one ((postings, errors))."""
    if isinstance(value, tuple):
        value = value[0] if value else None
    if value is None:
        return False
    try:
        return len(value) > 0
    except TypeError:
        return True


def reusable_key(key) -> bool:
    """Keys that name a per-account fetch (the account's own session, a paid Apify run) or
    a row whose key could not be built are never shared across runs."""
    return bool(key) and key[0] != "unkeyed" and not (len(key) > 1 and key[1] == "own")


def shared_fetch_for(key, params: Optional[dict] = None) -> "SharedFetch":
    """The SharedFetch a run should use for target `key`: a recent or in-flight one of
    another run when it covers `params`, else a new one (registered for later runs)."""
    if not reusable_key(key):
        return SharedFetch(key, params)
    now = time.monotonic()
    with _recent_lock:
        for k in [k for k, f in _recent.items() if not f.reusable(now)]:
            _recent.pop(k, None)
        earlier = _recent.get(key)
        if earlier is not None and earlier.covers(params):
            age = "in flight" if not earlier.done else f"{now - earlier.finished_at:.0f}s old"
            logger.info(f"Reusing the fetch of {key[0]} target ({age})")
            if earlier.done:
                # A board's throttle stop (`<source>_halt`) lasts one run; pages the earlier
                # run already read stay in the memo and are not requested again.
                for k in [k for k in earlier.memo if k.endswith("_halt")]:
                    earlier.memo.pop(k, None)
            return earlier
        fresh = SharedFetch(key, params)
        _recent[key] = fresh
        return fresh


def reset_shared_fetches() -> None:
    """Forget every recent fetch (tests)."""
    with _recent_lock:
        _recent.clear()


# ── Keys ─────────────────────────────────────────────────────────────────────

def target_url(url: str) -> str:
    """A list/board URL in comparable form: trimmed, scheme and host lowercased, no
    trailing slash on the path, query params sorted. Nothing else is dropped (QA F6 /
    R4-04): the fetch uses the first subscriber's raw URL, so anything removed here
    could make two different lists collapse into one target. The fragment stays (a
    hash-routed SPA board, `#/jobs?location=Berlin`, is a different list), and so does
    every query param, tracking-looking or not (`from=20`, `mode=location`, `source=`
    are real list filters on some boards; `dedup_tracking_params` is for posting URLs)."""
    raw = (url or "").strip()
    if not raw:
        return ""
    if not raw.lower().startswith(("http://", "https://")):
        return raw   # a request spec such as "POST|https://…|{json}": compared verbatim
    try:
        parts = urlsplit(raw)
        query = sorted(parse_qsl(parts.query, keep_blank_values=True))
        path = parts.path.rstrip("/")
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path,
                           urlencode(query, doseq=True), parts.fragment))
    except Exception:
        return raw


def _ats_label(url: str) -> str:
    try:
        from backend.api.routes_companies import detect_scrape_type
        return detect_scrape_type(url)
    except Exception:
        return "?"


def company_key(company) -> tuple:
    urls = sorted({target_url(u) for u in (company.scrape_urls or []) if (u or "").strip()})
    return (
        "company",
        tuple((u, _ats_label(u)) for u in urls),
        getattr(company, "max_pages", 5) or 5,
        (getattr(company, "wait_for_selector", None) or "").strip(),
    )


def _norm(text) -> str:
    return " ".join(str(text or "").split()).lower()


def search_key(search) -> tuple:
    mode = search.search_mode or "keyword"
    if mode == "keyword":
        from backend.countries import DEFAULT_COUNTRY
        return (
            "keyword",
            tuple(sorted({s for s in (search.sources or []) if s != "direct"})),
            _norm(search.search_term),
            _norm(search.location),
            _norm(search.country or DEFAULT_COUNTRY),
            _norm(search.job_type or "fulltime"),
            search.is_remote,
        )
    if mode == "levels_fyi":
        return ("levels_fyi", target_url(search.direct_url), search.max_pages or 50)
    if mode == "freehire":
        from backend.scraper.sources.freehire import _base_params
        return ("freehire", tuple(sorted(_base_params(search).items())))
    if mode == "caribbeanjobs":
        from backend.scraper.sources.caribbeanjobs import _query_plan
        base, keywords = _query_plan(search)
        # Keyword order is kept: the merged listing is cut to results_wanted in query order.
        return ("caribbeanjobs", tuple(sorted(base.items())), tuple(_norm(k) for k in keywords))
    if mode == "trulyremote":
        # The whole corpus, no query: every trulyremote search is the same fetch.
        return ("trulyremote",)
    # linkedin_personal / jobright (the account's own session), apify (each account pays
    # with its own token, see the module docstring) and unknown modes
    return (mode, "own", str(search.id))


def search_fetch_params(subscribers: list) -> dict:
    """The fetch-side values for a shared search target: the max of the trimmed fields."""
    from backend.scraper.search_modes import get_mode
    mode = get_mode(subscribers[0].search_mode)
    return {field: max((getattr(s, field) or default) for s in subscribers)
            for field, default in (mode.fetch_params if mode else ())}


# QA F10 / R4-05: when JobSpy subscribers may share one fetch. A shared fetch runs at the
# largest results_wanted / hours_old of its subscribers, so a big request (long, likely to
# hit LinkedIn's 429) would degrade every small one sharing it. The rule:
#   - hours_old: same bucket (≤24 h, ≤3 d, ≤7 d, ≤14 d, ≤30 d, longer);
#   - results_wanted: within SHARE_RESULTS_RATIO of the smallest in the group.
# Anything else is a separate fetch (a separate target with the same key plus its fetch
# values). 50 vs 60 rows → one fetch at 60, the 50 trimmed; 50 vs 500 → two fetches.
SHARE_HOURS_BUCKETS = (24, 72, 168, 336, 720)
SHARE_RESULTS_RATIO = 1.5


def hours_bucket(hours_old) -> int:
    hours = hours_old or 24
    for i, bound in enumerate(SHARE_HOURS_BUCKETS):
        if hours <= bound:
            return i
    return len(SHARE_HOURS_BUCKETS)


def _split_keyword_subscribers(subscribers: list) -> list[list]:
    """Groups of subscribers that may share one JobSpy fetch, each in load order, the
    groups ordered by their first subscriber."""
    order = {id(s): i for i, s in enumerate(subscribers)}
    by_bucket: dict = {}
    for s in subscribers:
        by_bucket.setdefault(hours_bucket(s.hours_old), []).append(s)
    groups = []
    for rows in by_bucket.values():
        rows = sorted(rows, key=lambda s: ((s.results_wanted or 50), order[id(s)]))
        current, floor = [], None
        for s in rows:
            wanted = s.results_wanted or 50
            if current and wanted > floor * SHARE_RESULTS_RATIO:
                groups.append(current)
                current = []
            if not current:
                floor = wanted
            current.append(s)
        if current:
            groups.append(current)
    groups = [sorted(g, key=lambda s: order[id(s)]) for g in groups]
    return sorted(groups, key=lambda g: order[id(g[0])])


def split_search_targets(targets: list) -> list:
    """Split shared keyword (JobSpy) targets whose subscribers ask for very different
    results_wanted / hours_old into separate fetches (QA F10 / R4-05). Other modes and
    single-subscriber targets pass through unchanged."""
    out = []
    for target in targets:
        if not target.shared or target.key[:1] != ("keyword",):
            out.append(target)
            continue
        groups = _split_keyword_subscribers(target.subscribers)
        if len(groups) == 1:
            out.append(target)
            continue
        for group in groups:
            params = search_fetch_params(group)
            key = target.key + (("fetch", params["results_wanted"], params["hours_old"]),)
            out.append(Target(key=key, subscribers=group))
    return out


def is_shareable_search(search) -> bool:
    # Never "apify" (billed per account) nor the own-session modes; see search_modes.py.
    from backend.scraper.search_modes import get_mode
    mode = get_mode(search.search_mode)
    return bool(mode and mode.shareable)


# ── Due-ness ─────────────────────────────────────────────────────────────────

def _as_utc(ts):
    if ts is not None and ts.tzinfo is None:   # SQLite / legacy rows are naive UTC
        return ts.replace(tzinfo=timezone.utc)
    return ts


def _elapsed_due(elapsed: float, interval: int, tick: int) -> bool:
    """QA V-02: the scheduler only looks on its ticks (every `tick` minutes), so a
    target runs on the tick NEAREST its due time: due once at most half a tick is
    left. A strict `elapsed >= interval` skipped every other tick whenever the
    interval was a multiple of the tick, because the previous stamp lands a little
    after the previous tick (the default 60 m interval ran every 120 m)."""
    return elapsed >= interval - max(tick or 0, 0) / 2


def company_due(company, now: datetime, global_interval: int, tick: Optional[int] = None):
    """(due, elapsed_minutes|None, interval) for one company under its own interval.
    `tick` is the scheduler period, by default the global interval (they are the same
    setting)."""
    interval = company.scrape_interval_minutes or global_interval
    last = _as_utc(company.last_scraped_at)
    if not last:
        return True, None, interval
    elapsed = (now - last).total_seconds() / 60
    return _elapsed_due(elapsed, interval, global_interval if tick is None else tick), elapsed, interval


def search_due(search, now: datetime, tick: int = 0):
    """(due, elapsed_minutes|None, interval). A search without its own interval runs on
    every scheduler tick (the global scrape interval), as before. `tick` is that
    interval (see _elapsed_due)."""
    interval = search.run_interval_minutes or 0
    last = _as_utc(search.last_run_at)
    if not interval or not last:
        return True, None, interval
    elapsed = (now - last).total_seconds() / 60
    return _elapsed_due(elapsed, interval, tick), elapsed, interval


# ── Targets ──────────────────────────────────────────────────────────────────

@dataclass
class Target:
    key: tuple
    subscribers: list = field(default_factory=list)   # Search or Company rows, load order

    @property
    def shared(self) -> bool:
        return len(self.subscribers) > 1

    @property
    def account_ids(self) -> list:
        out = []
        for s in self.subscribers:
            if s.account_id not in out:
                out.append(s.account_id)
        return out


def group_targets(rows: list, key_fn: Callable) -> list[Target]:
    """Group rows by key, keeping the order in which each key first appears (so a single
    account without duplicates iterates exactly as the rows were loaded)."""
    targets: dict = {}
    for row in rows:
        try:
            key = key_fn(row)
        except Exception as e:   # a key we cannot build is a target of its own
            logger.warning(f"Target key failed for {getattr(row, 'name', row)!r}: {e}")
            key = ("unkeyed", str(getattr(row, "id", id(row))))
        targets.setdefault(key, Target(key=key)).subscribers.append(row)
    return list(targets.values())


def company_target_due(target: Target, now: datetime, global_interval: int):
    """(due, effective_interval). Due when any subscriber is due; the effective interval
    is the shortest among subscribers."""
    states = [company_due(c, now, global_interval) for c in target.subscribers]
    return any(s[0] for s in states), min(s[2] for s in states)


def search_target_due(target: Target, now: datetime, tick: int = 0):
    states = [search_due(s, now, tick) for s in target.subscribers]
    return any(s[0] for s in states), min(s[2] for s in states)


def load_active(db, model, account_ids=None) -> list:
    """Active rows of `model` for the given accounts (None = every account), read
    `unscoped()` so one pass sees every subscriber. An id list holding only None (the
    owner fallback when accounts are unreadable) reads in the caller's context."""
    q = db.query(model).filter(model.active == True)  # noqa: E712
    if account_ids is None:
        with unscoped():
            return q.all()
    ids = [a for a in account_ids if a is not None]
    if not ids:
        return q.all()
    with unscoped():
        return q.filter(model.account_id.in_(ids)).all()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
