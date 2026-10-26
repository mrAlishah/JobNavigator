"""Link a new job to the posting it repeats, for every source at once.

A job is stored once per link (`external_id`), so one posting reached through Jobright,
Levels.fyi, LinkedIn and the company's own board became four feed rows. Every new Job
now gets a `posting_key` (postings.py) from its employer link (`apply_url`) or its own
link, and before it is written it is compared with the account's jobs:

1. Same posting key as a job the feed already has: the newcomer is that posting again.
2. Same role (same_role.py): the same company and title as a job from the last 60 days, and
   the same text word for word apart from ids, pay, dates and the two locations, in a text the
   company does not also use under another title. This covers a role listed once per city, several
   openings of one role, a re-post under a new id, and a board listing of the employer's posting.
   A job without enough text to compare is never linked this way.
3. When the newcomer is the employer's posting and an untouched (status "new") listing-only
   copy of it arrived first (rule 2's proof), that copy is the duplicate from now on.

A duplicate is kept, not dropped: status "duplicate", `duplicate_of` pointing at the
posting, so its link stays known (no re-import), the feed hides it, and the original
lists it under "Also listed". Rows the user acted on (saved, applied, skipped) are never
turned into duplicates. Filtered rows ("ignored") are neither linked nor link targets.

A copy in a place the original is not in is not hidden under an original the user skipped:
a role skipped in Seattle may fit in New York. It stays in the feed with `same_role_as`
pointing at the skipped job, so the feed can say so.

This runs in a before_flush listener registered after the account stamping
(accounts/scoping.py), so `account_id` is already set; every query names the account.
"""
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func

from backend.scraper._shared.postings import is_employer_key, posting_key, unwrap
from backend.scraper._shared.same_role import is_template, same_text

logger = logging.getLogger("jobnavigator.posting_links")

DUPLICATE = "duplicate"
NOT_TARGETS = ("ignored", DUPLICATE)
SAME_ROLE_DAYS = 60
# Most rows of one company read for the template check (a big employer posts hundreds).
_TEMPLATE_ROWS = 3000


def keys_of(job) -> list:
    """Every posting key the job's links name, the employer's first (an Indeed row names both
    its listing and, through `apply_url`, the employer's posting)."""
    keys = []
    for k in (posting_key(u) for u in (job.apply_url, job.url) if u):
        if k and k not in keys:
            keys.append(k)
    return sorted(keys, key=lambda k: not is_employer_key(k))


def key_of(job) -> str | None:
    """The job's posting key: the employer's posting when either link names one, else the listing."""
    keys = keys_of(job)
    return keys[0] if keys else None


def employer_link(url) -> str | None:
    """`url` as an employer link worth storing in `apply_url`: unwrapped, and naming an employer posting."""
    if not url:
        return None
    plain = unwrap(url)
    return plain if is_employer_key(posting_key(plain)) else None


def _visible(Job):
    return (Job.status.is_(None)) | (Job.status.notin_(NOT_TARGETS))


def _ensure_id(job):
    if job.id is None:
        job.id = uuid.uuid4()
    return job.id


def mark_duplicate(job, original) -> None:
    """`job` is `original` again: hide it under the original, and give the original the employer link it lacked."""
    job.status = DUPLICATE
    job.duplicate_of = _ensure_id(original)
    if job.posting_key and is_employer_key(job.posting_key) and not is_employer_key(original.posting_key):
        original.posting_key = job.posting_key
    if not original.apply_url:
        link = employer_link(job.apply_url) or employer_link(job.url)
        if link and link != original.url:
            original.apply_url = link


def _since(when=None):
    return (when or datetime.now(timezone.utc)) - timedelta(days=SAME_ROLE_DAYS)


def _norm(text) -> str:
    return (text or "").strip().lower()


class Batch:
    """The jobs of the flush being linked: the queries run with autoflush off and do not see them."""

    def __init__(self):
        self.keys: dict = {}
        self.titles: dict = {}
        self.companies: dict = {}

    def seen(self, job) -> None:
        """Every new job of the flush, filtered ones too: its text counts for the template check."""
        self.companies.setdefault((job.account_id, _norm(job.company)), []).append(job)

    def add(self, job) -> None:
        """A job that stays visible: later jobs of the flush may be its copies."""
        for k in dict.fromkeys([job.posting_key] + keys_of(job)):
            if k:
                self.keys.setdefault((job.account_id, k), job)
        if job.content_hash:
            self.titles.setdefault((job.account_id, job.content_hash), []).append(job)


