"""Salary extraction from JD text + H-1B LCA median fallback, and the shared period/currency
helpers every source uses on its own salary strings.

Only yearly US-dollar figures are stored. An amount tagged hourly, daily, weekly or monthly is
skipped, never annualised (the convention every source follows: freehire's `_annual_salary`,
apify's `_annual_from_text`, CaribbeanJobs' and TrulyRemote's USD-annual gates), so the posted
text in the description is what shows it. An explicit yearly tag ("annually", "per year") after
an amount wins over a period named before it. A yearly figure under 1000, an untagged one under
10,000 and one over 2,000,000 are not salaries. A dollar with a country prefix (TT$, J$, HK$, A$, C$,
NZ$, …) or a non-USD ISO code next to it is not USD and is never read.
"""
import re
import logging
import unicodedata

logger = logging.getLogger("jobnavigator.salary")

# Below this, a figure with no "k" is not a yearly salary ("$34.32 to $42.13" is hourly pay).
_MIN_YEARLY = 1000
# Above this, a "yearly" figure is not a US salary ("J$2,400,000" read without its prefix).
_MAX_YEARLY = 2_000_000
# An amount with no period at all ("USD 4,000 - 6,000", "$5,000 - $8,000") under this is monthly
# pay or a bonus far more often than a yearly salary, so it is not stored as one.
UNSTATED_PERIOD_FLOOR = 10_000


# ── Periods ─────────────────────────────────────────────────────────────────

# Shorter periods first: a salary string naming both ("$60/hr ($124,800 a year)") is not a clean
# yearly figure for a source field, so the shorter one wins there.
_PERIOD_RES = tuple((unit, re.compile(rx, re.IGNORECASE)) for unit, rx in (
    ("HOUR", r"\bhourly\b|\b(?:per|an?|each|every)\s+(?:hour|hr)s?\b|/\s*(?:hour|hr|h)s?\b"
             r"|\bp\s*/\s*h\b|\bp\.\s*h\.|\bph\b"),
    ("DAY", r"\bdaily\b|\bday\s+rate\b|\b(?:per|an?|each|every)\s+days?\b|/\s*(?:day|d)s?\b|\bp\s*/\s*d\b"),
    ("WEEK", r"\bweekly\b|\b(?:per|an?|each|every)\s+(?:week|wk)s?\b|/\s*(?:week|wk|w)s?\b"
             r"|\bp\s*/\s*w\b|\bpw\b"),
    ("MONTH", r"\bmonthly\b|\bpcm\b|\b(?:per|an?|each|every)\s+(?:month|mo|mth)s?\b"
              r"|/\s*(?:month|mo|mth|m)s?\b|\bp\s*/\s*m\b"),
    ("YEAR", r"\b(?:yearly|annual|annually|annum)\b|\b(?:per|an?|each|every)\s+(?:year|yr)s?\b"
             r"|/\s*(?:year|yr|annum)s?\b|\bp\.?a\.?(?![a-z])"),
))
# A bare unit as a structured field gives it (schema.org `unitText`: "MONTH", "YEAR").
_BARE_UNITS = {"hour": "HOUR", "day": "DAY", "week": "WEEK", "month": "MONTH", "year": "YEAR"}
_NON_ANNUAL_UNITS = ("HOUR", "DAY", "WEEK", "MONTH")


def salary_period(text: str) -> str:
    """The pay period a salary string names: HOUR, DAY, WEEK, MONTH or YEAR ("" when it names
    none). A shorter period wins over a yearly one named in the same string."""
    if not text:
        return ""
    bare = _BARE_UNITS.get(re.sub(r"^per[\s_]+", "", text.strip().lower()).rstrip("s"))
    if bare:
        return bare
    for unit, rx in _PERIOD_RES:
        if rx.search(text):
            return unit
    return ""


def is_non_annual(text: str) -> bool:
    """True when a salary string names a period shorter than a year (hour, day, week, month)."""
    return salary_period(text) in _NON_ANNUAL_UNITS


# ── Currencies ──────────────────────────────────────────────────────────────

