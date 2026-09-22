"""`location` holds a place, `country` holds the country, and one string carries both.

Indeed reads the country from `country_indeed` only. LinkedIn, ZipRecruiter and
Google read no country parameter at all: `location` is their only geography
signal. So a search for "Toronto" with country "usa" used to return 20 jobs from
Pittsburgh, PA and store them as new — no exception, no warning, wrong country.

The scraper now composes one location string, "<place>, <country label>", and
sends it to every board. These tests pin the composition, pin that both call
sites build the same string, and pin the migration that removes the country from
the stored location text.
"""
import logging

import pytest

from backend.models.db import Search


def _search(db, **kw):
    kw.setdefault("sources", ["indeed"])
    kw.setdefault("title_include_keywords", [])
    kw.setdefault("title_exclude_keywords", [])
    kw.setdefault("company_filter", [])
    kw.setdefault("company_exclude", [])
    kw.setdefault("country", "usa")
    kw.setdefault("search_mode", "keyword")
    s = Search(name="Location probe", active=True,
               search_term="program manager", **kw)
    db.add(s)
    db.commit()
    return s


def _record_kwargs(monkeypatch, calls):
    """Replace the real scrape_jobs and keep every kwargs dict it receives."""
    import jobspy
    import pandas as pd

    def scrape_jobs(**kwargs):
        calls.append(kwargs)
        return pd.DataFrame([])

    monkeypatch.setattr(jobspy, "scrape_jobs", scrape_jobs)


def _first_run_auth(db):
    """Empty dashboard_api_key triggers the middleware's first-run bypass."""
    from backend.models.db import Setting
    db.add(Setting(key="dashboard_api_key", value=""))
    db.commit()


# ── the composition ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("location,country,expected", [
    # A plain city gets the country spelled out. This is the case LinkedIn got
    # wrong: "Austin" alone resolved to Austin, Colorado.
    ("Austin", "usa", "Austin, United States"),
    ("Toronto", "canada", "Toronto, Canada"),
    # A city that already carries its country does not carry it twice.
    ("Toronto, Canada", "canada", "Toronto, Canada"),
    ("Cambridge, United Kingdom", "uk", "Cambridge, United Kingdom"),
    # `country` wins over the text, because the user picked that field.
    ("Toronto, Canada", "usa", "Toronto, United States"),
    # A region is not a country, so it survives. "ON" matches no jobspy alias.
    ("Toronto, ON", "canada", "Toronto, ON, Canada"),
    # An empty place gives the label alone: "Canada" is a valid query and
    # ", Canada" is not.
    ("", "canada", "Canada"),
    (None, "usa", "United States"),
    ("   ", "poland", "Poland"),
    # A location that is only a country name carries no place, so the text goes
    # and the country field decides. "United States" is the column default.
    ("United States", "usa", "United States"),
    ("US", "usa", "United States"),
    # A location that names ANOTHER country is refused by the write path, so
    # only a row stored before this change reaches the composition. See
    # `test_create_rejects_a_location_that_names_another_country`.
    ("Canada", "usa", "United States"),
    # Text that names no place still receives the country. Measured with "usa":
    # Indeed answers both spellings with the same 20 "Remote, US" rows, so the
    # country costs it nothing, while LinkedIn answers a bare "Remote" with jobs
    # in Taiwan, Japan and India. The forms point at the `is_remote` field.
    ("Remote", "usa", "Remote, United States"),
    ("Bay Area", "usa", "Bay Area, United States"),
    # An unknown or missing country falls back the way `country_indeed` does.
    ("Austin", None, "Austin, United States"),
    ("Austin", "Atlantis", "Austin, United States"),
])
def test_compose_location(location, country, expected):
    from backend.countries import compose_location
    assert compose_location(location, country) == expected


@pytest.mark.parametrize("location,expected", [
    ("Toronto, Canada", ("Toronto", "canada")),
    ("Toronto, ON", ("Toronto, ON", None)),
    ("Toronto, ON, Canada", ("Toronto, ON", "canada")),
    ("Canada", ("", "canada")),
    ("  Warsaw ,  Poland  ", ("Warsaw", "poland")),
    ("Remote", ("Remote", None)),
    ("", ("", None)),
    (None, ("", None)),
])
def test_split_country_suffix(location, expected):
    from backend.countries import split_country_suffix
    assert split_country_suffix(location) == expected


