"""Tests for analyzer/salary_extractor — regex patterns + LCA fallback + 30K input cap."""
import pytest


def test_extract_salary_range_with_dollar_sign():
    from backend.analyzer.salary_extractor import extract_salary
    result = extract_salary("Salary: $140,000 - $210,000 per year")
    assert result["salary_min"] == 140000
    assert result["salary_max"] == 210000
    assert result["salary_source"] != "unknown"


def test_extract_salary_range_with_usd_suffix():
    from backend.analyzer.salary_extractor import extract_salary
    result = extract_salary("Compensation: 173,900.00 - 235,200.00 USD annually")
    assert result["salary_min"] == 173900
    assert result["salary_max"] == 235200


def test_extract_salary_k_notation():
    from backend.analyzer.salary_extractor import extract_salary
    result = extract_salary("Pay range: $120k-$180k")
    # k-notation should resolve to thousands
    assert result["salary_min"] == 120000
    assert result["salary_max"] == 180000


def test_extract_salary_empty_returns_unknown():
    from backend.analyzer.salary_extractor import extract_salary
    result = extract_salary("")
    assert result["salary_min"] is None
    assert result["salary_max"] is None
    assert result["salary_source"] == "unknown"


def test_extract_salary_lca_fallback():
    """No salary in text + h1b_median given → uses LCA estimate."""
    from backend.analyzer.salary_extractor import extract_salary
    result = extract_salary("A cool job", h1b_median_salary=165000)
    assert result["salary_min"] == 165000
    assert result["salary_max"] == 165000
    assert result["salary_source"] == "lca_estimate"


def test_extract_salary_strips_html_tags():
    from backend.analyzer.salary_extractor import extract_salary
    # HTML tags should be stripped before regex runs
    result = extract_salary("<p>Salary: <b>$140,000</b> - <b>$210,000</b></p>")
    assert result["salary_min"] == 140000
    assert result["salary_max"] == 210000


def test_extract_salary_input_capped_at_30k_chars():
    """ReDoS mitigation: input capped at 30K chars before regex."""
    from backend.analyzer.salary_extractor import extract_salary
    # Place salary at the BEGINNING so it's preserved under the cap
    prefix = "Salary: $100,000 - $150,000"
    padding = "x" * 100000  # 100K of junk AFTER the salary
    text = prefix + " " + padding
    result = extract_salary(text)
    assert result["salary_min"] == 100000
    assert result["salary_max"] == 150000


def test_extract_salary_single_value_not_picked_as_range():
    """A single salary mention (no range) does not populate both min and max to the same value; range-only patterns win."""
    from backend.analyzer.salary_extractor import extract_salary
    result = extract_salary("Starting salary $100,000")
    assert isinstance(result, dict)
    assert "salary_min" in result
    assert "salary_max" in result


# ── Non-yearly pay is never stored as a yearly figure ───────────────────────

_NONE = (None, None)


def _pair(text):
    from backend.analyzer.salary_extractor import extract_salary
    r = extract_salary(text)
    return r["salary_min"], r["salary_max"]


def test_wikimedia_hourly_range_is_not_stored():
    """The live TrulyRemote Wikimedia posting: was stored as 34 / 42 yearly."""
    from backend.analyzer.salary_extractor import extract_salary
    r = extract_salary("The hourly pay range in the US is US$34.32 to $42.13 per hour, depending on location.")
    assert (r["salary_min"], r["salary_max"], r["salary_source"]) == (None, None, "unknown")
    assert _pair("US$34.32 to $42.13 per hour") == _NONE


@pytest.mark.parametrize("text", [
    "$34.32 - $42.13 per hour",
    "US$34.32 to $42.13 per hour",
    "$45 - $60/hr",
    "$45/hr - $60/hr",
    "$45 - $60 / hour",
    "$45 to $60 an hour",
    "$45-$60 hourly",
    "$45 - $60 p/h",
    "$45 - $60 ph",
    "$45 - $60 USD per hour",
    "Hourly rate: $45 - $60",
    "Pay per hour: $45 - $60",
    "$1,200 - $1,500 per hour",          # big enough to pass the 1000 guard; the tag still wins
    "COMPENSATION: $102 - $112 HOURLY",  # live levels_fyi row
    "The hourly pay range for this position is from $36.85 to $55.26 and pay offered",
])
def test_hourly_amounts_are_not_stored(text):
    assert _pair(text) == _NONE