# ISO codes of currencies other than the US dollar that postings name.
_NON_USD_CODES = (
    "EUR|GBP|CAD|AUD|NZD|HKD|CHF|SEK|NOK|DKK|PLN|CZK|HUF|RON|UAH|RUB|INR|PKR|BDT|BRL|MXN|ARS|"
    "CLP|COP|ZAR|NGN|KES|EGP|JPY|CNY|KRW|TWD|SGD|MYR|IDR|THB|VND|PHP|AED|ILS|TRY|"
    "TTD|JMD|BBD|XCD|KYD|BSD|GYD|BZD|BMD|ANG|AWG|DOP|HTG|SRD"
)
_NON_USD_CODE_RE = re.compile(r"\b(" + _NON_USD_CODES + r")\b")
# Dollars with a country prefix. Two-letter prefixes may stand apart ("TT $8,000"); a single
# letter only counts written on ("A$120k"), so "a $120k salary" stays USD.
_DOLLAR_PREFIXES = {
    "AU": "AUD", "A": "AUD", "CA": "CAD", "C": "CAD", "NZ": "NZD", "HK": "HKD", "SG": "SGD",
    "S": "SGD", "R": "BRL", "MX": "MXN", "NT": "TWD", "TT": "TTD", "J": "JMD", "EC": "XCD",
    "BZ": "BZD", "BDS": "BBD", "BD": "BBD", "CI": "KYD", "G": "GYD", "B": "BSD",
}
_MULTI_PREFIXES = "BDS|BD|AU|CA|NZ|HK|SG|MX|NT|TT|EC|BZ|CI"
_FOREIGN_DOLLAR_RE = re.compile(r"\b(" + _MULTI_PREFIXES + r")\s?\$|\b(A|C|S|R|J|G|B)\$")
_SYMBOLS = {"€": "EUR", "£": "GBP", "¥": "JPY", "₹": "INR", "₱": "PHP", "₦": "NGN", "₩": "KRW",
            "₺": "TRY", "₪": "ILS", "₫": "VND", "฿": "THB", "₽": "RUB", "₴": "UAH", "₨": "PKR"}
_USD_RE = re.compile(r"\b[Uu][Ss][Dd]\b|\bUS\s?\$")


def foreign_currency(text: str) -> str:
    """The first currency other than the US dollar a salary string names, as an ISO code (or the
    symbol itself when it is not a known one): a prefixed dollar (TT$, A$, HK$, …), an ISO code
    (EUR, CAD, TTD, …) or a currency symbol (€ £ ¥ ₹ …). "" when it names none."""
    if not text:
        return ""
    m = _FOREIGN_DOLLAR_RE.search(text)
    if m:
        return _DOLLAR_PREFIXES[m.group(1) or m.group(2)]
    m = _NON_USD_CODE_RE.search(text)
    if m:
        return m.group(1)
    for c in text:
        if c != "$" and unicodedata.category(c) == "Sc":
            return _SYMBOLS.get(c, c)
    return ""


def currency_code(text: str) -> str:
    """The currency a salary string names: "USD" for USD / US$, another ISO code for any other
    currency, "" when it names none (a bare "$" names none)."""
    foreign = foreign_currency(text)
    if foreign:
        return foreign
    return "USD" if text and _USD_RE.search(text) else ""


def is_usd(text: str, bare_dollar: bool = True) -> bool:
    """True when a salary string is in US dollars: it names USD or US$, or (with `bare_dollar`,
    for boards where that is the norm) a bare "$", and no other currency."""
    code = currency_code(text)
    return code == "USD" or (bare_dollar and code == "" and "$" in (text or ""))


def yearly_usd(low, high, currency: str, period: str) -> tuple:
    """(low, high) to store from a source's structured salary, (None, None) unless it is USD per
    year. An unstated period counts as yearly only from UNSTATED_PERIOD_FLOOR up."""
    if not low or (currency or "") != "USD":
        return None, None
    period = (period or "").upper()
    if period not in ("", "YEAR"):
        return None, None
    if low < (_MIN_YEARLY if period else UNSTATED_PERIOD_FLOOR) or max(low, high or 0) > _MAX_YEARLY:
        return None, None
    return low, high


# ── The description extractor ───────────────────────────────────────────────

