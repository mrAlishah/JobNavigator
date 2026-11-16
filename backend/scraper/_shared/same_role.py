"""Whether two postings with one company and title are one role, read from their text.

An employer lists one role once per city (LinkedIn, Apple, Axon), posts several openings of it
on one day (Amazon), or re-posts it under a new id weeks later. Each copy has its own id, so no
link says they are one role; the text does. On 90 days of real postings, two rules came out of
the data:

- **Near enough is not enough.** Two Workday "Senior Product Manager" postings from one office
  were 94% alike and were for different products; two Cisco ones differed only in "4+" against
  "6+ years". So the texts must be the same word for word, apart from what a per-city or
  re-posted copy changes: codes with three digits or more (job ids, reference numbers, pay,
  dates), dollar amounts, US state names, known city names ("based in our Denver office" when
  the location says "United States"), and the place words of either posting's own location.
  No other word may differ (`MAX_OTHER_WORDS`).
- **A shared template proves nothing.** Different roles can carry one text (Sierra's Spanish- and
  Portuguese-speaking strategists, PayPal's company-wide description). When the company uses the
  text under another title too, the title is what tells roles apart, so two postings that share
  it are not taken for one role.

Too little text (a listing without a description, a short blurb) proves nothing either.
"""
import difflib
import html
import re

MIN_WORDS = 100
# Any other word that differs means another role: Apple's "Product Manager, Employee Experience"
# postings differ only in naming "legal, finance" or "music" as the area.
MAX_OTHER_WORDS = 0
TEMPLATE_LIKENESS = 0.9

_TAG = re.compile(r"<[^>]+>")
_TOKEN = re.compile(r"\$?[a-z0-9]+")
# Pay and place words a per-city copy changes along with the place.
_PLACE_WORDS = frozenset({"usa", "us", "usd", "annually", "annual", "hourly", "yearly"})
# A per-city copy names its state in full ("Seattle, Washington") where the location says "SEATTLE".
_STATES = re.compile(r"\b(?:" + "|".join(sorted((
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut", "delaware",
    "district of columbia", "florida", "georgia", "hawaii", "idaho", "illinois", "indiana", "iowa",
    "kansas", "kentucky", "louisiana", "maine", "maryland", "massachusetts", "michigan", "minnesota",
    "mississippi", "missouri", "montana", "nebraska", "nevada", "new hampshire", "new jersey",
    "new mexico", "new york", "north carolina", "north dakota", "ohio", "oklahoma", "oregon",
    "pennsylvania", "rhode island", "south carolina", "south dakota", "tennessee", "texas", "utah",
    "vermont", "virginia", "washington", "west virginia", "wisconsin", "wyoming"), key=len, reverse=True))
    + r")\b")
# A code with a run of three digits or more, with its joined parts: a reference number
# (R-482211-8), a requisition (JR363445-1), a date (2026-10-21), pay (151,000.00).
_CODES = re.compile(r"[a-z0-9]*\d{3,}[a-z0-9]*(?:[-_/.][a-z0-9]+)*")

# City names a per-city copy writes in its text when its location field does not name the city
# ("based in our Boston office" / "Denver", location "United States"): the cities and metros
# analyzer/location.py knows, with a state or province code right after one ("Seattle, WA").
# Names that are also ordinary words or people's names stay words: a difference in them counts.
_NOT_CITIES = frozenset({
    "orange", "corona", "venice", "concord", "davis", "carson", "campbell", "hamilton", "milton",
    "lancaster", "victoria", "regina", "valencia", "burlington", "surrey", "langley", "windsor",
    "richmond", "stanford", "phoenix", "hawthorne", "glendale", "riverside", "kingston", "waterloo",
    "belmont", "saratoga", "sonoma", "napa", "industry", "union city", "danville", "east coast",
    "west coast", "tri-state", "tri state", "dmv"})
_CODE_WORDS = frozenset({"in", "or", "me", "ok", "hi", "de", "la", "pa", "ma", "al", "co", "oh", "id", "on"})


def _city_pattern():
    from backend.analyzer.location import ALL_REGION_CODES, BARE_CITIES, CALIFORNIA_CITIES, CANADA_CITIES, METROS
    names = (set(BARE_CITIES) | set(CALIFORNIA_CITIES) | set(CANADA_CITIES) | set(METROS)) - _NOT_CITIES
    codes = sorted(c.lower() for c in ALL_REGION_CODES | {"DC"})
    # "in", "or", "me", "ok", "hi"... are words too: as a code they need the comma ("Boston, MA")
    plain = [c for c in codes if c not in _CODE_WORDS]
    return re.compile(r"\b(" + "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
                      + r")\b(?:\s+(?:metropolitan|metro))?(?:\s+area)?(?:\s*,\s*(?:" + "|".join(codes) + r")\b|\s+(?:" + "|".join(plain) + r")\b)?")