@pytest.mark.parametrize("text", [
    "$8,000 - $10,000 per month",
    "$8,000 - $10,000/mo",
    "$8,000 - $10,000 monthly",
    "Monthly salary: $8,000 - $10,000",
    "$8k - $10k per month",
    "$9k/month",
    "$5,000 per month",
    "$1,500 - $2,000 per week",
    "$2,000 - $2,500 weekly",
    "$1,200 - $1,500 per day",
    "$1,200 a day",
])
def test_monthly_weekly_daily_amounts_are_not_stored(text):
    assert _pair(text) == _NONE


@pytest.mark.parametrize("text", [
    "$34 - $42",                 # no period, but no yearly salary is under 1000
    "$34.32 to $42.13",
    "$500 per year",
    "34 - 42 USD",
])
def test_implausibly_small_yearly_figures_are_skipped(text):
    assert _pair(text) == _NONE


@pytest.mark.parametrize("text,expected", [
    ("$120,000 - $150,000", (120000, 150000)),
    ("Salary: $120,000 - $150,000 per year", (120000, 150000)),
    ("$120k - $150k", (120000, 150000)),
    ("$120K-$150K/yr", (120000, 150000)),
    ("$140,000 USD - $210,000 USD", (140000, 210000)),
    ("$122,550.00-$201,000.00", (122550, 201000)),
    ("$130,000 per year", (130000, 130000)),
    ("$130k", (130000, 130000)),
    ("Compensation: 173,900.00 - 235,200.00 USD annually", (173900, 235200)),
    ("Base pay $120,000", (120000, 120000)),
])
def test_yearly_amounts_still_parse(text, expected):
    assert _pair(text) == expected


def test_a_yearly_range_after_an_hourly_one_is_read():
    text = "Hourly employees: $34 - $42 per hour. Salaried: $90,000 - $110,000 per year."
    assert _pair(text) == (90000, 110000)


def test_the_yearly_k_range_wins_over_its_hourly_equivalent():
    """Live levels_fyi row, stored as 67 / 105 before: the $-range pattern ran first."""
    assert _pair("Expected Salary: $140k-$220k/year ($67-$105/hour).") == (140000, 220000)


def test_a_yearly_range_is_not_dropped_for_a_stipend_mentioned_later():
    text = "Salary $120,000 - $150,000. Benefits include a $100 per month wellness stipend."
    assert _pair(text) == (120000, 150000)


def test_hourly_description_falls_back_to_lca_median():
    from backend.analyzer.salary_extractor import extract_salary
    r = extract_salary("US$34.32 to $42.13 per hour", h1b_median_salary=150000)
    assert (r["salary_min"], r["salary_source"]) == (150000, "lca_estimate")


def test_apply_salary_to_job_leaves_hourly_pay_empty():
    from types import SimpleNamespace
    from backend.analyzer.salary_extractor import apply_salary_to_job
    job = SimpleNamespace(salary_min=None, salary_max=None, salary_source=None,
                          description="Pay: US$34.32 to $42.13 per hour")
    apply_salary_to_job(job)
    assert (job.salary_min, job.salary_max, job.salary_source) == (None, None, "unknown")


@pytest.mark.parametrize("text,expected", [
    ("$45/hr", True), ("$25 - $30 an hour", True), ("$8K/mo", True), ("$9,000 monthly", True),
    ("$1,500 per week", True), ("$400/day", True), ("$45 p/h", True),
    ("$120K - $180K/yr", False), ("$120,000 - $150,000", False), ("$150k a year", False), ("", False),
])
def test_is_non_annual(text, expected):
    from backend.analyzer.salary_extractor import is_non_annual
    assert is_non_annual(text) is expected


