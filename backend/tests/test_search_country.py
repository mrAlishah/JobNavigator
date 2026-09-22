"""searches.country picks the Indeed domain and the `indeed-co` API header.

The field used to be the literal "USA" at both call sites, so a search for
Canadian cities asked the US Indeed site. Indeed answered HTTP 200 with an empty
result list: no exception, no error row, no rows. These tests pin the country as
a per-search value and pin the backfill that derives it from existing rows.
"""
import pytest

from backend.models.db import Search


def _search(db, **kw):
    kw.setdefault("sources", ["indeed"])
    kw.setdefault("title_include_keywords", [])
    kw.setdefault("title_exclude_keywords", [])
    kw.setdefault("company_filter", [])
    kw.setdefault("company_exclude", [])
    s = Search(name="Country probe", search_mode="keyword", active=True,
               search_term="program manager", **kw)
    db.add(s)
    db.commit()
    return s


def _record_kwargs(monkeypatch, calls):
    """Replace the real scrape_jobs and keep every kwargs dict it receives (same stubbing style as test_routes_searches._stub_jobspy)."""
    import jobspy
    import pandas as pd

    def scrape_jobs(**kwargs):
        calls.append(kwargs)
        return pd.DataFrame([])

    monkeypatch.setattr(jobspy, "scrape_jobs", scrape_jobs)


def _with_null_country(db, search):
    """Clear the column the way an ALTER TABLE ADD COLUMN leaves it.

    The model carries a Python default, and SQLAlchemy applies that default on
    INSERT even for an explicit None — so a row is made first and cleared after.
    """
    search.country = None
    db.commit()
    return search


def _first_run_auth(db):
    """Empty dashboard_api_key triggers the middleware's first-run bypass."""
    from backend.models.db import Setting
    db.add(Setting(key="dashboard_api_key", value=""))
    db.commit()


# ── the scraper hands the search's own country to jobspy ─────────────────────

@pytest.mark.parametrize("stored,expected", [
    ("canada", "canada"),
    ("poland", "poland"),
    ("usa", "usa"),
])
def test_scrape_kwargs_carry_the_search_country(test_db, monkeypatch, stored, expected):
    from backend.scraper.sources.jobspy import _run_sync

    calls = []
    _record_kwargs(monkeypatch, calls)
    _run_sync(_search(test_db, location="Toronto, Canada", country=stored))

    assert calls, "scrape_jobs was never called"
    assert calls[0]["country_indeed"] == expected


def test_country_indeed_is_not_a_constant(test_db, monkeypatch):
    """Two searches, two countries, one run each — a hardcoded literal fails here."""
    from backend.scraper.sources.jobspy import _run_sync

    calls = []
    _record_kwargs(monkeypatch, calls)
    _run_sync(_search(test_db, location="Toronto, Canada", country="canada"))
    _run_sync(_search(test_db, location="Warsaw, Poland", country="poland"))

    sent = [c["country_indeed"] for c in calls]
    assert sent == ["canada", "poland"]
    assert len(set(sent)) == 2, f"country_indeed did not vary between searches: {sent}"


def test_test_preview_sends_the_search_country(api_client, test_db, monkeypatch):
    """The /test preview built the same kwargs dict and carried the same literal."""
    _first_run_auth(test_db)
    search = _search(test_db, location="Toronto, Canada", country="canada")

    calls = []
    _record_kwargs(monkeypatch, calls)
    resp = api_client.post(f"/api/searches/{search.id}/test")

    assert resp.status_code == 200, f"Unexpected {resp.status_code}: {resp.text}"
    assert calls[0]["country_indeed"] == "canada"


# ── the API write path validates the field ───────────────────────────────────

def _payload(**kw):
    body = {"name": "New search", "search_mode": "keyword", "search_term": "pm"}
    body.update(kw)
    return body