# ── both call sites compose the same string ──────────────────────────────────

@pytest.mark.parametrize("location,country", [
    ("Austin", "usa"),
    ("Toronto", "canada"),
    ("Toronto, Canada", "usa"),
    ("Toronto, ON", "canada"),
    ("", "canada"),
    ("Remote", "uk"),
])
def test_the_scraper_and_the_test_preview_send_the_same_location(
    api_client, test_db, monkeypatch, location, country
):
    """One composition, two callers. This test fails if either side drifts."""
    from backend.scraper.sources.jobspy import _run_sync

    _first_run_auth(test_db)
    search = _search(test_db, location=location, country=country)

    calls = []
    _record_kwargs(monkeypatch, calls)

    _run_sync(search)
    resp = api_client.post(f"/api/searches/{search.id}/test")
    assert resp.status_code == 200, f"Unexpected {resp.status_code}: {resp.text}"

    assert len(calls) == 2, f"expected one call per site, got {len(calls)}"
    scraper_loc, preview_loc = calls[0]["location"], calls[1]["location"]
    assert scraper_loc == preview_loc, \
        f"the two call sites drifted: {scraper_loc!r} vs {preview_loc!r}"

    from backend.countries import compose_location
    assert scraper_loc == compose_location(location, country)


def test_the_scraper_sends_the_country_to_indeed_and_to_the_location(test_db, monkeypatch):
    """`country_indeed` still reaches Indeed, and the composed string reaches the rest."""
    from backend.scraper.sources.jobspy import _run_sync

    calls = []
    _record_kwargs(monkeypatch, calls)
    _run_sync(_search(test_db, location="Austin", country="usa",
                      sources=["indeed", "linkedin", "zip_recruiter", "google"]))

    assert calls[0]["country_indeed"] == "usa"
    assert calls[0]["location"] == "Austin, United States"


def test_an_empty_location_does_not_send_a_leading_comma(test_db, monkeypatch):
    """", Canada" is not a query. "Canada" is, and it returns rows."""
    from backend.scraper.sources.jobspy import _run_sync

    calls = []
    _record_kwargs(monkeypatch, calls)
    _run_sync(_search(test_db, location="", country="canada"))

    assert calls[0]["location"] == "Canada"


# ── the migration that removes the country from the stored text ──────────────

def test_the_migration_removes_only_a_country_segment(test_db):
    from backend.seed import _strip_country_from_search_location

    with_country = _search(test_db, location="Toronto, Canada", country="canada")
    with_region = _search(test_db, location="Toronto, ON", country="canada")
    plain = _search(test_db, location="Austin", country="usa")
    only_country = _search(test_db, location="United States", country="usa")

    _strip_country_from_search_location(test_db)

    for row in (with_country, with_region, plain, only_country):
        test_db.refresh(row)
    assert with_country.location == "Toronto"
    assert with_region.location == "Toronto, ON", "a region is not a country"
    assert plain.location == "Austin"
    # The last segment always stays. Removing it would strip one more segment on
    # every restart for a name that repeats itself. `compose_location()` drops a
    # country-only location anyway, so the query is right either way.
    assert only_country.location == "United States"


def test_the_migration_keeps_the_country_field_when_the_text_disagrees(test_db, caplog):
    """`country` is the field the user picked, so the text loses."""
    from backend.seed import _strip_country_from_search_location

    search = _search(test_db, location="Toronto, Canada", country="usa")

    with caplog.at_level(logging.WARNING):
        _strip_country_from_search_location(test_db)

    test_db.refresh(search)
    assert search.country == "usa", "the explicitly chosen field must survive"
    assert search.location == "Toronto"
    assert any("keeping country" in r.getMessage() for r in caplog.records), \
        "a contradicting row must be logged"

    # Known residual, pinned on purpose: the first pass removed the only
    # evidence. From the second startup on, the row reads as a deliberate
    # "Toronto" + usa search and nothing logs it again. It composes to
    # "Toronto, United States" with no signal.
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        _strip_country_from_search_location(test_db)

    test_db.refresh(search)
    assert search.location == "Toronto"
    assert not [r for r in caplog.records if "keeping country" in r.getMessage()], \
        "the contradiction is silent after the first startup"


