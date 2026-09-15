"""Tests for analyzer/h1b_checker — scan_jd, determine_verdict, refresh-preserves-cache."""
import pytest
from unittest.mock import AsyncMock


# ── scan_jd_for_h1b_flags ────────────────────────────────────────────────────

def test_scan_jd_for_h1b_flags_match():
    """JD contains an exclusion phrase → jd_flag=True + snippet populated."""
    from backend.analyzer.h1b_checker import scan_jd_for_h1b_flags

    description = (
        "Great role for an experienced PM. Unfortunately we do not sponsor "
        "visas for this position. Strong culture and benefits."
    )
    phrases = ["no visa sponsorship", "do not sponsor"]

    result = scan_jd_for_h1b_flags(description, phrases)

    assert result["jd_flag"] is True
    assert result["jd_snippet"] is not None
    assert "sponsor" in result["jd_snippet"].lower()


def test_scan_jd_for_h1b_flags_no_match():
    """JD with no exclusion phrases → jd_flag=False, snippet=None."""
    from backend.analyzer.h1b_checker import scan_jd_for_h1b_flags

    description = "Great role. We offer competitive benefits and equity."
    phrases = ["no visa sponsorship", "do not sponsor"]

    result = scan_jd_for_h1b_flags(description, phrases)

    assert result["jd_flag"] is False
    assert result["jd_snippet"] is None


def test_scan_jd_for_h1b_flags_empty_description():
    """Empty description → jd_flag=False."""
    from backend.analyzer.h1b_checker import scan_jd_for_h1b_flags

    result = scan_jd_for_h1b_flags("", ["no sponsorship"])
    assert result["jd_flag"] is False
    assert result["jd_snippet"] is None


def test_scan_jd_for_h1b_flags_case_insensitive():
    """Phrase match is case-insensitive."""
    from backend.analyzer.h1b_checker import scan_jd_for_h1b_flags

    description = "We DO NOT SPONSOR visas for this role."
    phrases = ["do not sponsor"]

    result = scan_jd_for_h1b_flags(description, phrases)
    assert result["jd_flag"] is True


# ── determine_h1b_verdict (5-branch logic) ───────────────────────────────────

def test_determine_h1b_verdict_likely():
    """lca_count > 50, no jd_flag → 'likely'."""
    from backend.analyzer.h1b_checker import determine_h1b_verdict
    assert determine_h1b_verdict(100, False) == "likely"
    assert determine_h1b_verdict(51, False) == "likely"


def test_determine_h1b_verdict_possible():
    """10 <= lca_count <= 50, no jd_flag → 'possible'."""
    from backend.analyzer.h1b_checker import determine_h1b_verdict
    assert determine_h1b_verdict(10, False) == "possible"
    assert determine_h1b_verdict(50, False) == "possible"


def test_determine_h1b_verdict_unlikely_low_count():
    """lca_count 1..9, no jd_flag → 'unlikely'."""
    from backend.analyzer.h1b_checker import determine_h1b_verdict
    assert determine_h1b_verdict(5, False) == "unlikely"
    assert determine_h1b_verdict(1, False) == "unlikely"


def test_determine_h1b_verdict_unknown_zero():
    """lca_count == 0, no jd_flag → 'unknown'."""
    from backend.analyzer.h1b_checker import determine_h1b_verdict
    assert determine_h1b_verdict(0, False) == "unknown"


def test_determine_h1b_verdict_jd_flag_wins():
    """JD flag overrides LCA count → always 'unlikely'."""
    from backend.analyzer.h1b_checker import determine_h1b_verdict
    # JD flag wins even with high LCA count
    assert determine_h1b_verdict(500, True) == "unlikely"
    assert determine_h1b_verdict(10, True) == "unlikely"
    assert determine_h1b_verdict(0, True) == "unlikely"


# ── resolve_company_h1b (VisaCache is the source of truth) ───────────────────

