"""JobSpy field cleaning: pandas nulls must never be stringified into 'None'/'nan'.

Root cause of the tailoring crash: JobSpy rows come from a DataFrame, so missing
cells are None/NaN. `str(cell)` turned those into the literal text 'None'/'nan',
which then masqueraded as real content. `_clean` detects the actual null instead.
"""


def test_clean_returns_none_for_nulls():
    from backend.scraper.sources.jobspy import _clean
    assert _clean(None) is None
    assert _clean(float("nan")) is None
    assert _clean("") is None
    assert _clean("   ") is None


def test_clean_preserves_real_values():
    from backend.scraper.sources.jobspy import _clean
    assert _clean("Senior PM") == "Senior PM"
    assert _clean("  padded text  ") == "padded text"
    assert _clean(42) == "42"  # non-string scalars still coerce


def test_clean_never_produces_literal_none_or_nan():
    """The exact regression: a null must not become the string 'None' or 'nan'."""
    from backend.scraper.sources.jobspy import _clean
    assert _clean(None) != "None"
    assert _clean(float("nan")) != "nan"
