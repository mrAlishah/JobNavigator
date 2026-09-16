"""Tests for ats/google.py — detection only (scrape requires real browser)."""
import pytest


def test_is_google_detects_about_careers():
    from backend.scraper.ats.google import is_google
    assert is_google("https://www.google.com/about/careers/applications/jobs/results")


def test_is_google_rejects_non_google():
    from backend.scraper.ats.google import is_google
    assert not is_google("https://boards.greenhouse.io/acme")


def test_is_google_rejects_path_injection():
    from backend.scraper.ats.google import is_google
    # is_google uses a plain substring check, so ?url=google.com/about/careers would false-positive.
    assert not is_google("https://evil.com/?dest=some-other-careers")


# ── the card's place line ────────────────────────────────────────────────────

def test_places_from_text_splits_the_card_line_and_drops_the_count():
    from backend.scraper.ats.google import _places_from_text
    assert _places_from_text(
        "New York, NY, USA ; Atlanta, GA, USA ; +5 more"
    ) == ["New York, NY, USA", "Atlanta, GA, USA"]


def test_places_from_text_strips_the_material_icon_word():
    """The <i> is removed in the DOM, but a card read as plain text still
    carries the icon's own word in front of the first place."""
    from backend.scraper.ats.google import _places_from_text
    assert _places_from_text(
        "place San Bruno, CA, USA ; Mountain View, CA, USA"
    ) == ["San Bruno, CA, USA", "Mountain View, CA, USA"]


def test_places_from_text_keeps_a_city_that_merely_starts_with_place():
    from backend.scraper.ats.google import _places_from_text
    assert _places_from_text("Placerville, CA, USA") == ["Placerville, CA, USA"]


@pytest.mark.parametrize("text", ["", "   ", None, "+3 more"])
def test_places_from_text_yields_nothing_for_an_empty_card(text):
    from backend.scraper.ats.google import _places_from_text
    assert _places_from_text(text) == []