def test_the_migration_logs_a_country_only_location_that_disagrees(test_db, caplog):
    """The text stays, so only the log tells an operator this row scrapes the
    country its text denies. The write path refuses to create a new one."""
    from backend.seed import _strip_country_from_search_location

    search = _search(test_db, location="Canada", country="usa")

    with caplog.at_level(logging.WARNING):
        _strip_country_from_search_location(test_db)

    test_db.refresh(search)
    assert search.location == "Canada", "the last segment always stays"
    assert any("keeping country" in r.getMessage() for r in caplog.records)


def test_the_migration_is_idempotent(test_db):
    """Three runs, because the first version was stable only for rows that never doubled."""
    from backend.seed import _strip_country_from_search_location

    rows = [
        _search(test_db, location="Toronto, Canada", country="canada"),
        _search(test_db, location="Toronto, ON", country="canada"),
        _search(test_db, location="United States", country="usa"),
        _search(test_db, location="Remote", country="usa"),
        # A city whose name repeats its country. Stripping one segment per run
        # would widen each of these to the whole country on the next restart.
        _search(test_db, location="Luxembourg, Luxembourg", country="luxembourg"),
        _search(test_db, location="Mexico, Mexico", country="mexico"),
        _search(test_db, location="Panama, Panama", country="panama"),
        _search(test_db, location="Kuwait, Kuwait", country="kuwait"),
        _search(test_db, location="Singapore, Singapore", country="singapore"),
    ]
    _strip_country_from_search_location(test_db)
    for row in rows:
        test_db.refresh(row)
    after_first = [r.location for r in rows]
    assert "Luxembourg" in after_first and "Mexico" in after_first

    for _ in range(2):
        _strip_country_from_search_location(test_db)
        for row in rows:
            test_db.refresh(row)
        assert [r.location for r in rows] == after_first, \
            "a later run must change nothing"


def test_the_migration_keeps_the_last_segment(test_db):
    """The doubled-name case, spelled out. Two runs used to leave an empty location."""
    from backend.seed import _strip_country_from_search_location

    row = _search(test_db, location="Luxembourg, Luxembourg", country="luxembourg")
    _strip_country_from_search_location(test_db)
    test_db.refresh(row)
    assert row.location == "Luxembourg"

    _strip_country_from_search_location(test_db)
    test_db.refresh(row)
    assert row.location == "Luxembourg", "a second restart must not widen the search"


def test_the_migration_settles_a_repeated_country_in_one_pass(test_db):
    """One pass removes every country segment, not one segment per pass."""
    from backend.seed import _strip_country_from_search_location

    row = _search(test_db, location="Mexico, Mexico, Mexico", country="mexico")
    _strip_country_from_search_location(test_db)
    test_db.refresh(row)
    assert row.location == "Mexico"


def test_the_migration_leaves_other_search_modes_alone(test_db):
    """`jobright` reads search.location raw and drops the parameter when it is empty.

    It never reaches `compose_location()`, so rewriting its rows would move this
    defect to a board this change never measured.
    """
    from backend.seed import _strip_country_from_search_location

    jobright = _search(test_db, location="Vancouver, Canada", country="usa",
                       search_mode="jobright")
    keyword = _search(test_db, location="Vancouver, Canada", country="canada")

    _strip_country_from_search_location(test_db)

    test_db.refresh(jobright)
    test_db.refresh(keyword)
    assert jobright.location == "Vancouver, Canada", "only keyword rows are composed"
    assert keyword.location == "Vancouver"


def test_run_migrations_calls_the_location_strip(test_db, monkeypatch):
    """Without this pin, deleting the call leaves the country in every stored location."""
    import backend.seed as seed

    called = []
    monkeypatch.setattr(seed, "_strip_country_from_search_location",
                        lambda db: called.append(db))
    # SQLite rejects `ADD COLUMN IF NOT EXISTS`, so the guard would skip the data
    # migrations for a reason this test is not about. Report a clean list.
    monkeypatch.setattr(seed, "run_migration_statements", lambda db, statements: [])

    seed.run_migrations(test_db)

    assert called, "run_migrations must call the location strip"