def _seed_cache(db, name, lca, med, days_ago=0, has=True):
    from backend.models.db import VisaCache
    from datetime import datetime, timezone, timedelta
    db.add(VisaCache(name_key=name.strip().lower(), country="US", display_name=name,
                     lca_count=lca, approval_rate=90.0, median_salary=med, has_data=has,
                     fetched_at=datetime.now(timezone.utc) - timedelta(days=days_ago)))
    db.commit()


@pytest.mark.asyncio
async def test_resolve_cache_hit_no_fetch(test_db, monkeypatch):
    from backend.analyzer import h1b_checker
    _seed_cache(test_db, "Acme", 120, 150000, days_ago=1)
    called = {"n": 0}
    async def fake(*a, **k):
        called["n"] += 1
        return {"lca_count": 0, "approval_rate": 0, "median_salary": 0}
    monkeypatch.setattr(h1b_checker, "fetch_company_h1b_data", fake)
    data = await h1b_checker.resolve_company_h1b(test_db, "Acme")
    assert data["lca_count"] == 120 and called["n"] == 0  # fresh hit, no live fetch


@pytest.mark.asyncio
async def test_resolve_miss_does_live_fetch(test_db, monkeypatch):
    from backend.models.db import VisaCache
    from backend.analyzer import h1b_checker
    monkeypatch.setattr(h1b_checker, "_budget", h1b_checker._LiveBudget())
    async def fake(name, h1b_slug=None):
        return {"lca_count": 300, "approval_rate": 97.0, "median_salary": 190000}
    monkeypatch.setattr(h1b_checker, "fetch_company_h1b_data", fake)
    data = await h1b_checker.resolve_company_h1b(test_db, "NewCo")
    assert data["lca_count"] == 300 and data["has_data"] is True
    row = test_db.query(VisaCache).filter(VisaCache.name_key == "newco").first()
    assert row and row.median_salary == 190000


@pytest.mark.asyncio
async def test_resolve_negative_cache(test_db, monkeypatch):
    from backend.models.db import VisaCache
    from backend.analyzer import h1b_checker
    monkeypatch.setattr(h1b_checker, "_budget", h1b_checker._LiveBudget())
    async def zero(name, h1b_slug=None):
        return {"lca_count": 0, "approval_rate": 0, "median_salary": 0}
    monkeypatch.setattr(h1b_checker, "fetch_company_h1b_data", zero)
    data = await h1b_checker.resolve_company_h1b(test_db, "NoData")
    assert data["has_data"] is False
    row = test_db.query(VisaCache).filter(VisaCache.name_key == "nodata").first()
    assert row and row.has_data is False and row.fetched_at is not None  # negative cached


@pytest.mark.asyncio
async def test_resolve_allow_live_false_no_fetch(test_db, monkeypatch):
    from backend.analyzer import h1b_checker
    called = {"n": 0}
    async def fake(*a, **k):
        called["n"] += 1
        return {"lca_count": 1, "approval_rate": 0, "median_salary": 0}
    monkeypatch.setattr(h1b_checker, "fetch_company_h1b_data", fake)
    data = await h1b_checker.resolve_company_h1b(test_db, "Ghost", allow_live=False)
    assert data is None and called["n"] == 0


@pytest.mark.asyncio
async def test_resolve_preserves_good_data_on_zero_fetch(test_db, monkeypatch):
    from backend.models.db import VisaCache
    from backend.analyzer import h1b_checker
    monkeypatch.setattr(h1b_checker, "_budget", h1b_checker._LiveBudget())
    _seed_cache(test_db, "CachedCo", 250, 180000, days_ago=200)  # stale but has data
    async def zero(name, h1b_slug=None):
        return {"lca_count": 0, "approval_rate": 0, "median_salary": 0}
    monkeypatch.setattr(h1b_checker, "fetch_company_h1b_data", zero)
    await h1b_checker.resolve_company_h1b(test_db, "CachedCo", force=True)
    row = test_db.query(VisaCache).filter(VisaCache.name_key == "cachedco").first()
    assert row.lca_count == 250 and row.median_salary == 180000  # not wiped by the zero fetch


# ── budget / rate-limit breaker ──────────────────────────────────────────────

