"""Greenhouse embed links (/embed/job_app?for=<board>&token=<id>, Jobright's link) are read through
the job API: the page itself starts with Greenhouse's header ("Job Application for … at …")."""
import json

import pytest

from backend.scraper.ats import _descriptions as d

CONTENT = "&lt;p&gt;" + "We build payments. " * 20 + "&lt;/p&gt;"


class _Resp:
    def __init__(self, url, status=200, text=""):
        self.url, self.status_code, self.text = url, status, text


def _fake_api(monkeypatch, asked):
    class Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            asked.append(url)
            ok = url == "https://boards-api.greenhouse.io/v1/boards/twitch/jobs/8843932002"
            return _Resp(url, 200 if ok else 404, json.dumps({"content": CONTENT}) if ok else "")
    monkeypatch.setattr(d.httpx, "AsyncClient", Client)


@pytest.mark.asyncio
async def test_an_embed_link_is_read_through_the_api(monkeypatch):
    asked = []
    _fake_api(monkeypatch, asked)
    text = await d._fetch_description_ats("https://job-boards.greenhouse.io/embed/job_app?for=twitch&token=8843932002")
    assert text.startswith("We build payments.") and "Job Application" not in text
    assert asked == ["https://boards-api.greenhouse.io/v1/boards/twitch/jobs/8843932002"]


@pytest.mark.asyncio
async def test_an_embed_link_without_its_board_follows_the_redirect(monkeypatch):
    asked = []
    _fake_api(monkeypatch, asked)

    async def safe_get(url, **kw):
        return _Resp("https://job-boards.greenhouse.io/embed/job_app?for=twitch&token=8843932002&utm_source=jobright")
    monkeypatch.setattr("backend.scraper._shared.url_safety.safe_get", safe_get)
    text = await d._fetch_description_ats("https://boards.greenhouse.io/embed/job_app?token=8843932002&utm_source=jobright")
    assert text and text.startswith("We build payments.")
