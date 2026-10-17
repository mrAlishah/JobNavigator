"""Startup check: does the backend image match the code mounted into it?

`git pull` alone updates ./backend (bind-mounted) but not the image's packages, so
3.0 code can run in a 2.2 image. The check reads the pins from requirements.txt,
compares them with the installed distributions and the image's build stamp, and
/health reports the result.
"""
import uuid

import pytest

from backend import image_check
from backend.image_check import canonical, check, parse_pins
from backend.tests.r4_support import client  # noqa: F401
from backend.version import APP_VERSION


# ── requirements parsing ────────────────────────────────────────────────────

def test_parse_pins_reads_exact_pins_only():
    text = "\n".join([
        "fastapi==0.136.3",
        "uvicorn[standard]==0.34.0",
        "python-multipart==0.0.31  # >=0.0.31 required: CVE note",
        "python-docx==1.2.0  # Word export",
        "# pandas==9.9.9 commented out",
        "",
        "starlette>=1.0.1",
        "lxml>=6.1.0",
        "linkedin-api @ git+https://github.com/vesaias/linkedin-api@988dc61d",
        "Jinja2>=3.1.0",
        "pdfminer.six==20251230",
        "Foo_Bar==1.0 ; python_version >= '3.10'",
    ])
    assert parse_pins(text) == {
        "fastapi": "0.136.3",
        "uvicorn": "0.34.0",
        "python-multipart": "0.0.31",
        "python-docx": "1.2.0",
        "pdfminer-six": "20251230",
        "foo-bar": "1.0",
    }


def test_canonical_names_follow_pep_503():
    assert canonical("Python_Docx") == "python-docx"
    assert canonical("pdfminer.six") == "pdfminer-six"


def test_the_real_requirements_file_pins_the_critical_packages():
    pins = parse_pins(image_check.REQUIREMENTS_FILE.read_text(encoding="utf-8"))
    assert "python-docx" in pins
    assert "python-jobspy" in pins
    assert len(pins) > 10


# ── the check ───────────────────────────────────────────────────────────────

PINS = {"python-docx": "1.2.0", "python-jobspy": "1.3.0", "fastapi": "0.136.3"}


def _installed(overrides=None):
    have = dict(PINS, **(overrides or {}))
    return lambda name: have.get(name)


def test_matching_image_is_not_flagged():
    status = check(PINS, installed=_installed(), can_import=lambda m: True,
                   image_version=APP_VERSION, code_version=APP_VERSION)
    assert status["image_outdated"] is False
    assert status["image_message"] is None
    assert status["image_details"] == []


def test_image_without_a_stamp_is_not_flagged_by_itself():
    status = check(PINS, installed=_installed(), can_import=lambda m: True, image_version=None)
    assert status["image_outdated"] is False


def test_missing_python_docx_is_flagged():
    status = check(PINS, installed=_installed({"python-docx": None}), can_import=lambda m: m != "docx")
    assert status["image_outdated"] is True
    assert status["image_details"] == ["python-docx missing"]   # reported once, not twice
    assert "older than the code" in status["image_message"]
    assert "docker compose pull && docker compose up -d" in status["image_message"]
    assert "docker compose up --build -d" in status["image_message"]


def test_docx_installed_but_not_importable_is_flagged():
    status = check(PINS, installed=_installed(), can_import=lambda m: m != "docx")
    assert status["image_details"] == ["python-docx not importable"]


def test_jobspy_version_mismatch_is_flagged():
    status = check(PINS, installed=_installed({"python-jobspy": "1.1.82"}), can_import=lambda m: True)
    assert status["image_outdated"] is True
    assert status["image_details"] == ["python-jobspy 1.1.82, code needs 1.3.0"]
    assert "python-jobspy 1.1.82, code needs 1.3.0" in status["image_message"]


def test_equal_versions_spelled_differently_match():
    status = check({"numpy": "2.5.0"}, installed=lambda n: "2.5", can_import=lambda m: True)
    assert status["image_outdated"] is False


def test_older_image_stamp_is_flagged_with_both_versions():
    status = check(PINS, installed=_installed(), can_import=lambda m: True,
                   image_version="2.2", code_version="3.0")
    assert status["image_details"] == ["image 2.2, code 3.0"]
    assert status["image_message"].startswith("The backend image is older than the code (image 2.2, code 3.0).")