def test_live_budget_breaker():
    from backend.analyzer.h1b_checker import _LiveBudget
    b = _LiveBudget()
    assert b.allow()
    for _ in range(_LiveBudget.MAX_RATE_STRIKES):
        b.note_rate_limit()
    assert not b.allow()          # 3 strikes → stop
    b.start = 0.0                  # roll the window
    assert b.allow()              # reset
    b2 = _LiveBudget()
    for _ in range(_LiveBudget.MAX_LOOKUPS):
        b2.note_lookup()
    assert not b2.allow()         # lookup cap reached


@pytest.mark.asyncio
async def test_resolve_rate_limit_counts_strike(test_db, monkeypatch):
    from backend.analyzer import h1b_checker
    monkeypatch.setattr(h1b_checker, "_budget", h1b_checker._LiveBudget())
    async def rl(name, h1b_slug=None):
        raise h1b_checker.H1bRateLimited("429")
    monkeypatch.setattr(h1b_checker, "fetch_company_h1b_data", rl)
    await h1b_checker.resolve_company_h1b(test_db, "Blocked")
    assert h1b_checker._budget.strikes == 1


# ── check_job_h1b reads the cache + stashes median ───────────────────────────

@pytest.mark.asyncio
async def test_check_job_h1b_verdict_from_cache(test_db, monkeypatch):
    from backend.models.db import Setting
    from backend.analyzer import h1b_checker
    test_db.add(Setting(key="body_exclusion_phrases", value="[]"))
    _seed_cache(test_db, "BigCo", 200, 175000, days_ago=1)

    class J:
        company = "BigCo"
        description = "Great role"
    j = J()
    await h1b_checker.check_job_h1b(j, test_db)
    assert j.h1b_verdict == "likely"
    assert j.h1b_company_lca_count == 200
    assert j._h1b_median == 175000


# ── h1bdata.info fallback ────────────────────────────────────────────────────

class _FakeResp:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text
        self.url = ""


class _FakeClient:
    def __init__(self, resp):
        self._resp = resp
    async def __aenter__(self):
        return self
    async def __aexit__(self, *a):
        return False
    async def get(self, url, headers=None):
        return self._resp


_H1BDATA_HTML = """
<table><tr><th>Employer</th><th>Job</th><th>Salary</th><th>Loc</th><th>Sub</th><th>Start</th></tr>
<tr><td>TWILIO INC</td><td>PM</td><td>125,000</td><td>SF, CA</td><td>06/07/2025</td><td>10/01/2025</td></tr>
<tr><td>TWILIO INC</td><td>ENG</td><td>175,000</td><td>NY, NY</td><td>05/06/2025</td><td>09/29/2025</td></tr>
<tr><td>TWILIO INC</td><td>DS</td><td>150,000</td><td>MIA, FL</td><td>05/06/2025</td><td>09/29/2025</td></tr>
</table>
"""


@pytest.mark.asyncio
async def test_fetch_h1bdata_parses_rows(monkeypatch):
    from backend.analyzer import h1b_checker
    monkeypatch.setattr(h1b_checker.httpx, "AsyncClient",
                        lambda *a, **k: _FakeClient(_FakeResp(200, _H1BDATA_HTML)))
    data = await h1b_checker._fetch_h1bdata("Twilio")
    assert data["lca_count"] == 3
    assert data["median_salary"] == 150000  # median of 125k/150k/175k
    assert data["approval_rate"] == 0.0


@pytest.mark.asyncio
async def test_fetch_h1bdata_raises_on_403(monkeypatch):
    from backend.analyzer import h1b_checker
    monkeypatch.setattr(h1b_checker.httpx, "AsyncClient",
                        lambda *a, **k: _FakeClient(_FakeResp(403)))
    with pytest.raises(h1b_checker.H1bRateLimited):
        await h1b_checker._fetch_h1bdata("Twilio")


