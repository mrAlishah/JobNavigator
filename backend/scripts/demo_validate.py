"""Validate the demo data before it is published.

    python -m backend.scripts.demo_validate <demo_repo_dir> [--forbid "Name,mail-fragment"]

Reads <dir>/data/data.json, <dir>/data/snapshots/. Prints every problem and exits 1
when there is any. `--forbid` (or $DEMO_FORBID) lists strings that must not appear anywhere in the data
(the owner's name and mail); any e-mail address outside example.com fails regardless.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ALLOWED_STATUSES = {"applied", "interview", "offer", "rejected"}
NEXT = {None: {"applied"}, "applied": {"interview", "rejected"}, "interview": {"offer", "rejected"}, "offer": set(), "rejected": set()}
JOB_KEYS = {"id", "external_id", "company", "title", "url", "source", "description", "location", "cv_scores", "best_cv", "best_score",
            "scoring_report", "has_cached_page", "seen", "saved", "status", "discovered_at", "short_id"}
APP_KEYS = {"id", "job_id", "status", "applied_at", "status_transitions", "updated_at", "company", "title", "url", "best_cv", "interviews"}
RESUME_KEYS = {"id", "name", "is_base", "template", "page_format", "json_data"}
LETTER_KEYS = {"id", "name", "job_id", "template", "json_data"}
REPORT_KEYS = {"summary", "breakdown", "requirement_mapping", "keyword_coverage_pct", "matched_keywords", "missing_keywords", "ats_tip"}
BREAKDOWN = ("skills", "experience", "domain", "role", "requirements")
PERSONA_NODES = ("contact", "work_auth", "demographics", "compensation", "preferences", "resume_content", "qa_bank")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")


def _walk(value):
    if isinstance(value, dict):
        for k, v in value.items():
            yield k
            yield from _walk(v)
    elif isinstance(value, list):
        for v in value:
            yield from _walk(v)
    else:
        yield value


def validate(root: Path, forbid=()) -> list[str]:
    errs: list[str] = []
    err = errs.append
    path = root / "data" / "data.json"
    if not path.exists():
        return [f"missing {path}"]
    raw = path.read_text(encoding="utf-8")
    d = json.loads(raw)
    ref = date.fromisoformat(d["reference_date"])

    # schema
    for coll, keys in (("jobs", JOB_KEYS), ("applications", APP_KEYS), ("resumes", RESUME_KEYS), ("cover_letters", LETTER_KEYS)):
        rows = d.get(coll)
        if not isinstance(rows, list) or not rows:
            err(f"{coll}: not a non-empty list")
            continue
        for r in rows:
            missing = keys - set(r)
            if missing:
                err(f"{coll} {r.get('id')}: missing {sorted(missing)}")
        ids = [r.get("id") for r in rows]
        if len(set(ids)) != len(ids):
            err(f"{coll}: duplicate ids")
    for coll in ("searches", "companies", "accounts", "activity_log", "runs"):
        if not isinstance(d.get(coll), list) or not d[coll]:
            err(f"{coll}: not a non-empty list")
    for n in PERSONA_NODES:
        if n not in d.get("persona", {}):
            err(f"persona: missing node {n}")
    if not d.get("persona", {}).get("qa_bank"):
        err("persona: empty qa_bank")
    if not any(a.get("disabled") for a in d.get("accounts", [])):
        err("accounts: no disabled second account")
    from backend.seed import DEFAULT_SETTINGS
    lost = [k for k in DEFAULT_SETTINGS if k not in d.get("settings", {})]
    if lost:
        err(f"settings: missing DEFAULT_SETTINGS keys {lost[:5]}...")

    jobs = {j["id"]: j for j in d["jobs"]}
    short = [j.get("short_id") for j in d["jobs"]]
    if len(set(short)) != len(short):
        err("jobs: duplicate short_id")
    resumes = {r["id"]: r for r in d["resumes"]}
    bases = [r for r in d["resumes"] if r["is_base"]]
    base_names = {r["name"] for r in bases}
    if len(bases) != 2:
        err(f"resumes: expected 2 base résumés, found {len(bases)}")

    # applications
    apps = d["applications"]
    if len(apps) != 30:
        err(f"applications: expected 30, found {len(apps)}")
    if len({a["job_id"] for a in apps}) != len(apps):
        err("applications: two applications for one job")
    lo, hi = ref - timedelta(days=70), ref + timedelta(days=21)

    def in_window(label, s):
        if not s:
            return
        if not ISO.match(s):
            err(f"{label}: not an ISO datetime: {s!r}")
            return
        day = date.fromisoformat(s[:10])
        if not lo <= day <= hi:
            err(f"{label}: {s} outside 10 weeks before {ref}")

    for a in apps:
        label = f"application {a['id']} ({a['company']})"
        if a["job_id"] not in jobs:
            err(f"{label}: job {a['job_id']} not exported")
            continue
        if jobs[a["job_id"]]["status"] != "applied":
            err(f"{label}: job status is {jobs[a['job_id']]['status']!r}, expected 'applied'")
        if a["status"] not in ALLOWED_STATUSES:
            err(f"{label}: status {a['status']!r} not in {sorted(ALLOWED_STATUSES)}")
        prev, last_at = None, ""
        for t in a["status_transitions"]:
            if t["to"] not in NEXT.get(prev, set()):
                err(f"{label}: invalid transition {prev} -> {t['to']}")
            if t.get("from") != prev:
                err(f"{label}: transition 'from' {t.get('from')!r} != {prev!r}")
            if t["at"] < last_at:
                err(f"{label}: transitions out of order")
            in_window(label + " transition", t["at"])
            prev, last_at = t["to"], t["at"]
        if prev != a["status"]:
            err(f"{label}: status {a['status']} != last transition {prev}")
        in_window(label + " applied_at", a["applied_at"])
        in_window(label + " discovered", a.get("discovered_at"))
        for i in a["interviews"]:
            in_window(label + " interview", i["when_at"])
    for j in d["jobs"]:
        in_window(f"job {j['id']} discovered_at", j["discovered_at"])
    counts = {}
    for a in apps:
        counts[a["status"]] = counts.get(a["status"], 0) + 1
    if not (counts.get("offer") and counts.get("rejected") and counts.get("interview") and counts.get("applied")):
        err(f"applications: stage mix too thin {counts}")

    # featured jobs
    prepared = d.get("prepared", {})
    if len(prepared) != 15:
        err(f"prepared: expected 15 featured jobs, found {len(prepared)}")
    strong = middling = weak = 0
    for jid, p in prepared.items():
        job = jobs.get(jid)
        if not job:
            err(f"prepared {jid}: no such job")
            continue
        label = f"featured {job['company']}"
        if set(p.get("scores", {})) != base_names:
            err(f"{label}: scores for {sorted(p.get('scores', {}))}, expected {sorted(base_names)}")
        for name, entry in p.get("scores", {}).items():
            rep, score = entry["report"], entry["score"]
            if REPORT_KEYS - set(rep):
                err(f"{label}/{name}: report missing {sorted(REPORT_KEYS - set(rep))}")
            bd = rep.get("breakdown", {})
            if set(bd) != set(BREAKDOWN) or sum(bd.values()) != score:
                err(f"{label}/{name}: breakdown {bd} does not sum to {score}")
            if job["cv_scores"].get(name) != score or (job.get("scoring_report") or {}).get(name) != rep:
                err(f"{label}/{name}: job score/report differs from the prepared one")
            if not 5 <= len(rep.get("requirement_mapping", [])) <= 12:
                err(f"{label}/{name}: requirement_mapping size")
            if not rep.get("matched_keywords"):
                err(f"{label}/{name}: no matched keywords")
        best = max(e["score"] for e in p["scores"].values()) if p.get("scores") else 0
        strong += best >= 80
        middling += 60 <= best < 80
        weak += best < 60
        t = p.get("tailored") or {}
        base = resumes.get(t.get("base_id"))
        if not base:
            err(f"{label}: tailored base résumé missing")
        else:
            for be, te in zip(base["json_data"]["experience"], t["json_data"]["experience"]):
                if len(be["bullets"]) != len(te["bullets"]):
                    err(f"{label}: tailored bullet count differs for {be['company']}")
            if base["json_data"]["summary"] == t["json_data"]["summary"]:
                err(f"{label}: tailored summary unchanged")
            if not any(b != x for be, te in zip(base["json_data"]["experience"], t["json_data"]["experience"])
                       for b, x in zip(be["bullets"], te["bullets"])):
                err(f"{label}: tailoring changed no bullet")
        cl = (p.get("cover_letter") or {}).get("json_data") or {}
        if len(cl.get("body_paragraphs", [])) != 3 or not cl.get("greeting") or not cl.get("signature"):
            err(f"{label}: cover letter incomplete")
        af = p.get("autofill") or {}
        if "_default" not in af or len(af) < 3:
            err(f"{label}: autofill needs 2+ answers and _default")
        snap = root / "data" / "snapshots" / f"{jid}.html"
        if p.get("snapshot") is False:   # posting expired before capture: the reader page shows its saved text
            if snap.exists():
                err(f"{label}: marked without snapshot but the file exists")
        elif not snap.exists():
            err(f"{label}: snapshot missing")
        else:
            html = snap.read_text(encoding="utf-8")
            if re.search(r"<script|\son\w+\s*=|<iframe", html, re.I):
                err(f"{label}: snapshot still has script/handler/frame")
            if re.search(r"""(?:src|href)=["']https?://[^"']*\.(?:css|js|png|jpe?g|webp|woff2?|svg)""", html, re.I):
                err(f"{label}: snapshot references an external file")
            if re.search(r"""url\(\s*["']?https?://""", html):
                err(f"{label}: snapshot css references an external url")
    if (strong, middling, weak) == (0, 0, 0) or not (strong >= 3 and middling >= 5 and weak >= 3):
        err(f"featured fit spread {strong}/{middling}/{weak} is not a realistic mix")
    for r in d["resumes"]:
        if not r["is_base"] and r.get("job_id") not in jobs:
            err(f"tailored résumé {r['id']}: job missing")
    for c in d["cover_letters"]:
        if c["job_id"] not in jobs:
            err(f"cover letter {c['id']}: job missing")


    # privacy: owner strings and stray e-mail addresses
    forbid = [s.lower() for s in forbid if s]
    scan = dict(d)
    for k in ("settings", "settings_defaults"):   # default Gmail sender patterns, not personal data
        scan.pop(k, None)
    texts = [json.dumps(scan, ensure_ascii=False)] + [p.read_text(encoding="utf-8", errors="replace") for p in (root / "data" / "snapshots").glob("*.html")]
    for text in texts:
        low = text.lower()
        for s in forbid:
            if s in low:
                err(f"forbidden string {s!r} appears in the data")
        for m in set(EMAIL.findall(text)):
            if not m.lower().endswith("@example.com") and not re.search(r"\.(png|jpe?g|svg|webp|gif)$", m, re.I):
                err(f"e-mail address in the data: {m}")
    return sorted(set(errs))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("demo_repo")
    ap.add_argument("--forbid", default=os.environ.get("DEMO_FORBID", ""))
    a = ap.parse_args(argv)
    errs = validate(Path(a.demo_repo), [s.strip() for s in a.forbid.split(",")])
    for e in errs:
        print("FAIL", e)
    print("demo data OK" if not errs else f"{len(errs)} problem(s)")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