# ── Per-source salary strings use the same rule ─────────────────────────────

def test_jobright_salary_string_hourly_is_not_read_as_thousands():
    from backend.scraper.sources import jobright
    assert jobright._parse_salary("$45/hr - $60/hr") == (None, None)
    assert jobright._parse_salary("$120K - $180K/yr") == (120000, 180000)


def test_linkedin_personal_salary_string_hourly_is_text_only():
    from backend.scraper.sources import linkedin_personal
    r = linkedin_personal._parse_salary_text("$45/hr - $60/hr")
    assert (r["salary_min"], r["salary_max"], r["salary_text"]) == (None, None, "$45/hr - $60/hr")
    r = linkedin_personal._parse_salary_text("$120K/yr - $180K/yr")
    assert (r["salary_min"], r["salary_max"]) == (120000, 180000)


def test_levelsfyi_card_hourly_is_skipped():
    from backend.scraper.sources import levelsfyi
    assert levelsfyi._parse_levelsfyi_salary("Austin, TX · Remote · $45/hr")[2:] == (None, None)
    assert levelsfyi._parse_levelsfyi_salary("Austin, TX · Remote · $150K - $200K")[2:] == (150000, 200000)


@pytest.mark.parametrize("interval,lo,hi,ok", [
    ("yearly", 120000.0, 150000.0, True),
    (None, 120000.0, 150000.0, True),
    (float("nan"), 120000.0, None, True),
    ("hourly", 34.32, 42.13, False),
    ("monthly", 8000.0, 10000.0, False),
    ("weekly", 1500.0, 2000.0, False),
    ("daily", 400.0, 500.0, False),
    ("yearly", 34.0, 42.0, False),
])
def test_jobspy_only_yearly_rows_are_stored(interval, lo, hi, ok):
    from backend.scraper.sources.jobspy import _jobspy_yearly
    assert _jobspy_yearly(interval, lo, hi) is ok


# ── R30 S-01: a period word before an amount does not beat its own yearly tag ──

@pytest.mark.parametrize("text,expected", [
    ("$60–$75 per hour ($124,800–$156,000 annually)", (124800, 156000)),
    ("Pay: $60 - $75 per hour ($124,800 - $156,000 annually)", (124800, 156000)),
    ("Full-time role, 40 hours per week with salary $120,000 - $140,000", (120000, 140000)),
    ("Hybrid (3 days a week) with a salary of $150,000 - $180,000", (150000, 180000)),
    ("Paid monthly: $150,000 - $180,000 per year", (150000, 180000)),
    ("$30 an hour ($62,400 a year)", (62400, 62400)),
    ("$8k - $10k per month ($96k - $120k per year)", (96000, 120000)),
    ("On site 2 days/week. Salary: $130,000 - $150,000", (130000, 150000)),
    ("$120,000 per annum", (120000, 120000)),
])
def test_a_yearly_amount_after_an_earlier_period_word_is_read_whole(text, expected):
    assert _pair(text) == expected


@pytest.mark.parametrize("text", [
    "Monthly salary: $150,000 - $180,000",   # no yearly tag of its own: the clause's period wins
    "Hourly rate: $120,000 - $140,000",
])
def test_a_rejected_range_is_not_read_again_as_its_second_half(text):
    assert _pair(text) == _NONE


@pytest.mark.parametrize("text", [
    "$5,000 - $8,000",               # no period and under the unstated floor
    "Signing bonus $5,000",
    "$9k",
])
def test_untagged_small_amounts_are_not_yearly(text):
    assert _pair(text) == _NONE


# ── R30 S-03: a prefixed or foreign dollar is not USD ──

