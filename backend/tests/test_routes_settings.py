"""Tests for PATCH /api/settings reconfig error surfacing."""
import pytest
from io import BytesIO

from PIL import Image


def _seed_first_run(db):
    from backend.models.db import Setting
    db.add(Setting(key="dashboard_api_key", value=""))
    db.commit()


def test_patch_settings_reports_scheduler_reconfig_error(api_client, test_db, monkeypatch):
    """configure_scheduler raises → response body contains warnings with the error."""
    _seed_first_run(test_db)

    def broken_configure():
        raise RuntimeError("scheduler config broken")

    monkeypatch.setattr(
        "backend.api.routes_settings.configure_scheduler",
        broken_configure,
        raising=False,
    )

    resp = api_client.patch("/api/settings", json={"scrape_interval_minutes": "15"})
    assert resp.status_code == 200
    data = resp.json()
    warnings = data.get("warnings") or []
    assert any("scheduler" in str(w).lower() for w in warnings), (
        f"Expected scheduler error in warnings, got: {data}"
    )


def test_patch_settings_no_warnings_on_clean_reconfig(api_client, test_db):
    """When all reconfigs succeed, response should NOT include warnings (or empty list)."""
    _seed_first_run(test_db)
    resp = api_client.patch("/api/settings", json={"scrape_interval_minutes": "15"})
    assert resp.status_code == 200
    data = resp.json()
    warnings = data.get("warnings") or []
    assert warnings == []


def test_profile_image_upload_returns_small_jpeg_data_uri(api_client, test_db):
    _seed_first_run(test_db)
    image = Image.new("RGB", (240, 160), "blue")
    data = BytesIO()
    image.save(data, format="PNG")
    response = api_client.post("/api/settings/profile-image", files={"file": ("photo.png", data.getvalue(), "image/png")})
    assert response.status_code == 200
    assert response.json()["profile_image_path"].startswith("data:image/jpeg;base64,")


def test_profile_image_upload_rejects_non_image(api_client, test_db):
    _seed_first_run(test_db)
    response = api_client.post("/api/settings/profile-image", files={"file": ("x.txt", b"not image", "text/plain")})
    assert response.status_code == 400


def _stored_size(response):
    import base64
    uri = response.json()["profile_image_path"]
    with Image.open(BytesIO(base64.b64decode(uri.split(",", 1)[1]))) as stored:
        return stored.size


def test_profile_image_keeps_up_to_300px_for_the_style_size_option(api_client, test_db):
    _seed_first_run(test_db)
    data = BytesIO()
    Image.new("RGB", (1000, 800), "blue").save(data, format="PNG")
    response = api_client.post("/api/settings/profile-image", files={"file": ("photo.png", data.getvalue(), "image/png")})
    assert response.status_code == 200
    assert _stored_size(response) == (300, 240)


def test_profile_image_accepts_a_photo_over_2mb(api_client, test_db):
    import os
    _seed_first_run(test_db)
    data = BytesIO()
    Image.frombytes("RGB", (1200, 1200), os.urandom(1200 * 1200 * 3)).save(data, format="PNG")      # noise: ~4MB PNG
    assert 2 * 1024 * 1024 < len(data.getvalue()) < 10 * 1024 * 1024
    response = api_client.post("/api/settings/profile-image", files={"file": ("photo.png", data.getvalue(), "image/png")})
    assert response.status_code == 200
    assert _stored_size(response) == (300, 300)


def test_profile_image_rejects_a_file_over_10mb(api_client, test_db):
    _seed_first_run(test_db)
    response = api_client.post("/api/settings/profile-image", files={"file": ("big.png", b"x" * (10 * 1024 * 1024 + 1), "image/png")})
    assert response.status_code == 400
    assert "10MB" in response.json()["detail"]
