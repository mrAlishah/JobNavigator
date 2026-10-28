"""Select real consumer-app PM postings from the live DB for the public demo (read-only).

    python -m backend.scripts.demo_export <out_dir> [--featured-pool 30] [--apps 30]

Writes <out_dir>/export.json: the postings (sanitised) that demo_assemble.py combines with the
hand-written sample content into the demo repo's data.json. Nothing is written to the database.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
import uuid
from datetime import date
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from backend.scraper._shared.urls import host_matches

PM_TITLE = re.compile(r"\bproduct manager\b", re.I)
TITLE_EXCLUDE = re.compile(
    r"technical|program|marketing|\bdata\b|analytics|security|hardware|principal|director|head of|"
    r"\bvp\b|intern|\bstaff\b|\bgroup\b|\blead\b|engineering|infrastructure|cloud|devops|sales|partner|"
    r"\bgtm\b|compliance|risk|lending|\bai\b|\bml\b|platform|systems|enterprise|\(m/|\bm/w\b|\bsr\.?\s*mgr",
    re.I,
)
NON_US = re.compile(
    r"germany|india|canada|united kingdom|\buk\b|london|france|spain|ireland|australia|singapore|brazil|"
    r"mexico|poland|netherlands|israel|japan|china|hesse|frankfurt|berlin|toronto|vancouver|dublin", re.I)
CONSUMER_TERMS = {
    "consumer": 3, "mobile app": 3, "app store": 2, "users": 1, "subscribers": 2, "members": 1,
    "growth": 2, "onboarding": 2, "retention": 2, "engagement": 2, "marketplace": 2, "shoppers": 2,
    "creators": 2, "experimentation": 1, "a/b": 1, "ios": 1, "android": 1, "millions": 1,
    "personalization": 1, "activation": 1, "monetization": 1,
}
B2B_TERMS = ("b2b", "enterprise customers", "saas platform", "government", "clearance", "federal")
SNAPSHOT_HOSTS = re.compile(r"greenhouse\.io|ashbyhq\.com|jobs\.lever\.co", re.I)
TRACKING = re.compile(r"^(utm_|gh_src|lever-source|iis|iisn|source$|src$|lang$|feedid|ref$|mode$|hub$|in_iframe|t$)", re.I)
NAMESPACE = uuid.UUID("5d0f6f0e-9a7e-4c2e-8f59-0f2d6a1e7c11")

_EMAIL = r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"
_PHONE = re.compile(r"(?<![\w/])(?:\+?\d[\s().-]?){9,15}\d(?![\w/])")
_NAME = r"[A-Z][a-z]+(?:[ ]+[A-Z][a-zA-Z'-]+){1,2}"
_NAME_AFTER_ROLE = re.compile(
    rf"\b((?:contact|reach out to|speak with|speak to|recruiter|talent partner|recruiting coordinator|"
    rf"hiring manager|recruiting partner|sourcer)\s*:?\s+)({_NAME})")


def sanitize(text: str) -> str:
    """Strip recruiter/contact names, emails and phone numbers from posting text."""
    if not text:
        return text
    out = text

    out = re.sub(rf"\b(?:contact|email|e-mail|reach out to|write to|call)\s+(?:{_NAME}\s+)?(?:at|on|:)?\s*"
                 rf"(?:{_EMAIL})(?:\s*(?:,|or|and)?\s*(?:\+?\d[\s().-]?){{9,15}}\d)?",
                 lambda m: m.group(0).split()[0] + " our recruiting team", out, flags=re.I)
    out = re.sub(rf"\b(Contact|Email|Reach out to)\s+({_NAME})\s+(?:at|on)\s+", r"\1 our recruiting team at ", out)
    out = _NAME_AFTER_ROLE.sub(lambda m: m.group(1) + "our recruiting team", out)
    out = re.sub(_EMAIL, "", out)
    out = _PHONE.sub("", out)
    out = re.sub(r"(our recruiting team)\s+(?:at|on)\s*(?=[,.;:)\s]|$)", r"\1", out)
    out = re.sub(r"[ \t]+([,.;:])", r"\1", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    return out


def clean_url(url: str | None) -> str | None:
    if not url:
        return url
    p = urlsplit(url)
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not TRACKING.match(k)]
    return urlunsplit((p.scheme, p.netloc, p.path, urlencode(q), ""))


def _consumer_score(job) -> int:
    blob = f"{job.title or ''}\n{(job.description or '')[:4000]}".lower()
    score = sum(w for term, w in CONSUMER_TERMS.items() if term in blob)
    score -= 3 * sum(1 for t in B2B_TERMS if t in blob)
    return score


def _eligible(job) -> bool:
    title = html.unescape(job.title or "")
    if not PM_TITLE.search(title) or TITLE_EXCLUDE.search(title):
        return False
    if job.loc_country and job.loc_country.upper() != "US":
        return False
    if NON_US.search(job.location or ""):
        return False
    if not job.company or not job.url or host_matches(job.url, "linkedin.com"):
        return False
    return True


def select_postings(db, n_featured: int = 15, n_apps: int = 30):
    """(featured, apps): distinct consumer-app PM postings from distinct companies the owner has not applied to.

    Featured ones need a long description and an ATS page that snapshots well; the rest only need a
    usable description. Ranked by how consumer-app-like the text is, newest first on ties.
    """
    from sqlalchemy import select
    from backend.models.db import Application, Job

    applied = {r[0] for r in db.execute(select(Application.job_id)).all()}
    rows = db.query(Job).filter(Job.description.isnot(None)).order_by(Job.discovered_at.desc()).all()
    ranked = sorted((j for j in rows if j.id not in applied and _eligible(j)), key=_consumer_score, reverse=True)

    used: set[str] = set()
    featured, apps = [], []
    for j in ranked:
        company = j.company.strip().lower()
        if company in used or _consumer_score(j) < 3:
            continue
        n = len(j.description or "")
        if len(featured) < n_featured and 1500 <= n <= 14000 and SNAPSHOT_HOSTS.search(j.url) and "embed/job_app" not in j.url:
            featured.append(j)
            used.add(company)
    for j in ranked:
        company = j.company.strip().lower()
        if company in used or _consumer_score(j) < 2:
            continue
        if len(apps) < n_apps and 1200 <= len(j.description or "") <= 9000:
            apps.append(j)
            used.add(company)
    return featured, apps


def _posting(job, featured: bool) -> dict:
    """The serialised row the demo's job collection holds, with the real DB id replaced."""
    from backend.api.routes_jobs import _job_to_dict

    d = _job_to_dict(job)
    new_id = str(uuid.uuid5(NAMESPACE, str(job.id)))
    d.update({
        "id": new_id,
        "external_id": hashlib.sha256(f"demo:{job.id}".encode()).hexdigest()[:40],
        "title": html.unescape(job.title or ""),
        "company": html.unescape(job.company or "").strip(),
        "url": clean_url(job.url),
        "description": sanitize(html.unescape(job.description or "")),
        "search_id": None,
        "cv_scores": {}, "best_cv": None, "best_score": 0, "scoring_report": None,
        "h1b_jd_flag": None, "h1b_jd_snippet": None, "h1b_verdict": None,
        "h1b_company_lca_count": None, "h1b_company_approval_rate": None,
        "has_cached_page": True, "page_cached_at": None,
        "seen": False, "saved": False, "status": "new",
        "tailored_resume_id": None, "has_tailored_resume": False, "in_flight": [], "in_flight_detail": [],
        "short_id": None,
        "_featured": featured,
    })
    return d


