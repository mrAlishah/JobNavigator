"""Tests for ats/meta.py — detection only (scrape requires real browser)."""
import pytest


def test_is_meta_detects_metacareers():
    from backend.scraper.ats.meta import is_meta
    assert is_meta("https://metacareers.com/jobs/")


def test_is_meta_detects_subdomain():
    from backend.scraper.ats.meta import is_meta
    assert is_meta("https://www.metacareers.com/jobs/12345")


def test_is_meta_rejects_non_meta():
    from backend.scraper.ats.meta import is_meta
    assert not is_meta("https://boards.greenhouse.io/acme")


def test_is_meta_rejects_path_injection():
    from backend.scraper.ats.meta import is_meta
    assert not is_meta("https://evil.com/?url=metacareers.com")


# ── the card's place line ────────────────────────────────────────────────────

def test_places_from_text_splits_on_the_dot_and_drops_the_count():
    from backend.scraper.ats.meta import _places_from_text
    assert _places_from_text(
        "Bellevue, WA ⋅ Redmond, WA ⋅ Austin, TX ⋅ Menlo Park, CA ⋅ Seattle, WA ⋅ +4 more"
    ) == ["Bellevue, WA", "Redmond, WA", "Austin, TX", "Menlo Park, CA", "Seattle, WA"]


def test_places_from_text_keeps_a_card_that_names_one_place():
    from backend.scraper.ats.meta import _places_from_text
    assert _places_from_text("Menlo Park, CA") == ["Menlo Park, CA"]


@pytest.mark.parametrize("text", [
    "",
    "   ",
    None,
    "Product Management",   # a team tag, not a place
    "+4 more",              # the count on its own
])
def test_places_from_text_yields_nothing_when_the_line_is_not_places(text):
    from backend.scraper.ats.meta import _places_from_text
    assert _places_from_text(text) == []


def test_places_from_text_reads_the_block_without_spaces_round_the_dot():
    """The block's own text carries no spaces round the separator; only the
    card rendered as lines puts them there."""
    from backend.scraper.ats.meta import _places_from_text
    assert _places_from_text(
        "Menlo Park, CA⋅Seattle, WA⋅New York, NY⋅+2 more"
    ) == ["Menlo Park, CA", "Seattle, WA", "New York, NY"]