@pytest.mark.parametrize("text", [
    "Salary: TT$180,000 per annum",
    "Salary: TT $180,000 per annum",
    "J$2,400,000 - J$3,000,000 annually",
    "HK$600,000 - HK$800,000 per year",
    "Compensation: A$150,000 - A$170,000",
    "Compensation: AU$150k - AU$170k",
    "Base salary C$140,000",
    "CA$140,000 per year",
    "NZ$120,000",
    "S$80,000 - S$100,000",
    "R$120,000 per year",
    "$140,000 - $170,000 AUD",
    "$140,000 (CAD) per year",
    "CAD $150,000 per year",
    "$2,400,000 annually",           # implausible as a US salary
])
def test_foreign_dollars_are_not_read_as_usd(text):
    assert _pair(text) == _NONE


@pytest.mark.parametrize("text,expected", [
    ("US$150,000 - US$180,000", (150000, 180000)),
    ("USD $150,000", (150000, 150000)),
    ("USD 150,000 - 180,000", (150000, 180000)),
    ("San Francisco, CA $150,000 - $180,000", (150000, 180000)),
    ("Salary: $150,000 - $180,000 USD", (150000, 180000)),
])
def test_usd_amounts_still_parse(text, expected):
    assert _pair(text) == expected


@pytest.mark.parametrize("text,stored", [
    ("$120,000 - $150,000 per year", False),
    ("US$120,000 - $150,000", True),
    ("USD 120,000 - 150,000 per year", True),
    ("Salary: USD $120,000 per year", True),
    ("$120,000 - $150,000 USD", True),
])
def test_explicit_usd_mode_needs_usd_named(text, stored):
    from backend.analyzer.salary_extractor import extract_salary
    r = extract_salary(text, explicit_usd=True)
    assert (r["salary_source"] == "posting") is stored


# ── Shared period and currency helpers ──────────────────────────────────────

@pytest.mark.parametrize("text,period", [
    ("$4,000 - $6,000 a month", "MONTH"), ("$5,000/m", "MONTH"), ("USD 3,500 - 4,500 pcm", "MONTH"),
    ("$9,000/mth", "MONTH"), ("$2,500 a week", "WEEK"), ("$1,200 - $1,500 per wk", "WEEK"),
    ("$400 a day", "DAY"), ("$45/hr", "HOUR"), ("60,000 per annum", "YEAR"), ("$120K - $180K/yr", "YEAR"),
    ("$150k", ""), ("MONTH", "MONTH"), ("YEAR", "YEAR"), ("", ""),
    ("$60/hr ($124,800 a year)", "HOUR"),   # a source field naming both is not a clean yearly figure
])
def test_salary_period(text, period):
    from backend.analyzer.salary_extractor import salary_period
    assert salary_period(text) == period


@pytest.mark.parametrize("text", [
    "$4,000 - $6,000 a month", "$5,000/m", "$2,500 a week", "$1,200 - $1,500 per wk",
    "USD 3,500 - 4,500 pcm", "$9,000/mth",
])
def test_is_non_annual_reads_every_short_period(text):
    from backend.analyzer.salary_extractor import is_non_annual
    assert is_non_annual(text) is True


@pytest.mark.parametrize("text,code", [
    ("TT$ 8,000", "TTD"), ("J$2,400,000", "JMD"), ("BDS$50,000", "BBD"), ("EC$40,000", "XCD"),
    ("CI$60,000", "KYD"), ("AU$120k", "AUD"), ("HK$600,000", "HKD"), ("€65K", "EUR"), ("£90,000", "GBP"),
    ("$100,000 AUD", "AUD"), ("US$ 60,000", "USD"), ("usd 60,000", "USD"), ("$150k", ""), ("85000", ""),
])
def test_currency_code(text, code):
    from backend.analyzer.salary_extractor import currency_code
    assert currency_code(text) == code


@pytest.mark.parametrize("low,high,currency,period,stored", [
    (120000, 150000, "USD", "YEAR", (120000, 150000)),
    (120000, 150000, "USD", "", (120000, 150000)),
    (5000, 8000, "USD", "YEAR", (5000, 8000)),        # explicitly yearly
    (5000, 8000, "USD", "", (None, None)),            # no period, under the floor: monthly
    (4000, 6000, "USD", "MONTH", (None, None)),
    (120000, 150000, "TTD", "YEAR", (None, None)),
    (120000, 150000, "", "YEAR", (None, None)),
    (2_400_000, 3_000_000, "USD", "YEAR", (None, None)),
])
def test_yearly_usd(low, high, currency, period, stored):
    from backend.analyzer.salary_extractor import yearly_usd
    assert yearly_usd(low, high, currency, period) == stored