def company_template(session, job, batch: Batch) -> bool:
    """True when the company uses this job's text under another title (same_role.is_template)."""
    from backend.models.db import Job

    company, title = _norm(job.company), _norm(job.title)
    texts = [o.description for o in batch.companies.get((job.account_id, company), [])
             if o is not job and _norm(o.title) != title and o.description]
    rows = (session.query(Job.description)
            .filter(Job.account_id == job.account_id,
                    func.lower(func.trim(Job.company)) == company,
                    func.lower(func.trim(Job.title)) != title,
                    Job.description.isnot(None), Job.discovered_at >= _since())
            .limit(_TEMPLATE_ROWS).all())
    texts += [d for (d,) in rows]
    return is_template(job.description, texts)


def _proven(job, others) -> list:
    """The jobs among `others` whose text proves they are the same role as `job`."""
    return [o for o in others if same_text(job.description, o.description, job.location, o.location)]


def _same_title(session, job, batch: Batch, query_filter=None) -> list:
    from backend.models.db import Job

    if not job.content_hash or not job.description:
        return []
    q = (session.query(Job)
         .filter(Job.account_id == job.account_id, Job.content_hash == job.content_hash,
                 Job.description.isnot(None), Job.discovered_at >= _since()))
    if query_filter is not None:
        q = q.filter(query_filter(Job))
    if job.id is not None:
        q = q.filter(Job.id != job.id)
    return q.order_by(Job.discovered_at.asc()).all()


def _listing_only(job) -> bool:
    return not is_employer_key(job.posting_key)


def _places(job) -> set:
    """The places a job names: its parsed place list, else its primary parsed place."""
    rows = {(r.country, r.region, r.city) for r in (getattr(job, "locations", None) or [])}
    primary = (job.loc_country, job.loc_region, job.loc_city)
    if any(primary):
        rows.add(primary)
    return {p for p in rows if any(p)}


def new_place(job, original) -> bool:
    """True when `job` names a place `original` does not. Without parsed places on both, any
    difference in the location text counts: when unsure, the copy is shown, not hidden."""
    a, b = _places(job), _places(original)
    if a and b:
        return bool(a - b)
    return _norm(job.location) != _norm(original.location)


def skipped_elsewhere(job, original) -> bool:
    """`original` was skipped, and `job` is the role somewhere it is not: not a copy to hide."""
    return original.status == "skip" and new_place(job, original)


def _pick(job, candidates, elsewhere):
    """The first candidate that may hold `job` as its copy; a skipped one in another place
    is noted in `elsewhere` (a one-item list) instead."""
    for o in candidates:
        if skipped_elsewhere(job, o):
            elsewhere[:] = elsewhere or [o]
            continue
        return o
    return None


def find_original(session, job, batch: Batch):
    """(the job the account already has for this posting or role, rules 1 and 2, or None;
    the job the user skipped in another place, or None)."""
    from backend.models.db import Job

    key, acct = job.posting_key, job.account_id
    elsewhere: list = []
    keys = [k for k in dict.fromkeys([key] + keys_of(job)) if k]
    if keys:
        hits = [batch.keys.get((acct, k)) for k in keys]
        hits = [h for h in hits if h is not None and h is not job and h.status not in NOT_TARGETS]
        q = (session.query(Job)
             .filter(Job.account_id == acct, Job.posting_key.in_(keys), _visible(Job)))
        if job.id is not None:
            q = q.filter(Job.id != job.id)
        hit = _pick(job, hits + q.order_by(Job.discovered_at.asc()).all(), elsewhere)
        if hit is not None:
            return hit, None
    # Rule 2. An employer posting skips the untouched listing-only rows: rule 3 makes those its copies.
    if not job.content_hash or not job.description:
        return None, (elsewhere or [None])[0]
    employer = is_employer_key(key)
    candidates = _same_title(session, job, batch, _visible)
    candidates += [o for o in batch.titles.get((acct, job.content_hash), [])
                   if o is not job and o.status not in NOT_TARGETS]
    if employer:
        candidates = [o for o in candidates if not (_listing_only(o) and (o.status or "new") == "new")]
    proven = _proven(job, candidates)
    if proven and not company_template(session, job, batch):
        hit = _pick(job, proven, elsewhere)
        if hit is not None:
            return hit, None
    return None, (elsewhere or [None])[0]


