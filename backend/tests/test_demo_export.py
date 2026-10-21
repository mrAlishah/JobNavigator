"""Demo export: posting selection, sanitising and the export file."""
import json
import re
import uuid

from backend.models.db import Application, Job
from backend.scripts import demo_export as de

DESC = ("Own the growth roadmap for a consumer mobile app used by millions of users. "
        "You will run experimentation on onboarding and retention, and improve engagement. " * 14)


def _job(i, title="Senior Product Manager, Growth", company=None, url=None, **kw):
    return Job(id=uuid.uuid4(), external_id=f"ext-{i}", title=title, company=company or f"Co{i}",
               url=url or f"https://boards.greenhouse.io/co{i}/jobs/{i}", description=kw.pop("description", DESC),
               **{"location": "Remote, US", "loc_country": "US", **kw})


def test_sanitize_strips_name_email_phone():
    out = de.sanitize("Contact Jane Doe at jane@x.com, +1 555 123 4567 for questions.")
    assert "Jane" not in out and "Doe" not in out
    assert "@" not in out and "555" not in out
    assert "recruiting team" in out


def test_sanitize_recruiter_label_and_plain_text():
    assert "Maria Lopez" not in de.sanitize("Your recruiter: Maria Lopez will reach out.")
    plain = "We build a mobile app for millions of users."
    assert de.sanitize(plain) == plain


def test_clean_url_drops_tracking():
    assert de.clean_url("https://boards.greenhouse.io/a/jobs/1?gh_src=x&utm_source=y") == "https://boards.greenhouse.io/a/jobs/1"


def test_select_postings_counts_and_filters(test_db):
    for i in range(50):
        test_db.add(_job(i))
    test_db.add(_job(100, title="Technical Program Manager"))                      # excluded title
    test_db.add(_job(101, title="Product Manager", location="Berlin, Germany"))
    applied = _job(102)
    test_db.add(applied)
    test_db.flush()
    test_db.add(Application(job_id=applied.id, status="applied"))
    test_db.commit()
    featured, apps = de.select_postings(test_db, 15, 30)
    assert len(featured) == 15 and len(apps) == 30
    ids = {j.id for j in featured} | {j.id for j in apps}
    assert len(ids) == 45
    assert len({j.company.lower() for j in featured + apps}) == 45
    assert applied.id not in ids
    assert all(re.search(r"product manager", j.title, re.I) for j in featured + apps)
    assert all("greenhouse" in j.url for j in featured)


def test_export_writes_valid_json(test_db, tmp_path):
    for i in range(6):
        test_db.add(_job(i, description=DESC + " Contact Jane Doe at jane@x.com."))
    test_db.commit()
    path = de.export(tmp_path, n_featured_pool=3, n_apps=3, db=test_db)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", data["reference_date"])
    assert len(data["featured_pool"]) == 3 and len(data["apps"]) == 3
    blob = json.dumps(data)
    assert "jane@x.com" not in blob and "Jane Doe" not in blob


def test_inline_snapshot_is_offline_and_clean():
    from backend.scripts import demo_snapshot as ds
    html = ('<html><head><link rel="stylesheet" href="/a.css"><script>evil()</script>'
            '<link rel="preload" href="/x.js"></head><body onload="x()">'
            '<img src="/p.png" srcset="/p2.png 2x"><img src="/missing.png">'
            '<iframe src="https://x.test"></iframe><a href="/apply">Apply</a>'
            '<p>Contact Jane Doe at jane@x.com</p><div id="onetrust-banner">cookies</div></body></html>')
    res = {"https://j.test/a.css": ("text/css", b'body{background:url("/bg.png")} .f{src:url(/f.woff2)}'),
           "https://j.test/bg.png": ("image/png", b"PNG"), "https://j.test/p.png": ("image/png", b"PNG")}
    out = ds.inline_snapshot(html, "https://j.test/jobs/1", res)
    assert "<script" not in out and "<iframe" not in out and "onload" not in out and "onetrust" not in out
    assert "jane@x.com" not in out and "Jane Doe" not in out
    assert "data:image/png;base64," in out and "/p2.png" not in out
    assert 'href="https://j.test/apply"' in out
    assert "Content-Security-Policy" in out
    assert not re.search(r'(src|href)="https?://j\.test/(p|bg|a)\.', out)
