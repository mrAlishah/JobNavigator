"""Tests for sources/linkedin_personal.py — LinkedIn /jobs/collections/ scraper."""
import pytest


def test_sources_linkedin_personal_exposes_entry_points():
    from backend.scraper.sources import linkedin_personal
    assert hasattr(linkedin_personal, "run")
    assert hasattr(linkedin_personal, "preview")


def test_run_is_async():
    import asyncio
    from backend.scraper.sources import linkedin_personal
    assert asyncio.iscoroutinefunction(linkedin_personal.run)


def test_preview_is_async():
    import asyncio
    from backend.scraper.sources import linkedin_personal
    assert asyncio.iscoroutinefunction(linkedin_personal.preview)


class _FakeLocator:
    def __init__(self, selector, calls):
        self._selector = selector
        self._calls = calls
        self.first = self

    async def fill(self, value):
        self._calls.append(("fill", self._selector, value))

    async def press(self, key):
        self._calls.append(("press", self._selector, key))


class _FakePage:
    """Records locator use and fails the legacy `page.fill('#username')` path."""

    def __init__(self):
        self.calls = []

    def locator(self, selector):
        return _FakeLocator(selector, self.calls)

    async def fill(self, selector, value):
        raise AssertionError(f"page.fill({selector!r}) — the login page has no such id")

    async def click(self, selector):
        raise AssertionError(f"page.click({selector!r}) — the login page has no submit button")


@pytest.mark.asyncio
async def test_fill_login_form_uses_autocomplete_selectors(monkeypatch):
    import asyncio as _asyncio
    from backend.scraper.sources import linkedin_personal as lp

    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr(lp.asyncio, "sleep", _no_sleep)
    page = _FakePage()
    await lp._fill_login_form(page, "user@example.com", "secret")

    assert page.calls == [
        ("fill", 'input[autocomplete="username"]:visible', "user@example.com"),
        ("fill", 'input[autocomplete="current-password"]:visible', "secret"),
        ("press", 'input[autocomplete="current-password"]:visible', "Enter"),
    ]
    assert _asyncio.iscoroutinefunction(lp._fill_login_form)


class _SessionPage:
    """A page whose voyager /me answer and url the test controls."""

    def __init__(self, url="https://www.linkedin.com/feed/", status=200, raises=False):
        self.url = url
        self._status = status
        self._raises = raises

    async def evaluate(self, _script):
        if self._raises:
            raise RuntimeError("page closed")
        return self._status

    def locator(self, selector):
        raise AssertionError(f"locator({selector!r}) — the feed DOM is not a login signal")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "page,expected",
    [
        (_SessionPage(status=200), True),
        (_SessionPage(status=401), False),
        (_SessionPage(status=0), False),
        (_SessionPage(raises=True), False),
        (_SessionPage(url="https://www.linkedin.com/login", status=200), False),
        (_SessionPage(url="https://www.linkedin.com/checkpoint/ch/login", status=200), False),
    ],
)
async def test_is_logged_in_trusts_voyager_not_the_dom(page, expected):
    from backend.scraper.sources import linkedin_personal as lp
    assert await lp._is_logged_in(page) is expected