def listing_copies(session, job, batch: Batch) -> list:
    """Rule 3: untouched listing-only copies of an employer posting that just arrived."""
    from backend.models.db import Job

    if not is_employer_key(job.posting_key) or not job.content_hash or not job.description:
        return []
    untouched = _same_title(session, job, batch, lambda J: (J.status == "new") & (
        J.posting_key.is_(None) | J.posting_key.like("linkedin:%") | J.posting_key.like("indeed:%")))
    untouched += [o for o in batch.titles.get((job.account_id, job.content_hash), [])
                  if o is not job and _listing_only(o) and (o.status or "new") == "new"]
    proven = _proven(job, untouched)
    if proven and company_template(session, job, batch):
        return []
    return proven


def link_new_jobs(session, flush_context=None, instances=None) -> None:
    """before_flush: give every new Job its posting key and link it to the posting it repeats."""
    from backend.models.db import Job

    new = [o for o in session.new if isinstance(o, Job)]
    if not new:
        return
    batch = Batch()
    with session.no_autoflush:
        for job in new:
            if job.posting_key is None:
                job.posting_key = key_of(job)
            if job.apply_url is None:
                link = employer_link(job.url)
                if link and link != job.url:
                    job.apply_url = link
            batch.seen(job)
        for job in new:
            # a row created saved or applied (the extension's save) is the user's own: never hidden
            if (job.status or "new") != "new" or job.duplicate_of is not None or job.account_id is None:
                if job.status not in NOT_TARGETS:
                    batch.add(job)
                continue
            try:
                original, elsewhere = find_original(session, job, batch)
                if original is not None:
                    mark_duplicate(job, original)
                    continue
                if elsewhere is not None:
                    job.same_role_as = _ensure_id(elsewhere)
                for copy in listing_copies(session, job, batch):
                    mark_duplicate(copy, job)
            except Exception as e:  # linking must never stop a job from being stored
                logger.warning(f"Posting link skipped for {job.title!r} at {job.company!r}: {e}")
                continue
            batch.add(job)


# ── stored jobs (seed.migrate_posting_keys, after the readers change) ─────────

# Who keeps a posting when several stored rows are one: the row the user acted on most,
# then the employer's own posting over a board listing, then the oldest.
_STATUS_RANK = {"applied": 0, "saved": 1, "skip": 2, "new": 3}


def _rank(row):
    return (_STATUS_RANK.get(row.status or "new", 3), 0 if is_employer_key(row.posting_key) else 1,
            row.discovered_at.timestamp() if row.discovered_at else 0)


def _all_keys(job) -> set:
    return {k for k in [job.posting_key] + keys_of(job) if k}


class _CompanyTexts:
    """The account's job texts by company, for the template check on stored jobs."""

    def __init__(self, db, account_id):
        self.db, self.account_id, self.cache = db, account_id, {}

    def template(self, job) -> bool:
        from backend.models.db import Job
        from backend.scraper._shared.same_role import shingles
        company = _norm(job.company)
        if company not in self.cache:
            rows = (self.db.query(Job.title, Job.discovered_at, Job.description)
                    .filter(Job.account_id == self.account_id,
                            func.lower(func.trim(Job.company)) == company,
                            Job.description.isnot(None)).all())
            self.cache[company] = [(_norm(t), d, shingles(desc)) for t, d, desc in rows]
        title, when = _norm(job.title), job.discovered_at
        window = timedelta(days=SAME_ROLE_DAYS)
        others = [sh for t, d, sh in self.cache[company]
                  if t != title and (when is None or d is None or abs(d - when) <= window)]
        return is_template(job.description, others)


def _same_role_stored(a, b, templates: _CompanyTexts) -> bool:
    return bool(a.content_hash and a.content_hash == b.content_hash
                and same_text(a.description, b.description, a.location, b.location)
                and not templates.template(a))


