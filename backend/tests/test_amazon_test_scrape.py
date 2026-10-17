"""The company "Test scrape" must route an amazon.jobs URL to the Amazon handler,
the same one the real scrape uses, not fall through to the generic reader."""
import pytest

from backend.models.db import Company

URL = "https://www.amazon.jobs/en/search?base_query=program+manager&country[]=USA"


@pytest.fixture
def _fake_amazon(monkeypatch):
    import backend.scraper._shared.browser as browser_mod
    import backend.scraper.ats.amazon as amazon

    class _FakeBrowser:
        async def close(self):
            return None

    class _FakePw:
        async def stop(self):
            return None

    async def fake_get_browser():
        return _FakePw(), _FakeBrowser()

    async def no_page(browser):  # the generic reader opens a page; Amazon must not
        raise AssertionError("amazon.jobs URL fell through to the generic reader")

    calls = []

    async def fake_scrape(url, debug=False, **kw):
        calls.append((url, debug))
        return [{"title": "Senior Program Manager",
                 "url": "https://www.amazon.jobs/en/jobs/1234567/senior-program-manager"}], []

    monkeypatch.setattr(browser_mod, "_get_browser", fake_get_browser)
    monkeypatch.setattr(browser_mod, "_new_page", no_page)
    monkeypatch.setattr(amazon, "scrape", fake_scrape)
    return calls


@pytest.mark.asyncio
async def test_amazon_url_is_read_by_the_amazon_handler(test_db, _fake_amazon):
    from backend.api.routes_companies import test_scrape_company

    c = Company(name="ZZ Amazon", active=True, scrape_urls=[URL],
                title_include_expr="manager", title_exclude_keywords=[])
    test_db.add(c)
    test_db.commit()

    out = await test_scrape_company(str(c.id), db=test_db)

    assert _fake_amazon == [(URL, True)]
    assert [j["title"] for j in out["jobs"]] == ["Senior Program Manager"]
    assert out["jobs"][0]["kept"] is True
    assert any("Amazon API" in line for line in out["urls_scraped"])


def test_amazon_chip_label_matches_the_row_chip():
    from backend.api.routes_companies import detect_scrape_type
    assert detect_scrape_type(URL) == "Amazon API"