def test_the_strip_runs_after_the_country_backfill(test_db, monkeypatch):
    """The backfill reads the country out of the location text the strip removes."""
    import backend.seed as seed

    order = []
    monkeypatch.setattr(seed, "_backfill_search_country",
                        lambda db: order.append("backfill"))
    monkeypatch.setattr(seed, "_strip_country_from_search_location",
                        lambda db: order.append("strip"))
    monkeypatch.setattr(seed, "run_migration_statements", lambda db, statements: [])

    seed.run_migrations(test_db)

    assert order == ["backfill", "strip"]


def test_the_strip_is_skipped_when_the_country_column_was_not_added(test_db, monkeypatch):
    """It reads searches.country. Without the column it raises out of the lifespan."""
    import backend.seed as seed

    called = []
    monkeypatch.setattr(seed, "_strip_country_from_search_location",
                        lambda db: called.append(db))
    monkeypatch.setattr(seed, "run_migration_statements",
                        lambda db, statements: [seed._ADD_COUNTRY_COLUMN])

    seed.run_migrations(test_db)

    assert called == [], "a missing column must not reach the strip"


def test_a_raising_strip_does_not_abort_the_migration(test_db, monkeypatch):
    """run_seeds() runs inside the FastAPI lifespan — no step may take the container down."""
    import backend.seed as seed

    def boom(db):
        raise RuntimeError("column vanished")

    monkeypatch.setattr(seed, "_strip_country_from_search_location", boom)
    monkeypatch.setattr(seed, "run_migration_statements", lambda db, statements: [])

    seed.run_migrations(test_db)      # raises if the guard is missing


# ── the write path refuses a pair that disagrees ─────────────────────────────

def _payload(**kw):
    body = {"name": "New search", "search_mode": "keyword", "search_term": "pm"}
    body.update(kw)
    return body


def test_create_rejects_a_location_that_names_another_country(api_client, test_db):
    """The pair used to fail loudly: Indeed answered zero rows and the health
    check raised a warning. Composed, it becomes a silent wrong-country scrape,
    so the API refuses it instead of choosing a side for the user."""
    _first_run_auth(test_db)
    resp = api_client.post("/api/searches",
                           json=_payload(location="Toronto, Canada", country="usa"))

    assert resp.status_code == 400, f"Unexpected {resp.status_code}: {resp.text}"
    assert "canada" in resp.json()["detail"].lower()
    assert test_db.query(Search).filter(Search.name == "New search").count() == 0


def test_create_rejects_a_location_that_is_another_country(api_client, test_db):
    """The case the composition would otherwise swallow whole: the text vanishes."""
    _first_run_auth(test_db)
    resp = api_client.post("/api/searches",
                           json=_payload(location="Canada", country="usa"))
    assert resp.status_code == 400, f"Unexpected {resp.status_code}: {resp.text}"


@pytest.mark.parametrize("location,country", [
    ("Toronto, Canada", "canada"),   # the text agrees with the field
    ("Toronto, ON", "canada"),       # a region is not a country
    ("Toronto", "canada"),
    ("Remote", "usa"),
    ("", "canada"),
])
def test_create_accepts_a_location_that_does_not_disagree(api_client, test_db,
                                                          location, country):
    _first_run_auth(test_db)
    resp = api_client.post("/api/searches",
                           json=_payload(location=location, country=country))
    assert resp.status_code == 200, f"Unexpected {resp.status_code}: {resp.text}"


def test_patch_rejects_a_location_that_contradicts_the_stored_country(api_client, test_db):
    _first_run_auth(test_db)
    search = _search(test_db, location="Austin", country="usa")

    resp = api_client.patch(f"/api/searches/{search.id}",
                            json={"location": "Toronto, Canada"})

    assert resp.status_code == 400, f"Unexpected {resp.status_code}: {resp.text}"
    test_db.refresh(search)
    assert search.location == "Austin", "a rejected write must leave the row alone"


def test_patch_rejects_a_country_that_contradicts_the_stored_location(api_client, test_db):
    """The same pair, reached from the other field."""
    _first_run_auth(test_db)
    search = _search(test_db, location="Toronto, Canada", country="canada")

    resp = api_client.patch(f"/api/searches/{search.id}", json={"country": "usa"})

    assert resp.status_code == 400, f"Unexpected {resp.status_code}: {resp.text}"
    test_db.refresh(search)
    assert search.country == "canada", "a rejected write must leave the row alone"