def relink_account(db, account_id, batch: int = 500) -> dict:
    """Bring one account's stored duplicates in line with the rules above.

    1. A duplicate linked by company and title alone (the 3.1 rule) that shares no posting key
       with its original and whose text does not prove one role goes back to the feed, and so does
       a copy hidden under a skipped original in another place (with `same_role_as` set).
    2. Visible rows that share a posting key, or that are one role by rule 2: the row the user
       acted on most keeps it and every other row still at status "new" becomes its duplicate,
       except under a skipped row in another place. A row the user acted on is never changed.
    Returns {"unlinked": n, "linked": n}."""
    from backend.models.db import Job

    templates = _CompanyTexts(db, account_id)
    unlinked = 0
    dups = (db.query(Job).filter(Job.account_id == account_id, Job.status == DUPLICATE,
                                 Job.duplicate_of.isnot(None)).all())
    want = list({d.duplicate_of for d in dups})
    originals = {}
    for i in range(0, len(want), batch):
        originals.update({o.id: o for o in db.query(Job).filter(Job.id.in_(want[i:i + batch])).all()})
    for d in dups:
        o = originals.get(d.duplicate_of)
        if o is None:
            continue
        if skipped_elsewhere(d, o):
            d.status, d.duplicate_of, d.same_role_as = "new", None, o.id
            unlinked += 1
            continue
        if _all_keys(d) & _all_keys(o) or _same_role_stored(d, o, templates):
            continue
        d.status, d.duplicate_of = "new", None
        unlinked += 1
    db.commit()

    cols = (Job.id, Job.posting_key, Job.url, Job.apply_url, Job.status, Job.discovered_at,
            Job.content_hash, Job.company, Job.title, Job.location, Job.description,
            Job.loc_country, Job.loc_region, Job.loc_city, Job.same_role_as)
    rows = (db.query(*cols).filter(Job.account_id == account_id, Job.duplicate_of.is_(None),
                                   _visible(Job)).all())
    rows.sort(key=_rank)
    moves: dict = {}
    notes: dict = {}
    by_key: dict = {}
    for r in rows:
        if (r.status or "new") == "new":
            elsewhere: list = []
            same = [k for key in _all_keys(r) for k in by_key.get(key, [])]
            keeper = _pick(r, same, elsewhere)
            if keeper is not None:
                moves[r.id] = keeper.id
                continue
            if elsewhere:
                notes[r.id] = elsewhere[0].id
        for k in _all_keys(r):
            by_key.setdefault(k, []).append(r)
    by_title: dict = {}
    window = timedelta(days=SAME_ROLE_DAYS)
    for r in rows:
        if r.id in moves or not r.content_hash or not r.description:
            continue
        kept = by_title.setdefault(r.content_hash, [])
        if (r.status or "new") == "new":
            elsewhere = []
            proven = [k for k in kept
                      if (not r.discovered_at or not k.discovered_at or abs(r.discovered_at - k.discovered_at) <= window)
                      and _same_role_stored(r, k, templates)]
            keeper = _pick(r, proven, elsewhere)
            if keeper is not None:
                moves[r.id] = keeper.id
                notes.pop(r.id, None)
                continue
            if elsewhere and r.id not in notes:
                notes[r.id] = elsewhere[0].id
        kept.append(r)
    items = list(moves.items())
    for i in range(0, len(items), batch):
        part = dict(items[i:i + batch])
        for job in db.query(Job).filter(Job.id.in_(list(part))).all():
            job.status, job.duplicate_of = DUPLICATE, part[job.id]
        db.commit()
    already = {r.id for r in rows if r.same_role_as}
    noted = [(i, o) for i, o in notes.items() if i not in already]
    for i in range(0, len(noted), batch):
        part = dict(noted[i:i + batch])
        for job in db.query(Job).filter(Job.id.in_(list(part))).all():
            job.same_role_as = part[job.id]
        db.commit()
    # A duplicate whose original became a duplicate points at the posting that stays visible.
    for _ in range(5):
        chained = (db.query(Job).filter(Job.account_id == account_id, Job.status == DUPLICATE,
                                        Job.duplicate_of.in_(list(moves))).all()) if moves else []
        if not chained:
            break
        for job in chained:
            job.duplicate_of = moves.get(job.duplicate_of, job.duplicate_of)
        db.commit()
    return {"unlinked": unlinked, "linked": len(moves)}


def install_posting_links() -> None:
    from sqlalchemy import event
    from sqlalchemy.orm import Session
    event.listen(Session, "before_flush", link_new_jobs)


def counts_as_new(job) -> bool:
    """For a source's "+N new": a job stored as a duplicate is not new to the feed."""
    return getattr(job, "status", None) != DUPLICATE
