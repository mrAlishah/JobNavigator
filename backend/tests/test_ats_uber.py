"""ats/uber.py: Uber's careers search through jobs.uber.com's own API."""
import pytest

from backend.scraper.ats import uber

OLD = ("https://www.uber.com/de/en/careers/list/?department=Product&location=USA-California-San%20Francisco"
       "&location=DEU--Berlin&team=Product%20Management&team=Program%20Management")


def _item(i, title="Sr Product Manager, Road Safety", remote=False, city="San Francisco"):
    return {"Id": str(i), "Title": title, "Remote": remote, "Teams": ["Product"],
            "Description": "<p>About the Role</p><p>" + "Own the road safety roadmap. " * 5 + "</p>",
            "Locations": [{"City": city, "Region": "California", "Country": "USA"},
                          {"City": "New York City", "Region": "New York", "Country": "USA"}],
            "Urls": [{"Culture": "en-us", "Url": f"/en/jobs/{i}/", "IsDefault": True}]}


class FakeApi:
    def __init__(self, pages, status=200):
        self.pages, self.status, self.asked = pages, status, []

    async def __call__(self, params):
        self.asked.append(params)
        if self.status != 200:
            return self.status, None
        n = int(params["page"][0])
        return 200, {"jobs": self.pages[n - 1], "totalPages": len(self.pages), "totalJobs": 0}


def test_is_uber():
    assert uber.is_uber("https://jobs.uber.com/en/jobs/?team=Product")
    assert uber.is_uber("https://jobs.uber.com/jobs")
    assert uber.is_uber(OLD)
    assert not uber.is_uber("https://jobs.uber.com/en/jobs/302019/"), "one posting, not a search"
    assert not uber.is_uber("https://www.uber.com/careers/list/160013")
    assert not uber.is_uber("https://jobs.uber.com/en/jobs/saved-jobs/")
    assert not uber.is_uber("https://evil-uber.com/careers/list/?department=Product")


def test_an_old_saved_url_becomes_the_new_search():
    assert uber.search_params(OLD) == {"team": ["Product"], "location": ["San Francisco", "Berlin"]}
    assert uber.search_params("https://jobs.uber.com/en/jobs/?team=Product&location=Seattle&keyword=pm") == {
        "team": ["Product"], "location": ["Seattle"], "keyword": ["pm"]}


@pytest.mark.asyncio
async def test_every_page_is_read_with_the_description():
    api = FakeApi([[_item(1), _item(2, remote=True)], [_item(3), _item(1)]])
    jobs = await uber.scrape(OLD, fetch=api, pause=0)
    assert [p["page"] for p in api.asked] == [["1"], ["2"]]
    assert api.asked[0]["team"] == ["Product"]
    assert [j["url"] for j in jobs] == [f"https://jobs.uber.com/en/jobs/{i}/" for i in (1, 2, 3)]
    j = jobs[0]
    assert j["title"] == "Sr Product Manager, Road Safety"
    assert j["location"] == "San Francisco, California, USA" and len(j["locations"]) == 2
    assert j["description"].startswith("About the Role") and j["arrangement"] is None
    assert jobs[1]["arrangement"] == "remote"


@pytest.mark.asyncio
async def test_a_refused_first_page_is_an_error_not_an_empty_board():
    with pytest.raises(RuntimeError, match="429"):
        await uber.scrape(OLD, fetch=FakeApi([], status=429), pause=0)


def test_the_posting_key_and_the_label():
    from backend.api.routes_companies import detect_scrape_type
    from backend.scraper._shared.postings import posting_key
    # Uber kept its ids when it moved: 160013 is the same posting on both sites
    assert posting_key("https://jobs.uber.com/en/jobs/160013/") == "site:uber.com:160013"
    assert posting_key("https://www.uber.com/careers/list/160013") == "site:uber.com:160013"
    assert detect_scrape_type(OLD) == "Uber API"


@pytest.mark.asyncio
async def test_a_description_the_board_sent_is_not_fetched_again(monkeypatch):
    from backend.scraper.ats import _descriptions as d
    asked = []

    async def fetch(url, job=None):
        asked.append(url)
        return "fetched"
    monkeypatch.setattr(d, "_fetch_job_description", fetch)
    out = await d._fetch_descriptions_parallel([{"url": "https://jobs.uber.com/en/jobs/1/", "description": "sent"},
                                                {"url": "https://jobs.lever.co/a/b"}])
    assert [desc for _, desc in out] == ["sent", "fetched"] and asked == ["https://jobs.lever.co/a/b"]
