"""freehire source — param forwarding + parsing (no network)."""
from types import SimpleNamespace
from backend.scraper.sources import freehire


def test_base_params_forwards_url_filters_and_drops_pagination():
    s = SimpleNamespace(
        direct_url="https://freehire.me/?q=go&category=backend&limit=9&offset=40&page=2",
        search_term="")
    assert freehire._base_params(s) == {"q": "go", "category": "backend"}


def test_search_term_overrides_url_q():
    s = SimpleNamespace(direct_url="https://freehire.me/?q=old", search_term="new term")
    assert freehire._base_params(s)["q"] == "new term"


def test_base_params_ignores_non_freehire_url():
    s = SimpleNamespace(direct_url="https://example.com/?q=go", search_term="")
    assert freehire._base_params(s) == {}


def test_strip_html_flattens_and_unescapes():
    out = freehire._strip_html("<p>Hello <b>world</b></p><ul><li>a</li></ul>")
    assert "<" not in out
    assert "Hello world" in out.replace("\n", " ")
    assert "<" not in freehire._strip_html("<p>x &amp; y</p>")
    assert freehire._strip_html("") == ""


def test_parse_job_maps_fields_and_strips_description():
    raw = {
        "title": "Backend Engineer", "company": "Acme", "location": "Remote",
        "url": "https://acme.com/apply/1", "description": "<p>Build <b>things</b></p>",
        "posted_at": "2026-08-21T00:00:00Z", "public_slug": "backend-acme-x",
        "enrichment": {"seniority": "senior", "employment_type": "full_time",
                       "salary_min": 150000, "salary_max": 200000,
                       "salary_currency": "USD", "salary_period": "year"},
    }
    j = freehire._parse_job(raw)
    assert j["title"] == "Backend Engineer"
    assert j["company"] == "Acme"
    assert j["url"] == "https://acme.com/apply/1"
    assert j["location"] == "Remote"
    assert "<" not in j["description"] and "Build things" in j["description"]
    assert j["seniority"] == "senior"
    assert j["employment_type"] == "full_time"
    assert j["salary_min"] == 150000 and j["salary_max"] == 200000
    assert j["salary_currency"] == "USD" and j["salary_period"] == "year"


def test_parse_job_tolerates_missing_fields():
    j = freehire._parse_job({})
    assert j["title"] == "" and j["company"] == "" and j["url"] == ""
    assert j["description"] == "" and j["seniority"] is None
    assert j["salary_min"] is None


def test_annual_salary_keeps_yearly_skips_non_annual():
    assert freehire._annual_salary({"salary_min": 100000, "salary_max": 120000, "salary_period": "year"}) == (100000, 120000)
    assert freehire._annual_salary({"salary_min": 100000, "salary_period": None}) == (100000, None)
    assert freehire._annual_salary({"salary_min": 8000, "salary_period": "month"}) == (None, None)
    assert freehire._annual_salary({"salary_min": 50, "salary_period": "hour"}) == (None, None)
    assert freehire._annual_salary({"salary_min": None}) == (None, None)