def test_newer_image_stamp_asks_for_git_pull():
    status = check(PINS, installed=_installed(), can_import=lambda m: True,
                   image_version="3.1", code_version="3.0")
    assert status["image_outdated"] is True
    assert "newer than the code" in status["image_message"]
    assert "git pull" in status["image_message"]


def test_a_22_image_reports_every_difference_in_one_message():
    status = check(PINS, installed=_installed({"python-docx": None, "python-jobspy": "1.1.82"}),
                   can_import=lambda m: False, image_version=None)
    assert status["image_message"] == (
        "The backend image is older than the code (python-docx missing; python-jobspy 1.1.82, "
        "code needs 1.3.0). Run `docker compose pull && docker compose up -d`, or "
        "`docker compose up --build -d`.")


def test_unreadable_requirements_does_not_raise(monkeypatch, tmp_path):
    monkeypatch.setattr(image_check, "REQUIREMENTS_FILE", tmp_path / "nope.txt")
    image_check.image_status.cache_clear()
    try:
        status = image_check.image_status()
        assert "image_outdated" in status
    finally:
        image_check.image_status.cache_clear()


def test_read_image_version(tmp_path):
    stamp = tmp_path / "image-version"
    stamp.write_text("3.0\n", encoding="utf-8")
    assert image_check.read_image_version(stamp) == "3.0"
    assert image_check.read_image_version(tmp_path / "missing") is None


def test_startup_logs_one_error_when_outdated(monkeypatch, caplog):
    bad = check(PINS, installed=_installed({"python-docx": None}), can_import=lambda m: False)
    monkeypatch.setattr(image_check, "image_status", lambda: bad)
    with caplog.at_level("ERROR", logger="jobnavigator.image_check"):
        image_check.log_image_status()
    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert len(errors) == 1 and "older than the code" in errors[0].getMessage()


def test_startup_logs_nothing_when_matching(monkeypatch, caplog):
    good = check(PINS, installed=_installed(), can_import=lambda m: True)
    monkeypatch.setattr(image_check, "image_status", lambda: good)
    with caplog.at_level("ERROR", logger="jobnavigator.image_check"):
        image_check.log_image_status()
    assert not [r for r in caplog.records if r.levelname == "ERROR"]


# ── /health contract ────────────────────────────────────────────────────────

def test_health_keeps_its_fields_and_reports_a_match(api_client, monkeypatch):
    monkeypatch.setattr(image_check, "image_status",
                        lambda: check(PINS, installed=_installed(), can_import=lambda m: True))
    body = api_client.get("/health").json()
    assert body["status"] == "ok" and body["service"] == "JobNavigator"
    assert body["version"] == APP_VERSION
    assert body["image_outdated"] is False
    assert body["image_message"] is None
    assert body["image_details"] == []


def test_health_reports_an_outdated_image(api_client, monkeypatch, test_db):
    from backend.models.db import Setting
    test_db.add(Setting(key="dashboard_api_key", value="sekret"))   # still public with a key set
    test_db.commit()
    bad = check(PINS, installed=_installed({"python-docx": None, "python-jobspy": "1.1.82"}),
                can_import=lambda m: False)
    monkeypatch.setattr(image_check, "image_status", lambda: bad)
    r = api_client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["image_outdated"] is True
    assert body["image_details"] == ["python-docx missing", "python-jobspy 1.1.82, code needs 1.3.0"]
    assert body["image_message"] == bad["image_message"]


# ── Word export in an image without python-docx ─────────────────────────────

def test_docx_export_without_python_docx_is_a_readable_503(client, test_db, monkeypatch):
    from backend.api import routes_resumes
    from backend.models.db import Resume

    def no_docx(*a, **k):
        raise ModuleNotFoundError("No module named 'docx'")
    monkeypatch.setattr(routes_resumes, "_build_docx", no_docx)
    resume = Resume(id=uuid.uuid4(), name="Base CV", is_base=True, template="inter",
                    page_format="letter", json_data={"header": {"name": "Dana"}})
    test_db.add(resume)
    test_db.commit()
    r = client.get(f"/api/resumes/{resume.id}/docx")
    assert r.status_code == 503
    assert r.json()["detail"].startswith("python-docx is missing from this image")
    assert "docker compose pull" in r.json()["detail"]