def test_create_rejects_an_unknown_country(api_client, test_db):
    _first_run_auth(test_db)
    resp = api_client.post("/api/searches", json=_payload(country="Atlantis"))
    assert resp.status_code == 400, f"Unexpected {resp.status_code}: {resp.text}"
    assert "Atlantis" in resp.json()["detail"]


def test_create_stores_the_canonical_spelling(api_client, test_db):
    """jobspy accepts several aliases per country; one of them is stored."""
    _first_run_auth(test_db)
    resp = api_client.post("/api/searches", json=_payload(country="United States"))
    assert resp.status_code == 200, f"Unexpected {resp.status_code}: {resp.text}"
    assert resp.json()["country"] == "usa"

    resp = api_client.post("/api/searches", json=_payload(name="Second", country="CANADA"))
    assert resp.status_code == 200, f"Unexpected {resp.status_code}: {resp.text}"
    assert resp.json()["country"] == "canada"


def test_create_defaults_to_usa(api_client, test_db):
    _first_run_auth(test_db)
    resp = api_client.post("/api/searches", json=_payload())
    assert resp.status_code == 200, f"Unexpected {resp.status_code}: {resp.text}"
    assert resp.json()["country"] == "usa"


def test_patch_rejects_an_unknown_country(api_client, test_db):
    _first_run_auth(test_db)
    search = _search(test_db, country="usa")
    resp = api_client.patch(f"/api/searches/{search.id}", json={"country": "Narnia"})
    assert resp.status_code == 400, f"Unexpected {resp.status_code}: {resp.text}"

    test_db.refresh(search)
    assert search.country == "usa", "a rejected write must leave the row alone"


def test_patch_accepts_a_known_country(api_client, test_db):
    _first_run_auth(test_db)
    search = _search(test_db, country="usa")
    resp = api_client.patch(f"/api/searches/{search.id}", json={"country": "Canada"})
    assert resp.status_code == 200, f"Unexpected {resp.status_code}: {resp.text}"
    assert resp.json()["country"] == "canada"


def test_countries_endpoint_lists_the_library_names(api_client, test_db):
    _first_run_auth(test_db)
    resp = api_client.get("/api/searches/countries")
    assert resp.status_code == 200, f"Unexpected {resp.status_code}: {resp.text}"
    rows = resp.json()

    by_value = {r["value"]: r["label"] for r in rows}
    assert by_value["usa"] == "United States"
    assert by_value["canada"] == "Canada"
    assert by_value["uk"] == "United Kingdom"
    # ZipRecruiter's and LinkedIn's internal routing members are not places.
    assert "usa/ca" not in by_value and "worldwide" not in by_value


# ── the one-time backfill ────────────────────────────────────────────────────

@pytest.mark.parametrize("location,expected", [
    ("Toronto, Canada", "canada"),          # last comma-separated segment
    ("Warsaw, Poland", "poland"),
    ("Canada", "canada"),                   # the whole trimmed string
    ("  poland  ", "poland"),
    ("United States", "usa"),
    ("US", "usa"),                          # any alias jobspy accepts
    ("Remote", "usa"),                      # no match -> the default
    ("", "usa"),
    (None, "usa"),
    ("Springfield, Nowhereland", "usa"),
])
def test_country_from_location(location, expected):
    from backend.countries import country_from_location
    assert country_from_location(location) == expected


def test_backfill_fills_only_the_null_rows(test_db):
    from backend.seed import _backfill_search_country

    canadian = _with_null_country(test_db, _search(test_db, location="Toronto, Canada"))
    polish = _with_null_country(test_db, _search(test_db, location="Warsaw, Poland"))
    already = _search(test_db, location="Toronto, Canada", country="usa")
    assert canadian.country is None, "the fixture must start from a NULL column"

    _backfill_search_country(test_db)

    test_db.refresh(canadian)
    test_db.refresh(polish)
    test_db.refresh(already)
    assert canadian.country == "canada"
    assert polish.country == "poland"
    # The backfill runs once per row: a row that already has a country keeps it,
    # even when the location text disagrees.
    assert already.country == "usa"