_CITIES = _city_pattern()


def text_places(text) -> set:
    """The known cities a posting's text names (lower-case)."""
    return {m.group(1) for m in _CITIES.finditer(_TAG.sub(" ", html.unescape(str(text or ""))).lower())}


def words(text) -> list:
    """The posting's words, lower-case, markup removed."""
    if not text:
        return []
    return _TOKEN.findall(_TAG.sub(" ", html.unescape(str(text))).lower())


# Words a location field carries that are not a place ("Remotely in the UK", "Berlin Office"):
# a difference in them still counts ("remote" against "hybrid" is another job).
_NOT_PLACES = frozenset({
    "a", "an", "and", "any", "area", "at", "for", "headquarters", "hq", "hybrid", "in", "location",
    "locations", "more", "multiple", "of", "office", "offices", "on", "onsite", "or", "remote",
    "remotely", "site", "the", "to", "within"})


def _place_words(*locations) -> set:
    out = set(_PLACE_WORDS)
    for loc in locations:
        out.update(w for w in words(loc) if w not in _NOT_PLACES)
    return out


def role_words(text, *locations) -> list:
    """`text` without what a per-city or re-posted copy of a role changes: dollar amounts,
    numbers of three digits and more (ids, pay, years, dates), known city and state names, and the
    words of `locations`. Short numbers stay: "4+ years" against "6+ years" is another role."""
    drop = _place_words(*locations)
    text = _TAG.sub(" ", html.unescape(str(text or ""))).lower()
    text = _CODES.sub(" ", _STATES.sub(" ", _CITIES.sub(" ", text)))
    return [w for w in words(text)
            if not (w.startswith("$") or (w.isdigit() and (len(w) >= 3 or w == "00")) or w in drop)]


# Title spellings of one word, and levels written as digits ("Engineer 2" is "Engineer II").
_TITLE_WORDS = {"sr": "senior", "snr": "senior", "jr": "junior", "mgr": "manager", "engr": "engineer",
                "dir": "director", "assoc": "associate", "mgmt": "management",
                "1": "i", "2": "ii", "3": "iii", "4": "iv", "5": "v"}
# A requisition number with its letter prefix: "R-482211", "JR363445-1".
_TITLE_CODE = re.compile(r"\b(?:[a-z]+[-_/])?" + _CODES.pattern)


def title_key(title) -> str:
    """A title as the same-role check compares it: case, punctuation, word order, common short
    forms ("Sr", "Mgr", "&") and requisition numbers do not count. Every word in brackets counts:
    Sierra's "(Spanish speaking)" and "(Brazilian Portuguese speaking)" are two roles."""
    t = _TITLE_CODE.sub(" ", html.unescape(str(title or "")).lower().replace("&", " and "))
    return " ".join(sorted(_TITLE_WORDS.get(w, w) for w in re.findall(r"[a-z0-9]+", t)))


def role_hash(company, title) -> str:
    """The key jobs that may be one role share: the company and `title_key`."""
    import hashlib
    raw = f"{(company or '').strip().lower()}\n{title_key(title)}"
    return hashlib.sha256(raw.encode()).hexdigest()


def other_words(a: list, b: list) -> int:
    """How many words differ between two word lists (the longer side of each changed run)."""
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    return sum(max(i2 - i1, j2 - j1) for op, i1, i2, j1, j2 in sm.get_opcodes() if op != "equal")


def same_text(text_a, text_b, location_a=None, location_b=None) -> bool:
    """True when the two texts are one role's text: both long enough, and the same apart from
    ids, pay, dates and the two locations, give or take `MAX_OTHER_WORDS` words."""
    a = role_words(text_a, location_a, location_b)
    b = role_words(text_b, location_a, location_b)
    if min(len(a), len(b)) < MIN_WORDS or abs(len(a) - len(b)) > MAX_OTHER_WORDS:
        return False
    return other_words(a, b) <= MAX_OTHER_WORDS


def shingles(text) -> frozenset:
    """Five-word runs of the text, for a quick likeness between two texts."""
    w = role_words(text)
    return frozenset(hash(" ".join(w[i:i + 5])) for i in range(len(w) - 4))


def likeness(a: frozenset, b: frozenset) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def is_template(text, other_title_texts) -> bool:
    """True when the company uses this text (90% alike or more) under another title."""
    mine = shingles(text)
    if not mine:
        return False
    for other in other_title_texts:
        theirs = other if isinstance(other, frozenset) else shingles(other)
        # a text far shorter or longer cannot be 90% alike
        if theirs and min(len(mine), len(theirs)) / max(len(mine), len(theirs)) >= TEMPLATE_LIKENESS \
                and likeness(mine, theirs) >= TEMPLATE_LIKENESS:
            return True
    return False