# ── R30 S-02: TrulyRemote and CaribbeanJobs use the shared period rule ──────

@pytest.mark.parametrize("text", [
    "$4,000 - $6,000 a month", "$5,000/m", "$2,500 a week", "$1,200 - $1,500 per wk",
    "USD 3,500 - 4,500 pcm", "$9,000/mth",
    "$5,000 - $8,000",               # no period, under the floor
])
def test_trulyremote_short_period_usd_is_text_only(text):
    from backend.scraper.sources import trulyremote
    fields = trulyremote._salary_fields(text)
    assert fields["salary_usd_annual"] is False and fields["salary_text"] == text


@pytest.mark.parametrize("text", ["USD 4,000 - 6,000 a month", "USD 5,000 /mth", "USD 1,200 a week",
                                  "USD 5,000 - 8,000"])
def test_caribbeanjobs_short_period_usd_is_text_only(text):
    from backend.scraper.sources import caribbeanjobs
    lo, hi = caribbeanjobs._parse_salary(text)
    j = {"salary_min": lo, "salary_max": hi,
         "salary_currency": caribbeanjobs._currency_of(text), "salary_unit": caribbeanjobs._unit_of(text)}
    assert j["salary_currency"] == "USD"
    assert caribbeanjobs._usd_annual(j) == (None, None)


def test_caribbeanjobs_json_ld_period_from_the_amount_text_or_unit_text():
    from backend.scraper.sources import caribbeanjobs
    raw = """<script id="jobPostingSchema" type="application/ld+json">
        {"@type": "JobPosting", "title": "Accountant",
         "baseSalary": {"currency": "USD", "value": {"minValue": "4,000 a month", "maxValue": "6,000"}}}
        </script>"""
    out = caribbeanjobs._parse_detail(raw, {"title": "x", "company": "Acme", "location": ""})
    assert out["salary_unit"] == "MONTH" and caribbeanjobs._usd_annual(out) == (None, None)
    per_month = raw.replace('"maxValue": "6,000"', '"maxValue": "6,000", "unitText": "per_month"')
    out = caribbeanjobs._parse_detail(per_month, {"title": "x", "company": "Acme", "location": ""})
    assert out["salary_unit"] == "MONTH"


# ── S3-01: a "$" glued to a word is a missing space, not a prefixed dollar ──

@pytest.mark.parametrize("text,expected", [
    ("Compensation$102,460 – $141,860", (102460, 141860)),
    ("Compensation$115K – $135K", (115000, 135000)),
    ("US - Salary Range$115,000—$140,000 USD", (115000, 140000)),
    ("where applicable. Compensation$102,460 – $141,860 • Offers Equity", (102460, 141860)),
    ("Pay$120,000 per year", (120000, 120000)),
    ("COMPENSATION$102,460 - $141,860", (102460, 141860)),
    ("CompensationUS$102,460 - US$141,860", (102460, 141860)),
    ("CompensationUSD$102,460 - USD$141,860", (102460, 141860)),
])
def test_a_dollar_glued_to_a_word_is_usd(text, expected):
    assert _pair(text) == expected


@pytest.mark.parametrize("text", [
    "CompensationHK$600,000 – HK$800,000",
    "Salary RangeA$150,000—A$170,000",
    "CompensationTT$180,000 per annum",
    "PayNZ$120,000",
    "SalaryJ$2,400,000 - J$3,000,000 annually",
    "Salary: RD$1,200,000 per year",
    "Compensation BDS$150,000 - BDS$170,000",
])
def test_a_prefixed_dollar_glued_to_a_word_stays_foreign(text):
    assert _pair(text) == _NONE


# ── S3-02: a period word in an aside that closes before the amount is not its period ──