# A period tag right after an amount: "/hr", "per hour", "an hour", "hourly", "p/h", "ph",
# "a day", "/wk", "per month", "pcm", … An optional currency code or "(USD)" may come first.
_PERIOD_WORDS = (
    r"(?:per|an?|each|every|/)\s*(?:hour|hr)s?\b|hourly\b|p\s*/\s*h\b|p\.\s*h\.|ph\b|/\s*h\b"
    r"|(?:per|an?|each|every|/)\s*day\b|daily\b|p\s*/\s*d\b|/\s*d\b"
    r"|(?:per|an?|each|every|/)\s*(?:week|wk)s?\b|weekly\b|p\s*/\s*w\b|pw\b|/\s*w\b"
    r"|(?:per|an?|each|every|/)\s*(?:month|mo|mth)s?\b|monthly\b|p\s*/\s*m\b|pcm\b|/\s*m\b"
)
_TAG_PREFIX = r"\s*(?:\(?\s*(?:USD|US\s?\$|dollars?)\s*\)?)?\s*\(?\s*"
_PERIOD_AFTER_RE = re.compile(_TAG_PREFIX + r"(?:" + _PERIOD_WORDS + r")", re.IGNORECASE)
_YEARLY_WORDS = (
    r"per\s*(?:year|annum)|annually\b|annual\b|yearly\b|an?\s*year\b|/\s*(?:yr|year|annum)\b"
    r"|p\.?a\.?(?![a-z])"
)
# An explicit yearly tag right after the amount wins over any period word before it:
# "Paid monthly: $150,000 - $180,000 per year".
_YEARLY_AFTER_RE = re.compile(_TAG_PREFIX + r"(?:" + _YEARLY_WORDS + r")", re.IGNORECASE)
_YEARLY_IN_RE = re.compile(_YEARLY_WORDS, re.IGNORECASE)
# The tail of an earlier amount ("k" and its own period tag): "$30 an hour ($62,400 a year)"
# — the "an hour" belongs to the $30, not to the yearly figure after it.
_EARLIER_TAIL_RE = re.compile(r"[kK]?" + _PERIOD_AFTER_RE.pattern, re.IGNORECASE)
# A work schedule is not a pay period: "40 hours per week", "3 days a week", "2 days/week".
_SCHEDULE_RE = re.compile(
    r"\b(?:hours?|hrs?|days?|shifts?|nights?|weeks?|months?)\s*(?:per|an?|each|every|/)\s*"
    r"(?:day|week|wk|month|mo|year|yr)s?\b",
    re.IGNORECASE,
)
# A period named just before the amount in the same clause: "Hourly pay range: $34 - $42",
# "Monthly salary: $8,000". The clause stops at a sentence break, a comma or an earlier
# number, so "Hourly: $34 - $42 or $70,000 - $87,000" still reads the yearly pair.
_PERIOD_BEFORE_RE = re.compile(
    r"\b(?:hourly|daily|weekly|monthly|day\s+rate)\b|\b(?:per|an?)\s+(?:hour|hr|day|week|month)\b",
    re.IGNORECASE,
)
_CLAUSE_BREAK_RE = re.compile(r"[.;!?|,](?:\s|$)|\n|\d")
# Hour, day and week words (not month: a monthly figure can reach the yearly floor).
_SHORT_PERIOD_RE = re.compile(r"hour|hr|da(?:y|ily)|week", re.IGNORECASE)

# A non-USD code or a spaced country prefix right before the "$" ("CAD $150,000", "TT $180,000";
# not "CA $", which is far more often California), or a code right after the amount.
_CODE_BEFORE_RE = re.compile(r"\b(?:" + _NON_USD_CODES + r"|BDS|BD|AU|NZ|HK|SG|MX|NT|TT|EC|BZ|CI)\s*$")
_CODE_AFTER_RE = re.compile(r"\s*\(?\s*(?:" + _NON_USD_CODES + r")\b")
_USD_BEFORE_RE = re.compile(r"\bUSD?\s*$")
_USD_AFTER_RE = re.compile(r"\s*\(?\s*USD\b")