@pytest.mark.asyncio
async def test_fetch_falls_back_when_myvisajobs_blocked(monkeypatch):
    """MyVisaJobs 403 → h1bdata fallback supplies the data, no exception."""
    from backend.analyzer import h1b_checker
    async def mvj(name, h1b_slug=None):
        raise h1b_checker.H1bRateLimited("myvisajobs 403")
    async def fb(name):
        return {"lca_count": 42, "approval_rate": 0.0, "median_salary": 160000}
    monkeypatch.setattr(h1b_checker, "_fetch_myvisajobs", mvj)
    monkeypatch.setattr(h1b_checker, "_fetch_h1bdata", fb)
    data = await h1b_checker.fetch_company_h1b_data("Twilio")
    assert data["lca_count"] == 42 and data["median_salary"] == 160000


@pytest.mark.asyncio
async def test_fetch_falls_back_when_myvisajobs_empty(monkeypatch):
    """MyVisaJobs 200-but-empty → still tries h1bdata for a better answer."""
    from backend.analyzer import h1b_checker
    async def mvj(name, h1b_slug=None):
        return {"lca_count": 0, "approval_rate": 0, "median_salary": 0}
    async def fb(name):
        return {"lca_count": 99, "approval_rate": 0.0, "median_salary": 140000}
    monkeypatch.setattr(h1b_checker, "_fetch_myvisajobs", mvj)
    monkeypatch.setattr(h1b_checker, "_fetch_h1bdata", fb)
    data = await h1b_checker.fetch_company_h1b_data("Twilio")
    assert data["lca_count"] == 99


@pytest.mark.asyncio
async def test_fetch_myvisajobs_wins_when_it_has_data(monkeypatch):
    """MyVisaJobs has data → fallback never called (richest source)."""
    from backend.analyzer import h1b_checker
    async def mvj(name, h1b_slug=None):
        return {"lca_count": 500, "approval_rate": 95.0, "median_salary": 180000}
    called = {"n": 0}
    async def fb(name):
        called["n"] += 1
        return {"lca_count": 1, "approval_rate": 0, "median_salary": 0}
    monkeypatch.setattr(h1b_checker, "_fetch_myvisajobs", mvj)
    monkeypatch.setattr(h1b_checker, "_fetch_h1bdata", fb)
    data = await h1b_checker.fetch_company_h1b_data("Google")
    assert data["approval_rate"] == 95.0 and called["n"] == 0


@pytest.mark.asyncio
async def test_fetch_raises_when_both_blocked(monkeypatch):
    """Both sources 403 → H1bRateLimited propagates so the breaker trips."""
    from backend.analyzer import h1b_checker
    async def mvj(name, h1b_slug=None):
        raise h1b_checker.H1bRateLimited("myvisajobs 403")
    async def fb(name):
        raise h1b_checker.H1bRateLimited("h1bdata 403")
    monkeypatch.setattr(h1b_checker, "_fetch_myvisajobs", mvj)
    monkeypatch.setattr(h1b_checker, "_fetch_h1bdata", fb)
    with pytest.raises(h1b_checker.H1bRateLimited):
        await h1b_checker.fetch_company_h1b_data("Twilio")


# ── migration: legacy companies.h1b_* → visa_cache ───────────────────────────

def test_migrate_seeds_visa_cache_from_legacy_columns(test_db):
    from sqlalchemy import text
    from backend.models.db import Company, VisaCache
    from backend.seed import migrate_h1b_to_visa_cache
    for col, typ in [("h1b_lca_count", "INTEGER"), ("h1b_approval_rate", "FLOAT"),
                     ("h1b_median_salary", "INTEGER"), ("h1b_last_checked", "DATETIME")]:
        test_db.execute(text(f"ALTER TABLE companies ADD COLUMN {col} {typ}"))
    test_db.add(Company(name="LegacyCo"))
    test_db.commit()
    test_db.execute(text("update companies set h1b_lca_count=320, h1b_approval_rate=92.0, "
                         "h1b_median_salary=165000 where name='LegacyCo'"))
    test_db.commit()

    migrate_h1b_to_visa_cache(test_db)

    row = test_db.query(VisaCache).filter(VisaCache.name_key == "legacyco").first()
    assert row and row.lca_count == 320 and row.median_salary == 165000 and row.has_data is True
