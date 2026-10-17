"""Recognises an anti-bot interstitial that a site serves in place of the requested page.

Such a page is never content: stored as a job's page cache it becomes the text an
LLM tailors a résumé against, and read as metadata it becomes the job title.
"""
import re

# Checked against pages seen in the wild: Cloudflare's managed challenge
# ("Just a moment...") and Indeed's login redirect ("Authenticating...").
_WALL_TITLE_RE = re.compile(
    r"<title[^>]*>\s*(just a moment\.\.\.|attention required! \| cloudflare|authenticating\.\.\.)",
    re.IGNORECASE,
)
_WALL_MARKERS = (
    "_cf_chl_opt",          # Cloudflare challenge bootstrap
    "from=bot-detection",   # Indeed sends a suspected bot to its login page
)


def is_bot_wall(html: str | None) -> bool:
    """True if the raw HTML is an anti-bot interstitial rather than the page itself."""
    if not html:
        return False
    if _WALL_TITLE_RE.search(html):
        return True
    lowered = html.lower()
    return any(marker in lowered for marker in _WALL_MARKERS)
