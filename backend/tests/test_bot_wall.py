"""is_bot_wall() must flag an anti-bot interstitial and leave a real page alone."""
import pytest

from backend.scraper._shared.bot_wall import is_bot_wall

# Trimmed from the 401 body ca.indeed.com served to the backend container.
_INDEED_401 = """<!DOCTYPE html><html><head><title>Authenticating...</title>
<script>(function() { var targetBase = "https://www.indeed.com/account/login?branding=login-required&from=bot-detection-anonymous&continue=";
})();</script></head><body>Redirecting to login...</body></html>"""

_CLOUDFLARE_CHALLENGE = """<!DOCTYPE html><html lang="en-US"><head><title>Just a moment...</title></head>
<body><script>window._cf_chl_opt = {cvId: '3'};</script></body></html>"""

# Cloudflare also injects its JS-detection script into ordinary pages it proxies.
_REAL_PAGE_BEHIND_CLOUDFLARE = """<html><head><title>Senior Backend Engineer | Acme</title></head>
<body><p>Just a moment... is how our onboarding buddy greets you.</p>
<script>window.__CF$cv$params={r:'a3c92e0d5f1f57bb'};a.src='/cdn-cgi/challenge-platform/scripts/jsd/main.js';</script>
</body></html>"""


@pytest.mark.parametrize("html", [_INDEED_401, _CLOUDFLARE_CHALLENGE])
def test_flags_an_interstitial(html):
    assert is_bot_wall(html) is True


@pytest.mark.parametrize("html", [_REAL_PAGE_BEHIND_CLOUDFLARE, "<html><body>ok</body></html>", "", None])
def test_leaves_a_real_page_alone(html):
    assert is_bot_wall(html) is False
