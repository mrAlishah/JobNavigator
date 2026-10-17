"""PR #32 decisions: R3 a body-excluded job is stored as ignored (TrulyRemoteWork and
CaribbeanJobs), so its page is read once; R10 the external_id backfill leaves trulyremote rows
alone (their id hashes the board URL, their url is the apply link). R12 (the badge colour)
is checked by frontend/src/__tests__/sourceBadges.test.js."""
import uuid

import pytest

from backend.models.db import Job, Search
from backend.scraper._shared.dedup import make_external_id
from backend.scraper.sources import caribbeanjobs, trulyremote


@pytest.fixture(autouse=True)
def _quiet(monkeypatch):
    async def no_sleep(_s):
        return None

    async def analyze(job, db=None, **kw):
        job.h1b_jd_flag = "visa" in (job.description or "").lower()

    for mod in (trulyremote, caribbeanjobs):
        monkeypatch.setattr(mod.asyncio, "sleep", no_sleep)
        monkeypatch.setattr(mod, "analyze_inline", analyze)
    monkeypatch.setattr("backend.activity.log_activity", lambda *a, **k: None)
    trulyremote._roster_cache.update(rows=None, expires=0.0)


def _search(db, mode, **kw):
    s = Search(name="t", search_mode=mode, results_wanted=10, title_exclude_keywords=[],
               company_exclude=[], **kw)
    db.add(s)
    db.commit()
    return s


class _Resp:
    status_code = 200
    headers = {}

    def __init__(self, text):
        self.text = text


# ── R3 ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_trulyremote_keeps_a_body_excluded_job_as_ignored_and_reads_it_once(monkeypatch, test_db):
    search = _search(test_db, "trulyremote")
    rows = [{"title": t, "company": "Acme", "url": f"https://trw.test/jobs/{i}", "location": "Worldwide",
             "description": "blurb", "employment_type": "", "posted": "2026-10-16", "salary_min": None,
             "salary_max": None, "salary_text": "", "salary_usd_annual": False}
            for i, t in enumerate(["Clean Role", "Visa Role"])]
    bodies = {"https://trw.test/jobs/0": "A clean role.", "https://trw.test/jobs/1": "We do not sponsor a visa."}
    requested = []

    async def fake_roster(client):
        return [dict(r) for r in rows]

    async def fake_get(client, url, params=None):
        job_url = url[:-len(".md")]
        requested.append(job_url)
        return _Resp(f"# {next(r['title'] for r in rows if r['url'] == job_url)}\n\n**Company:** Acme\n\n"
                     f"## Description\n\n{bodies[job_url]}\n")

    monkeypatch.setattr(trulyremote, "_fetch_roster", fake_roster)
    monkeypatch.setattr(trulyremote, "_get", fake_get)

    first = await trulyremote.run(search)
    second = await trulyremote.run(search)
    test_db.expire_all()
    assert (first["new_jobs"], first["ignored_jobs"]) == (1, 1)
    assert {(j.title, j.status) for j in test_db.query(Job).all()} == {("Clean Role", "new"), ("Visa Role", "ignored")}
    assert sorted(requested) == sorted(bodies), "each page read once across both runs"
    assert (second["new_jobs"], second["ignored_jobs"]) == (0, 0)


@pytest.mark.asyncio
async def test_caribbeanjobs_keeps_a_body_excluded_job_as_ignored_and_reads_it_once(monkeypatch, test_db):
    search = _search(test_db, "caribbeanjobs", search_term="engineer")
    rows = [{"title": t, "company": "Acme", "url": f"https://cj.test/{i}", "location": "Jamaica",
             "description": "blurb", "posted": None, "employment_type": "", "expired": False,
             "salary_min": None, "salary_max": None}
            for i, t in enumerate(["Clean Role", "Visa Role"])]
    bodies = {"https://cj.test/0": "A clean role.", "https://cj.test/1": "We do not sponsor a visa."}
    requested = []
    ld = ('<html><script type="application/ld+json" id="jobPostingSchema">'
          '{{"@type": "JobPosting", "title": "{t}", "description": "{d}", "hiringOrganization": {{"name": "Acme"}}}}'
          '</script></html>')

    async def fake_listings(client, base, keywords, wanted):
        return [dict(r) for r in rows]

    async def fake_get(client, url, params=None):
        requested.append(url)
        return _Resp(ld.format(t=next(r["title"] for r in rows if r["url"] == url), d=bodies[url]))

    monkeypatch.setattr(caribbeanjobs, "_fetch_listings", fake_listings)
    monkeypatch.setattr(caribbeanjobs, "_get", fake_get)

    first = await caribbeanjobs.run(search)
    second = await caribbeanjobs.run(search)
    test_db.expire_all()
    assert (first["new_jobs"], first["ignored_jobs"]) == (1, 1)
    assert {(j.title, j.status) for j in test_db.query(Job).all()} == {("Clean Role", "new"), ("Visa Role", "ignored")}
    assert sorted(requested) == sorted(bodies), "each page read once across both runs"
    assert second["new_jobs"] == 0


# ── R10 ─────────────────────────────────────────────────────────────────────

def test_backfill_external_id_leaves_trulyremote_rows_alone(test_db):
    from backend.scripts.backfill_external_id import compute_new_ids, fetch_all_rows, resolve_updates

    board = "https://trulyremotework.com/jobs/staff-sre-fingerprint"
    tr = Job(id=uuid.uuid4(), external_id=make_external_id("Fingerprint", "Staff SRE", board),
             company="Fingerprint", title="Staff SRE", source="trulyremote", status="new",
             url="https://fingerprint.com/careers/jobs/apply/?gh_jid=123")
    other = Job(id=uuid.uuid4(), external_id="stale-id", company="Acme", title="PM",
                source="caribbeanjobs", status="new", url="https://www.caribbeanjobs.com/pm-job-1.aspx")
    legacy = Job(id=uuid.uuid4(), external_id="stale-too", company="Beta", title="PM",
                 source=None, status="new", url="https://beta.test/jobs/2")
    test_db.add_all([tr, other, legacy])
    test_db.commit()

    rows = fetch_all_rows(test_db)
    assert {r[0] for r in rows} == {other.id, legacy.id}, "trulyremote rows are not recomputed"
    updates, _skipped, _unchanged = resolve_updates(compute_new_ids(rows))
    assert {u[0] for u in updates} == {other.id, legacy.id}
