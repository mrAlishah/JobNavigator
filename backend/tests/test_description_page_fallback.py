"""_fetch_job_description's page fallback: a LinkedIn page yields only its description block, and a bot wall yields nothing."""
import pytest

from backend.scraper.ats import _descriptions

_JD = "Responsibilities: run the release train. Requirements: Kubernetes, Terraform. " * 3

_LINKEDIN_PAGE = f"""<html><head><title>Staff Platform Engineer at Rivian | LinkedIn Jobs</title></head><body>
<div>Join or sign in to find your next job. Email or phone. Password. Forgot password? Sign in.</div>
<section class="description"><div class="description__text">
<div class="show-more-less-html__markup"><p>{_JD}</p></div>
</div></section>
<div>Similar jobs. People also viewed.</div></body></html>"""


class _Resp:
    status_code = 200

    def __init__(self, text):
        self.text = text

    def raise_for_status(self):
        return None


@pytest.fixture
def _serve(monkeypatch):
    """Answer the page fetch with the given HTML; the ATS APIs have nothing."""
    import backend.scraper._shared.url_safety as us

    async def _no_ats(url, job=None):
        return None

    monkeypatch.setattr(_descriptions, "_fetch_description_ats", _no_ats)
    monkeypatch.setattr(us, "assert_public_http_url", lambda u: None)

    def _install(html):
        async def _get(url, **kw):
            return _Resp(html)
        monkeypatch.setattr(us, "safe_get", _get)
    return _install


@pytest.mark.asyncio
async def test_linkedin_page_yields_only_the_description_block(_serve):
    _serve(_LINKEDIN_PAGE)
    text = await _descriptions._fetch_job_description("https://www.linkedin.com/jobs/view/4418891742")
    assert text.startswith("Responsibilities: run the release train.")
    for chrome in ("Sign in", "Password", "Similar jobs", "LinkedIn Jobs"):
        assert chrome not in text


@pytest.mark.asyncio
async def test_linkedin_page_without_the_block_yields_nothing(_serve):
    """A changed LinkedIn layout must not fall back to the sign-in chrome."""
    _serve(_LINKEDIN_PAGE.replace("show-more-less-html__markup", "renamed"))
    assert await _descriptions._fetch_job_description("https://www.linkedin.com/jobs/view/1") is None


@pytest.mark.asyncio
async def test_other_sites_still_read_the_whole_page(_serve):
    _serve(f"<html><body><h1>Platform Engineer</h1><p>{_JD}</p></body></html>")
    text = await _descriptions._fetch_job_description("https://careers.example.com/jobs/1")
    assert "Platform Engineer" in text and "Requirements" in text


@pytest.mark.asyncio
async def test_a_bot_wall_yields_nothing(_serve):
    _serve("<html><head><title>Just a moment...</title></head><body>"
           + "Verifying you are human. This may take a few seconds. " * 5 + "</body></html>")
    assert await _descriptions._fetch_job_description("https://ca.indeed.com/viewjob?jk=abc") is None
