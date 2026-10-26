"""Which posting a job link points to, whatever the spelling.

One posting reaches us under many links: Greenhouse alone serves the same job as
`boards.greenhouse.io/<co>/jobs/<id>`, `job-boards.greenhouse.io/<co>/jobs/<id>`,
`…/embed/job_app?token=<id>` and `careers.<co>.com/?gh_jid=<id>`; Workday lists one
requisition under a URL per location; Amazon puts a language in front. `external_id`
hashes the link, so each spelling became its own job. `posting_key()` reads the
posting's identity out of the link instead (`greenhouse:8686811002`), and two jobs with
one key are one posting.

A link that names no known system gives None: the exact-URL dedup still covers it, and
guessing an identity from an unknown path would merge different jobs.

`unwrap()` reads a link wrapped in a click tracker (LinkedIn's DoubleClick ads) without a
request; `resolve_short_link()` follows a known short-link host (grnh.se) to the posting.
"""
import logging
import re
from typing import Optional
from urllib.parse import parse_qs, unquote, urlparse

logger = logging.getLogger("jobnavigator.postings")

# Raised whenever a reader changes what key a link gets, or the linking rules change
# (posting_links.py); on start, stored jobs are keyed and linked again when the install's
# version is older (seed.migrate_posting_keys). 3: a role skipped in one place shows elsewhere.
READER_VERSION = 3

_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"