def _period_before(text: str, start: int) -> bool:
    """True when a shorter period is named earlier in the amount's own clause. An hour, day or
    week word inside a parenthetical that closes before the amount is an aside, not the amount's
    period: "(Exempt or non-exempt hourly rate)role ranges from $140,000 to $160,000"."""
    before = text[max(0, start - 60):start]
    breaks = list(_CLAUSE_BREAK_RE.finditer(before))
    if breaks:
        last = breaks[-1]
        before = before[last.end():]
        if last.group().isdigit():
            tail = _EARLIER_TAIL_RE.match(before)
            if tail:
                before = before[tail.end():]
    before = _SCHEDULE_RE.sub(" ", before)
    for m in _PERIOD_BEFORE_RE.finditer(before):
        paren = re.search(r"[()]", before[m.end():])
        if not (paren and paren.group() == ")" and _SHORT_PERIOD_RE.search(m.group())):
            return True
    return False


def _not_yearly(text: str, start: int, end: int, low: int) -> bool:
    """True when the amount at text[start:end] is not a yearly salary: tagged hourly, daily,
    weekly or monthly right after it, or (unless it is tagged yearly itself) earlier in the same
    clause, or untagged and under UNSTATED_PERIOD_FLOOR."""
    if _PERIOD_AFTER_RE.match(text, end):
        return True
    if _YEARLY_IN_RE.search(text[start:end]) or _YEARLY_AFTER_RE.match(text, end):
        return False
    return low < UNSTATED_PERIOD_FLOOR or _period_before(text, start)


def _foreign_near(text: str, start: int, end: int) -> bool:
    """True when the amount at text[start:end] is in another currency named next to it."""
    span = text[start:end]
    if _FOREIGN_DOLLAR_RE.search(span) or _NON_USD_CODE_RE.search(span):
        return True
    if _CODE_BEFORE_RE.search(text[max(0, start - 8):start]):
        return True
    return bool(_CODE_AFTER_RE.match(text, end))


def _names_usd(text: str, start: int, end: int) -> bool:
    """True when the amount at text[start:end] says US dollars explicitly (USD, US$)."""
    return bool(_USD_RE.search(text[start:end]) or _USD_BEFORE_RE.search(text[max(0, start - 6):start])
                or _USD_AFTER_RE.match(text, end))


def _to_int(amount: str) -> int:
    return int(amount.replace(",", ""))


# A "$" that is not part of a prefixed dollar (HK$, A$, TT$): USD, US$, USD$ or a bare "$".
# A prefix is a run of one to three capitals ("HK$", "BDS$", "RD$", also glued on as in
# "SalaryHK$"); a "$" after a longer or lower-case word is a missing space ("Compensation$102,460").
_BARE = r"(?-i:(?<![A-Z])|(?<=[A-Z]{4}))\$"  # case-sensitive inside IGNORECASE patterns too
_D = r"(?:(?:\b|(?-i:(?<=[a-z])))USD?\s?\$|" + _BARE + r")"
_D_OPT = r"(?:US\s?\$|USD\s*\$?|" + _BARE + r")?"
_SEP = r"(?:[-–—/]+|(?:and\s+)?up\s+to|to)"

