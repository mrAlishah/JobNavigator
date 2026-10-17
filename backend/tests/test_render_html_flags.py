"""The editor stores per-resume flags (profile_image_enabled, footer_enabled) inside json_data.
_render_html also passes profile_image_enabled explicitly, so a json_data copy of it used to
collide ("got multiple values for keyword argument") and the PDF/preview 500'd."""
from pathlib import Path

import pytest

from backend.api.routes_resumes import _discover_templates, _render_html

TEMPLATES = sorted(p.name for p in (Path(__file__).resolve().parents[1] / "resume_templates").iterdir() if p.is_dir())
DATA = {"header": {"name": "Dana", "contact_items": []}, "summary": "s", "experience": [],
        "skills": {}, "education": [], "projects": [], "publications": []}


@pytest.mark.parametrize("template", TEMPLATES)
@pytest.mark.parametrize("flag", [True, False])
def test_render_survives_flags_stored_in_json_data(template, flag):
    data = {**DATA, "profile_image_enabled": flag, "footer_enabled": flag}
    html = _render_html(data, template, "a4", profile_image_path="data:image/png;base64,AAAA",
                        profile_image_enabled=flag)
    assert "Dana" in html


def test_template_list_identifies_image_capability():
    templates = {item["id"]: item for item in _discover_templates()}
    assert templates["professional-top-left-image"]["has_profile_image"] is True
    assert templates["professional-top-right-image"]["has_profile_image"] is True
    assert templates["helvetica"]["has_profile_image"] is False