def export(out_dir, n_featured_pool: int = 30, n_apps: int = 30, db=None) -> Path:
    """Write <out_dir>/export.json. Read-only: one SELECT-only transaction."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    own = db is None
    if own:
        from sqlalchemy import text
        from backend.accounts.context import unscoped
        from backend.models.db import SessionLocal
        # unscoped(): the demo draws from the install's whole job table (single owner today); read-only.
        ctx = unscoped()
        ctx.__enter__()
        db = SessionLocal()
        db.execute(text("SET TRANSACTION READ ONLY"))
    try:
        featured, apps = select_postings(db, n_featured_pool, n_apps)
        payload = {
            "reference_date": date.today().isoformat(),
            "featured_pool": [_posting(j, True) for j in featured],
            "apps": [_posting(j, False) for j in apps],
        }
    finally:
        if own:
            db.rollback()
            db.close()
            ctx.__exit__(None, None, None)
    path = out / "export.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("--featured-pool", type=int, default=30)
    ap.add_argument("--apps", type=int, default=30)
    a = ap.parse_args(argv)
    p = export(a.out_dir, a.featured_pool, a.apps)
    data = json.loads(p.read_text(encoding="utf-8"))
    print(f"wrote {p}: {len(data['featured_pool'])} featured candidates, {len(data['apps'])} applications")
    return 0


if __name__ == "__main__":
    sys.exit(main())