# Each pattern yields (low, high) from a match; k-notation multiplies by 1000.
_PATTERNS = (
    # 1: $XXX,XXX - $XXX,XXX, e.g. $140,000 USD - $210,000 USD, USD$150,000 - USD$200,000,
    #    $122,550.00-$201,000.00. Cents are matched and dropped.
    (re.compile(
        _D + r'\s*(\d{1,3}(?:,\d{3})*)(?:\.\d{1,2})?\s*(?:USD|per\s*year|per\s*annum|annually|/\s*yr|/\s*year|a\s*year)?\s*(?:[-–—/]+|(?:and\s+)?up\s+to|to|and)\s*' + _D_OPT + r'\s*(\d{1,3}(?:,\d{3})*)(?:\.\d{1,2})?'
    ), lambda m: (_to_int(m.group(1)), _to_int(m.group(2)))),
    # 1b: bare numbers with USD: 173,900.00 - 235,200.00 USD
    (re.compile(
        r'(\d{1,3}(?:,\d{3})*)(?:\.\d{1,2})?\s*(?:USD\s*)?' + _SEP + r'\s*(\d{1,3}(?:,\d{3})*)(?:\.\d{1,2})?\s*USD'
    ), lambda m: (_to_int(m.group(1)), _to_int(m.group(2)))),
    # 1c: bare numbers after USD: USD 120,000 - 150,000
    (re.compile(
        r'\bUSD\s*(\d{1,3}(?:,\d{3})+)(?:\.\d{1,2})?\s*' + _SEP + r'\s*(?:USD\s*)?(\d{1,3}(?:,\d{3})+)(?:\.\d{1,2})?'
    ), lambda m: (_to_int(m.group(1)), _to_int(m.group(2)))),
    # 2: $XXXk - $XXXk
    (re.compile(
        _D + r'\s*(\d{2,3})\s*[kK]\s*' + _SEP + r'\s*' + _D + r'\s*(\d{2,3})\s*[kK]'
    ), lambda m: (int(m.group(1)) * 1000, int(m.group(2)) * 1000)),
    # 3: $XXX,XXX per year / annually (single amount)
    (re.compile(
        _D + r'\s*(\d{1,3}(?:,\d{3})*)(?:\.\d{1,2})?\s*(?:per\s*year|per\s*annum|annually|/\s*yr|/\s*year|a\s*year)',
        re.IGNORECASE,
    ), lambda m: (_to_int(m.group(1)),) * 2),
    # 4: $XXXk (single)
    (re.compile(_D + r'\s*(\d{2,3})\s*[kK]'), lambda m: (int(m.group(1)) * 1000,) * 2),
    # 5: a single bare $XXX,XXX of at least 30,000 (not the start of $X,XXX,XXX)
    (re.compile(_D + r'\s*(\d{3},\d{3})(?!,\d)(?:\.\d{1,2})?'),
     lambda m: (_to_int(m.group(1)),) * 2 if _to_int(m.group(1)) >= 30000 else None),
)


def _find_yearly(description: str, explicit_usd: bool = False):
    """(low, high) of the first yearly USD salary in the text, skipping amounts tagged with a
    shorter period, in another currency, or implausibly small or large. Once a match is
    rejected, no later pattern reads part of it (a rejected range's second half alone)."""
    rejected = []
    for pattern, values in _PATTERNS:
        for match in pattern.finditer(description):
            start, end = match.span()
            if any(start < r_end and r_start < end for r_start, r_end in rejected):
                continue
            found = values(match)
            if not found:
                continue
            low, high = found
            if (min(low, high) < _MIN_YEARLY or max(low, high) > _MAX_YEARLY
                    or _foreign_near(description, start, end)
                    or (explicit_usd and not _names_usd(description, start, end))
                    or _not_yearly(description, start, end, min(low, high))):
                rejected.append((start, end))
                continue
            return low, high
    return None


def extract_salary(description: str, h1b_median_salary: int = None, explicit_usd: bool = False) -> dict:
    """Extract salary range from job description text, falling back to H-1B LCA median if none
    found. With `explicit_usd`, a bare "$" does not count as US dollars (boards where "$" is
    usually a local dollar)."""
    if not description:
        if h1b_median_salary:
            return {
                "salary_min": h1b_median_salary,
                "salary_max": h1b_median_salary,
                "salary_source": "lca_estimate",
            }
        return {"salary_min": None, "salary_max": None, "salary_source": "unknown"}

    # Cap input size before any regex runs (matches the scraper's 30K truncation) to bound the
    # HTML stripper + salary regex against pathological input.
    description = description[:30_000]

    if "<" in description and ">" in description:
        import html as _html
        description = _html.unescape(re.sub(r'<[^>]+>', ' ', description))

    found = _find_yearly(description, explicit_usd=explicit_usd)
    if found:
        return {"salary_min": found[0], "salary_max": found[1], "salary_source": "posting"}

    # Fallback to LCA median
    if h1b_median_salary:
        return {
            "salary_min": h1b_median_salary,
            "salary_max": h1b_median_salary,
            "salary_source": "lca_estimate",
        }

    return {"salary_min": None, "salary_max": None, "salary_source": "unknown"}


def apply_salary_to_job(job, company_h1b_median: int = None) -> None:
    """Extract salary from job description and update job fields; only updates if no salary is already set from the scraper."""
    if job.salary_min and job.salary_source == "posting":
        return  # Already have salary from scraper

    result = extract_salary(job.description or "", company_h1b_median)
    job.salary_min = result["salary_min"]
    job.salary_max = result["salary_max"]
    job.salary_source = result["salary_source"]