def test_backfill_is_idempotent(test_db):
    from backend.seed import _backfill_search_country

    search = _with_null_country(test_db, _search(test_db, location="Remote"))
    _backfill_search_country(test_db)
    test_db.refresh(search)
    assert search.country == "usa"

    search.country = "canada"     # a later user edit
    test_db.commit()
    _backfill_search_country(test_db)
    test_db.refresh(search)
    assert search.country == "canada", "a second run must not overwrite the field"


def test_the_migration_adds_the_column_before_it_sets_the_default():
    """Order is the property, not presence.

    `ADD COLUMN ... DEFAULT x` writes x into every existing row, and those rows
    are exactly what the backfill must still read. `SET DEFAULT` writes no row,
    so it must come after and must not ride on the ADD COLUMN line.
    """
    import inspect
    import backend.seed as seed

    lines = [l.strip() for l in inspect.getsource(seed.run_migrations).splitlines()]
    add = [i for i, l in enumerate(lines) if "ADD COLUMN IF NOT EXISTS country" in l
           or l.startswith("_ADD_COUNTRY_COLUMN")]
    default = [i for i, l in enumerate(lines) if "ALTER COLUMN country SET DEFAULT" in l]
    assert add, "the searches.country migration disappeared"
    assert default, "later inserts still need the column default"
    assert "DEFAULT" not in seed._ADD_COUNTRY_COLUMN.upper(), \
        f"ADD COLUMN must not carry a default: {seed._ADD_COUNTRY_COLUMN}"
    assert min(default) > max(add), "SET DEFAULT must run after ADD COLUMN, not before it"


def test_run_migrations_calls_the_country_backfill(test_db, monkeypatch):
    """Without this pin, deleting the call leaves every existing row NULL and the whole suite still passes."""
    import backend.seed as seed

    called = []
    monkeypatch.setattr(seed, "_backfill_search_country", lambda db: called.append(db))
    # SQLite rejects `ADD COLUMN IF NOT EXISTS`, so on this fixture the statement
    # always lands in the failed list and the guard skips the backfill for a
    # reason this test is not about. Report a clean list instead.
    monkeypatch.setattr(seed, "run_migration_statements", lambda db, statements: [])

    seed.run_migrations(test_db)

    assert called, "run_migrations must call the country backfill"


def test_the_backfill_is_skipped_when_the_column_was_not_added(test_db, monkeypatch):
    """The backfill SELECTs searches.country. Without the column it raises out of run_seeds() and the container restarts forever."""
    import backend.seed as seed

    called = []
    monkeypatch.setattr(seed, "_backfill_search_country", lambda db: called.append(db))
    monkeypatch.setattr(seed, "run_migration_statements",
                        lambda db, statements: [seed._ADD_COUNTRY_COLUMN])

    seed.run_migrations(test_db)

    assert called == [], "a missing column must not reach the backfill SELECT"


def test_a_raising_backfill_does_not_abort_the_migration(test_db, monkeypatch):
    """run_seeds() runs inside the FastAPI lifespan — no migration step may take the container down."""
    import backend.seed as seed

    def boom(db):
        raise RuntimeError("column vanished")

    monkeypatch.setattr(seed, "_backfill_search_country", boom)
    # A clean statement list, so the backfill is really reached (see above).
    monkeypatch.setattr(seed, "run_migration_statements", lambda db, statements: [])

    seed.run_migrations(test_db)      # raises if the guard is missing


def test_aliases_survive_a_plain_string_enum_value():
    """If a later jobspy drops the tuple, value[0] would be the letter "u" and "usa" would silently become an invalid country."""
    from backend.countries import _aliases

    class _Member:
        value = "usa,us,united states"

    assert _aliases(_Member) == ["usa", "us", "united states"]