def test_patch_accepts_the_pair_when_both_fields_move_together(api_client, test_db):
    """Changing both at once is how a user moves a search to another country."""
    _first_run_auth(test_db)
    search = _search(test_db, location="Toronto, Canada", country="canada")

    resp = api_client.patch(f"/api/searches/{search.id}",
                            json={"location": "Austin", "country": "usa"})

    assert resp.status_code == 200, f"Unexpected {resp.status_code}: {resp.text}"
    test_db.refresh(search)
    assert (search.location, search.country) == ("Austin", "usa")


def test_patch_leaves_the_pair_alone_when_neither_field_moves(api_client, test_db):
    """A legacy row stays editable: renaming it must not fail on its own text."""
    _first_run_auth(test_db)
    search = _search(test_db, location="Toronto, Canada", country="usa")

    resp = api_client.patch(f"/api/searches/{search.id}", json={"name": "Renamed"})

    assert resp.status_code == 200, f"Unexpected {resp.status_code}: {resp.text}"


# ── the backfill says which rows it guessed ──────────────────────────────────

def test_the_backfill_logs_a_location_that_names_no_country(test_db, caplog):
    """A region code reads as no country, so the backfill guesses usa and the
    scraper then sends "Toronto, ON, United States". An operator must see it."""
    from backend.seed import _backfill_search_country

    search = _search(test_db, location="Toronto, ON")
    search.country = None
    test_db.commit()

    with caplog.at_level(logging.WARNING):
        _backfill_search_country(test_db)

    test_db.refresh(search)
    assert search.country == "usa"
    assert any("guessed" in r.getMessage() for r in caplog.records), \
        "a guessed country must be logged"


def test_the_backfill_stays_quiet_when_it_reads_a_country(test_db, caplog):
    from backend.seed import _backfill_search_country

    for location in ("Toronto, Canada", "", None):
        row = _search(test_db, location=location)
        row.country = None
        test_db.commit()

    with caplog.at_level(logging.WARNING):
        _backfill_search_country(test_db)

    assert not [r for r in caplog.records if "guessed" in r.getMessage()]


# ── jobright reads the location raw ───────────────────────────────────────────

class _FakeResponse:
    text = '<script id="__NEXT_DATA__" type="application/json">' \
           '{"props": {"pageProps": {"jobList": [], "totalJobs": 0}}}</script>'

    def raise_for_status(self):
        return None


def _record_jobright_params(monkeypatch, sent):
    """Stub the session and the HTTP client, and keep the params of every GET."""
    from backend.scraper.sources import jobright

    async def _session(force_relogin=False):
        return "session"

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url, params=None, **kwargs):
            sent.append(dict(params or {}))
            return _FakeResponse()

    monkeypatch.setattr(jobright, "_ensure_session", _session)
    monkeypatch.setattr(jobright.httpx, "AsyncClient", _Client)


def test_a_new_jobright_search_sends_no_location(api_client, test_db, monkeypatch):
    """A search created now stores "", and jobright receives no location parameter.

    The search goes through the real write path, so this test fails if the
    column default or the SearchCreate default returns to a country name.
    """
    import asyncio
    from backend.scraper.sources import jobright

    _first_run_auth(test_db)
    resp = api_client.post("/api/searches", json={
        "name": "New jobright", "search_mode": "jobright", "search_term": "program manager",
    })
    assert resp.status_code == 200, f"Unexpected {resp.status_code}: {resp.text}"
    search = test_db.query(Search).filter(Search.name == "New jobright").one()
    assert search.location == ""

    sent = []
    _record_jobright_params(monkeypatch, sent)
    asyncio.run(jobright.preview(search, test_db))

    assert sent, "the SSR search was never requested"
    assert "location" not in sent[0], f"a new jobright search sent {sent[0]}"


def test_an_old_jobright_search_still_sends_its_stored_location(test_db, monkeypatch):
    """The other population, pinned so that the difference stays a decision.

    The strip skips jobright rows, so an old row keeps the old default and sends
    it. Measured, jobright answers it the same way as no location.
    """
    import asyncio
    from backend.scraper.sources import jobright

    search = _search(test_db, search_mode="jobright", location="United States")

    sent = []
    _record_jobright_params(monkeypatch, sent)
    asyncio.run(jobright.preview(search, test_db))

    assert sent and sent[0].get("location") == "United States"