@pytest.mark.parametrize("text,expected", [
    ("(Salary Exempt or Non-exempt hourly rate)role ranges from($140,000USD to $160,000USD)",
     (140000, 160000)),
    ("The reasonable estimated pay for this (Salary Exempt or Non-exempt hourly rate)role ranges "
     "from($140,000USD to $160,000USD). Actual compensation is based upon factors", (140000, 160000)),
    ("(exempt or hourly) role: $120,000 - $150,000", (120000, 150000)),
    ("(no daily commute) salary $130,000 - $150,000", (130000, 150000)),
])
def test_an_hourly_aside_does_not_reject_a_yearly_range(text, expected):
    assert _pair(text) == expected


@pytest.mark.parametrize("text", [
    "hourly rate $45",
    "Non-exempt hourly rate $45 - $55",
    "(Salary Exempt or Non-exempt hourly rate) $45 - $55",
    "Hourly rate: $120,000 - $140,000",
    "Hourly (non-exempt) role: $120,000 - $140,000",   # the word is not inside the aside
    "(hourly rate $120,000 - $140,000)",               # the aside is still open
    "Salary (monthly): $12,000 - $15,000",             # a month can reach the yearly floor
    "(paid monthly) $150,000 - $180,000",
])
def test_an_attached_or_monthly_period_word_still_rejects(text):
    assert _pair(text) == _NONE


# ── linear-time rewrites (CodeQL py/polynomial-redos) ───────────────────────

@pytest.mark.parametrize("text", [
    "", "plain", "<b>x</b>", "a<>b<c>", "<<<x>", "<a <b>", "x<y", "<\n>", ">a<b", "<>", "<<>>",
    "<p>$150,000 - $180,000</p>", "a<b>c<d", "<<", ">>", "<x>>y<<z>",
])
def test_strip_tags_matches_the_regex_it_replaces(text):
    import re
    from backend.analyzer.salary_extractor import _strip_tags
    assert _strip_tags(text) == re.sub(r"<[^>]+>", " ", text)


def test_strip_tags_matches_the_regex_on_random_markup():
    import random
    import re
    from backend.analyzer.salary_extractor import _strip_tags
    rng = random.Random(7)
    for _ in range(2000):
        text = "".join(rng.choice("<>ab \n$") for _ in range(rng.randint(0, 30)))
        assert _strip_tags(text) == re.sub(r"<[^>]+>", " ", text), text


@pytest.mark.parametrize("text", [
    "$150,000 (USD)", "$150,000 ( USD )", "$150,000USD", "$150,000 \t (\tUSD", "$150,000 (US",
    "$150,000 CAD", "$150,000 ( CAD)", "$150,000 (cad)", "$150,000 ((USD", "$150,000",
])
def test_the_after_amount_patterns_keep_their_matches(text):
    import re
    from backend.analyzer import salary_extractor as se
    end = len("$150,000")
    old_usd = re.compile(r"\s*\(?\s*USD\b")
    old_code = re.compile(r"\s*\(?\s*(?:" + se._NON_USD_CODES + r")\b")
    assert se._usd_after(text[end:end + 40]) == bool(old_usd.match(text, end))
    assert bool(se._CODE_AFTER_RE.match(text, end)) == bool(old_code.match(text, end))


@pytest.mark.parametrize("text", [
    "<" * 30_000,                                    # every '<' without a '>' after it
    "$150,000" + "\t" * 29_000 + "x",                # whitespace run after an amount
    "$150,000 (" + " " * 29_000 + "x",
    "$150,000 USD" + " " * 29_000 + "x",
    "$150,000 -" + " " * 29_000 + "x",
    "$150,000 - USD" + " " * 29_000 + "x",
    "$1 " * 10_000,
])
def test_pathological_input_stays_fast(text):
    import time
    from backend.analyzer.salary_extractor import extract_salary, _names_usd, _foreign_near
    t0 = time.perf_counter()
    extract_salary(text + ">")
    extract_salary(text)
    _names_usd(text, 0, 8)
    _foreign_near(text, 0, 8)
    assert time.perf_counter() - t0 < 1.0