def _host(parsed) -> str:
    host = (parsed.hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _is(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def _q(parsed) -> dict:
    return {k.lower(): v for k, v in parse_qs(parsed.query or "").items()}


def _first(qs: dict, key: str) -> Optional[str]:
    vals = qs.get(key) or []
    return vals[0].strip() if vals and vals[0].strip() else None


def _segments(path: str) -> list:
    return [unquote(p) for p in (path or "").split("/") if p]


# ── per-system readers: (parsed, host, segments, query) -> key | None ─────────

# Company career pages that put the Greenhouse job id in the path (the Greenhouse API's
# absolute_url for these boards is the same page with ?gh_jid=).
_GREENHOUSE_SITES = {
    "brex.com": re.compile(r"^/careers/(\d{7,})/?$"),
    "stripe.com": re.compile(r"^/(?:careers|jobs)/listing/[^/]+/(\d{6,})"),
}


def _greenhouse(parsed, host, seg, qs):
    # A greenhouse job id is global, so the board slug is not part of the key: the
    # embed, the hosted board and a company page with ?gh_jid= all name one posting.
    gh = _first(qs, "gh_jid")
    if gh and gh.isdigit():
        return f"greenhouse:{gh}"
    site = _GREENHOUSE_SITES.get(host)
    m = site.search(parsed.path or "") if site else None
    if m:
        return f"greenhouse:{m.group(1)}"
    if _is(host, "greenhouse.io"):
        token = _first(qs, "token")
        if "job_app" in seg and token and token.isdigit():
            return f"greenhouse:{token}"
        if "jobs" in seg:
            i = seg.index("jobs")
            if i + 1 < len(seg) and seg[i + 1].isdigit():
                return f"greenhouse:{seg[i + 1]}"
    return None


def _lever(parsed, host, seg, qs):
    if _is(host, "lever.co") and len(seg) >= 2 and re.fullmatch(_UUID, seg[1]):
        return f"lever:{seg[1].lower()}"
    return None


def _ashby(parsed, host, seg, qs):
    if _is(host, "ashbyhq.com"):
        for s in seg[1:]:
            if re.fullmatch(_UUID, s):
                return f"ashby:{s.lower()}"
    ashby = _first(qs, "ashby_jid")
    if ashby and re.fullmatch(_UUID, ashby):
        return f"ashby:{ashby.lower()}"
    return None


_WORKDAY_REQ = re.compile(r"_([A-Za-z0-9][A-Za-z0-9-]*\d[A-Za-z0-9-]*)$")


# Company career sites that show a Workday requisition under its own number:
# host -> (tenant, path pattern).
_WORKDAY_SITES = {
    "careers.bankofamerica.com": ("ghr", re.compile(r"/job-detail/(\d{6,})(?:/|$)")),
}


def _workday(parsed, host, seg, qs):
    # .../<site>/job/<location>/<Title>_<REQ>: one requisition is listed once per
    # location, each with its own path, so the key is tenant + requisition.
    # <tenant>.wdN.myworkdayjobs.com/..., or wdN.myworkdaysite.com/recruiting/<tenant>/<site>/...
    if host in _WORKDAY_SITES:
        tenant, pattern = _WORKDAY_SITES[host]
        m = pattern.search(parsed.path or "")
        return f"workday:{tenant}:{m.group(1)}" if m else None
    if "job" not in seg:
        return None
    if _is(host, "myworkdayjobs.com"):
        tenant = host.split(".")[0]
    elif _is(host, "myworkdaysite.com") and len(seg) >= 2 and seg[0] == "recruiting":
        tenant = seg[1].lower()
    else:
        return None
    m = _WORKDAY_REQ.search(seg[-1])
    if not m:
        return None
    return f"workday:{tenant}:{m.group(1).upper()}"


# Company career sites that show an iCIMS job under its own number: host -> (portal, path pattern).
_ICIMS_SITES = {
    "careers.docusign.com": ("uscareers-docusign", re.compile(r"/jobs/(\d+)(?:/|$)")),
}


def _icims(parsed, host, seg, qs):
    if _is(host, "icims.com") and len(seg) >= 2 and seg[0] == "jobs" and seg[1].isdigit():
        return f"icims:{host.split('.')[0]}:{seg[1]}"
    if host in _ICIMS_SITES:
        portal, pattern = _ICIMS_SITES[host]
        m = pattern.search(parsed.path or "")
        return f"icims:{portal}:{m.group(1)}" if m else None
    return None


def _oracle(parsed, host, seg, qs):
    # <pod>.fa.<region>.oraclecloud.com/hcmUI/CandidateExperience/<lang>/[sites/<site>/]job/<id>
    if (_is(host, "oraclecloud.com") or host == "careers.oracle.com") and "job" in seg:
        i = seg.index("job")
        if i + 1 < len(seg) and seg[i + 1].isdigit():
            return f"oracle:{host.split('.')[0]}:{seg[i + 1]}"
    return None


def _smartrecruiters(parsed, host, seg, qs):
    if _is(host, "smartrecruiters.com") and len(seg) >= 2:
        m = re.match(r"(\d{6,})", seg[1])
        if m:
            return f"smartrecruiters:{m.group(1)}"
    return None


def _rippling(parsed, host, seg, qs):
    if _is(host, "rippling.com") and "jobs" in seg:
        i = seg.index("jobs")
        if i + 1 < len(seg) and re.fullmatch(_UUID, seg[i + 1]):
            return f"rippling:{seg[i + 1].lower()}"
    return None


def _workable(parsed, host, seg, qs):
    if _is(host, "workable.com") and "j" in seg:
        i = seg.index("j")
        if i + 1 < len(seg) and re.fullmatch(r"[A-Za-z0-9]{6,}", seg[i + 1]):
            return f"workable:{seg[i + 1].upper()}"
    return None


def _jobvite(parsed, host, seg, qs):
    if _is(host, "jobvite.com") and "job" in seg:
        i = seg.index("job")
        if i + 1 < len(seg) and re.fullmatch(r"[A-Za-z0-9]{6,}", seg[i + 1]):
            return f"jobvite:{seg[i + 1]}"
    return None


def _dayforce(parsed, host, seg, qs):
    # jobs.dayforcehcm.com/<lang>/<tenant>/CANDIDATEPORTAL/jobs/<id>
    if _is(host, "dayforcehcm.com") and "jobs" in seg and len(seg) >= 3:
        i = seg.index("jobs")
        if i + 1 < len(seg) and seg[i + 1].isdigit():
            return f"dayforce:{seg[1].lower()}:{seg[i + 1]}"
    return None


def _ultipro(parsed, host, seg, qs):
    opp = _first(qs, "opportunityid")
    if _is(host, "ultipro.com") and opp and re.fullmatch(_UUID, opp):
        return f"ultipro:{opp.lower()}"
    return None


# White-labelled Eightfold sites, by the tenant name the eightfold.ai host uses.
_EIGHTFOLD_HOSTS = {"explore.jobs.netflix.net": "netflix"}


def _eightfold(parsed, host, seg, qs):
    # <tenant>.eightfold.ai/careers/job/<id> or /careers?pid=<id>
    if not (_is(host, "eightfold.ai") or host in _EIGHTFOLD_HOSTS):
        return None
    tenant = _EIGHTFOLD_HOSTS.get(host) or host.split(".")[0]
    pid = _first(qs, "pid")
    if pid and pid.isdigit():
        return f"eightfold:{tenant}:{pid}"
    if "job" in seg:
        i = seg.index("job")
        if i + 1 < len(seg) and seg[i + 1].isdigit():
            return f"eightfold:{tenant}:{seg[i + 1]}"
    return None


_SUCCESSFACTORS_PATH = re.compile(r"/job/[^/]+/(\d{6,})/?$")


def _career_platforms(parsed, host, seg, qs):
    """Hosted career-site platforms whose posting id sits in a fixed place, keyed per host."""
    path = parsed.path or ""
    # SAP SuccessFactors career sites (careers.ey.com, careers.capgemini.com, …): /<site>/job/<slug>/<id>/
    m = _SUCCESSFACTORS_PATH.search(path)
    if m and not _is(host, "myworkdayjobs.com"):
        return f"successfactors:{host}:{m.group(1)}"
    # Avature: /careers/JobDetail/<slug>/<id>, /jobs/FolderDetail/<slug>/<id>
    for marker in ("JobDetail", "FolderDetail"):
        if marker in seg:
            i = seg.index(marker)
            tail = seg[i + 1:]
            if tail and tail[-1].isdigit():
                return f"avature:{host}:{tail[-1]}"
    # Oracle Taleo: …/jobdetail.ftl?job=<id>
    job = _first(qs, "job")
    if _is(host, "taleo.net") and job:
        return f"taleo:{host.split('.')[0]}:{job.upper()}"
    # Gem: jobs.gem.com/<company>/<id>
    if host == "jobs.gem.com" and len(seg) >= 2:
        return f"gem:{seg[1]}"
    # A job id in the query: Avature's JobDetail?jobId= is the same posting as its
    # /JobDetail/<slug>/<id> path; UBS's …&jobId= on its own site. Only the leading digits
    # count (a board re-encodes its own tracking into it: jobId=1130%3Fsource%3Djobright).
    m = re.match(r"\d+", _first(qs, "jobid") or "")
    if m and "JobDetail" in seg:
        return f"avature:{host}:{m.group(0)}"
    if m and "JobDetails" in (parsed.query or ""):
        return f"site:{host}:{m.group(0)}"
    return None


# First-party career sites the company scrapers read, by the path their posting id sits in.
_SITE_PATHS = {
    "careers.servicenow.com": re.compile(r"^/jobs/(\d{6,})"),
    "uber.com": re.compile(r"/careers/list/(\d+)"),
    "coinbase.com": re.compile(r"/careers/positions/(\d+)"),
    "joinbytedance.com": re.compile(r"^/search/(\d{10,})"),
}


_PHENOM_PATH = re.compile(r"^/(?:[a-z]{2}|global)/[a-z]{2}/job/([A-Za-z0-9-]{4,})(?:/|$)")


def _phenom(parsed, host, seg, qs):
    # Phenom career sites (careers.cisco.com, careers.adobe.com, …): /<country>/<lang>/job/<id>/<slug>
    m = _PHENOM_PATH.match(parsed.path or "")
    return f"phenom:{host}:{m.group(1).upper()}" if m else None


def _adp(parsed, host, seg, qs):
    # ADP Workforce Now: …/recruitment.html?cid=<company>&jobId=<id>
    jid, cid = _first(qs, "jobid"), _first(qs, "cid")
    if _is(host, "adp.com") and jid:
        return f"adp:{(cid or '').lower()}:{jid}"
    return None


def _first_party(parsed, host, seg, qs):
    pattern = _SITE_PATHS.get(host)
    m = pattern.search(parsed.path or "") if pattern else None
    return f"site:{host}:{m.group(1)}" if m else None


_TALENTBREW_PATH = re.compile(r"^/job/[^/]+/[^/]+/(\d+)/(\d+)/?$")


def _talentbrew(parsed, host, seg, qs):
    # Radancy TalentBrew sites (jobs.citi.com, jobs.intuit.com, careers.blackrock.com, …):
    # /job/<city>/<title-slug>/<org id>/<job id>; the slugs vary ("-/-" on shared links).
    m = _TALENTBREW_PATH.match(parsed.path or "")
    if m:
        return f"talentbrew:{m.group(1)}:{m.group(2)}"
    return None


def _big_boards(parsed, host, seg, qs):
    """The large first-party career sites the company scrapers read."""
    if host == "amazon.jobs" and "jobs" in seg:
        i = seg.index("jobs")
        if i + 1 < len(seg) and seg[i + 1].isdigit():
            return f"amazon:{seg[i + 1]}"
    if host == "jobs.apple.com" and "details" in seg:
        # <role>-<location code>: Apple lists one role once per location (200684524-0836
        # in Cupertino, 200684524-2459 in New York), so the role number is the posting.
        i = seg.index("details")
        if i + 1 < len(seg):
            m = re.match(r"(\d+)(?:-\d+)?", seg[i + 1])
            if m:
                return f"apple:{m.group(1)}"
    if host == "google.com" and "results" in seg:
        i = seg.index("results")
        if i + 1 < len(seg):
            m = re.match(r"(\d{6,})", seg[i + 1])
            if m:
                return f"google:{m.group(1)}"
    if _is(host, "careers.microsoft.com") and "job" in seg:
        i = seg.index("job")
        if i + 1 < len(seg) and seg[i + 1].isdigit():
            return f"microsoft:{seg[i + 1]}"
    if host in ("metacareers.com", "facebook.com"):
        for key in ("job_details", "jobs"):
            if key in seg:
                i = seg.index(key)
                if i + 1 < len(seg) and seg[i + 1].isdigit():
                    return f"meta:{seg[i + 1]}"
    return None


def _linkedin(parsed, host, seg, qs):
    # A LinkedIn listing, not the employer's posting: it still tells two LinkedIn copies apart.
    if not _is(host, "linkedin.com"):
        return None
    if "view" in seg:
        i = seg.index("view")
        if i + 1 < len(seg):
            m = re.search(r"(\d{6,})$", seg[i + 1])
            if m:
                return f"linkedin:{m.group(1)}"
    cur = _first(qs, "currentjobid")
    if cur and cur.isdigit():
        return f"linkedin:{cur}"
    return None


def _indeed(parsed, host, seg, qs):
    if _is(host, "indeed.com"):
        jk = _first(qs, "jk") or _first(qs, "vjk")
        if jk and re.fullmatch(r"[0-9a-fA-F]{8,}", jk):
            return f"indeed:{jk.lower()}"
    return None


_READERS = (_greenhouse, _lever, _ashby, _workday, _icims, _oracle, _smartrecruiters, _rippling,
            _workable, _jobvite, _dayforce, _ultipro, _eightfold, _big_boards, _first_party,
            _phenom, _adp, _linkedin, _indeed, _talentbrew, _career_platforms)

# Keys that name the listing on a job board rather than the employer's own posting.
BOARD_KEY_PREFIXES = ("linkedin:", "indeed:")


def posting_key(url) -> Optional[str]:
    """The posting a link names (`greenhouse:8686811002`), or None for an unknown system."""
    if not url or not isinstance(url, str):
        return None
    try:
        parsed = urlparse(unwrap(url.strip()))
        if parsed.scheme not in ("http", "https"):
            return None
        host, seg, qs = _host(parsed), _segments(parsed.path), _q(parsed)
        for reader in _READERS:
            key = reader(parsed, host, seg, qs)
            if key:
                return key
    except Exception:  # a malformed link names nothing
        return None
    return None


def is_employer_key(key) -> bool:
    """True for a key from the employer's own system, False for a job board listing or None."""
    return bool(key) and not key.startswith(BOARD_KEY_PREFIXES)


# ── links that wrap or shorten the posting ───────────────────────────────────

_EMBEDDED = re.compile(r"(https?(?::|%3A)(?://|%2F%2F)[^\s;]+)", re.I)
_TRACKER_HOSTS = ("doubleclick.net",)


def unwrap(url: str) -> str:
    """The link inside a click tracker (ad.doubleclick.net/ddm/clk/…;…?https://…), else the link."""
    try:
        host = _host(urlparse(url))
    except Exception:
        return url
    if not any(_is(host, t) for t in _TRACKER_HOSTS):
        return url
    found = _EMBEDDED.findall(url[url.index(host) + len(host):])
    if not found:
        return url
    inner = found[-1]
    return unquote(inner) if "%3a" in inner[:8].lower() else inner


# Hosts that only redirect to a posting: Greenhouse's and LinkedIn's short links, and the
# Appcast and Phenom click trackers job ads go through.
SHORT_LINK_HOSTS = ("grnh.se", "lnkd.in", "click.appcast.io", "prng.co")


def is_short_link(url) -> bool:
    try:
        return any(_is(_host(urlparse(url)), h) for h in SHORT_LINK_HOSTS)
    except Exception:
        return False


async def resolve_short_links(urls, concurrency: int = 5) -> dict:
    """{short link: where it lands} for the short links among `urls`; a link that did not
    resolve, and every other link, is left out. Called before a source's insert loop, so no
    request waits while its session holds uncommitted rows."""
    import asyncio
    short = list(dict.fromkeys(u for u in urls if u and is_short_link(u)))
    if not short:
        return {}
    gate = asyncio.Semaphore(concurrency)

    async def one(u):
        async with gate:
            return u, await resolve_short_link(u)

    pairs = await asyncio.gather(*(one(u) for u in short))
    return {u: final for u, final in pairs if final != u}


async def resolve_short_link(url: str) -> str:
    """Follow a known short link (grnh.se) to where it lands; any other link comes back as is.

    Goes through `safe_get` (SSRF-checked at every hop). A failure keeps the short link.
    """
    if not is_short_link(url):
        return url
    from backend.scraper._shared.url_safety import safe_get
    try:
        resp = await safe_get(url, timeout=10)
        final = str(resp.url)
        return final if final and not is_short_link(final) else url
    except Exception as e:
        logger.debug(f"Short link {url} not resolved: {e}")
        return url
