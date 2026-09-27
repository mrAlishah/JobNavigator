"""GET /settings and PATCH /settings endpoints."""
import json
import logging
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from backend.models.db import get_db, Setting
from backend.scheduler import configure_scheduler
from backend.analyzer.cv_scorer import reset_scoring_semaphore
from backend.scraper._shared.dedup import reload_tracking_params

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings", tags=["settings"])


_REDACT_SUFFIXES = ("_password", "_api_key", "_session_id", "_secret")
_REDACT_KEYS = {"dashboard_api_key", "gmail_refresh_token"}


@router.get("")
def get_settings(db: Session = Depends(get_db)):
    """Return all settings as a key-value map. Sensitive values are redacted."""
    rows = db.query(Setting).all()
    result = {}
    for row in rows:
        # Redact secrets — return empty string if not set, "••••••" if set
        if row.key in _REDACT_KEYS or any(row.key.endswith(s) for s in _REDACT_SUFFIXES):
            result[row.key] = "" if not row.value else "\u2022" * 6
            continue
        try:
            result[row.key] = json.loads(row.value)
        except (json.JSONDecodeError, TypeError):
            result[row.key] = row.value
    return result


@router.patch("")
def update_settings(updates: dict, db: Session = Depends(get_db)):
    """Update one or more settings; only keys the app reads are writable (unknown -> 400 as a group), and values are validated (integers non-negative, `*_cron` a parseable 5-field expression, enums known) since a bad one could crash configure_scheduler() and prevent the backend from starting."""
    from backend.seed import invalid_setting_values, unknown_setting_keys

    unknown = unknown_setting_keys(updates.keys())
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown setting: {', '.join(sorted(unknown))}",
        )

    # The redacted placeholder is never a real value — it is skipped below too.
    checkable = {k: v for k, v in updates.items()
                 if not (isinstance(v, str) and v == "•" * 6)}
    problems = invalid_setting_values(checkable)
    if problems:
        raise HTTPException(
            status_code=400,
            detail="Invalid setting value — " + "; ".join(problems),
        )

    warnings: list[str] = []
    updated = []
    for key, value in updates.items():
        if isinstance(value, str) and value == "\u2022" * 6:
            continue
        setting = db.query(Setting).filter(Setting.key == key).first()
        if setting:
            setting.value = json.dumps(value) if isinstance(value, (list, dict, bool)) else str(value)
            updated.append(key)
        else:
            db.add(Setting(key=key, value=json.dumps(value) if isinstance(value, (list, dict, bool)) else str(value)))
            updated.append(key)
    db.commit()

    def _reconfigure(name: str, fn) -> None:
        """Run one post-update reconfigure; a failure becomes a warning naming the step.

        The exception text stays in the server log: it can carry file paths, connection
        strings and stack detail, and the response is not the place for it.
        """
        try:
            fn()
        except Exception:
            warnings.append(f"{name} failed — see server logs")
            logger.exception("%s failed after settings update", name)

    timing_keys = {
        "scrape_interval_minutes", "email_check_interval_minutes",
        "backup_cron", "digest_cron", "h1b_cron", "cleanup_cron", "reject_cron",
    }
    if timing_keys & set(updated):
        _reconfigure("configure_scheduler", configure_scheduler)

    if "scoring_max_concurrent" in updated:
        _reconfigure("reset_scoring_semaphore", reset_scoring_semaphore)

    if "tailoring_max_concurrent" in updated:
        def _reset_tailoring():
            from backend.api.routes_resumes import reset_tailoring_semaphore
            reset_tailoring_semaphore()
        _reconfigure("reset_tailoring_semaphore", _reset_tailoring)

    if "dedup_tracking_params" in updated:
        _reconfigure("reload_tracking_params", reload_tracking_params)

    return {"updated": updated, "warnings": warnings}


@router.get("/defaults")
def get_defaults():
    """Seeded defaults, so an editor can offer "Reset to default" without hardcoding a second copy of every prompt in the frontend."""
    from backend.seed import DEFAULT_SETTINGS
    return {k: v[0] for k, v in DEFAULT_SETTINGS.items()}


@router.post("/profile-image")
async def upload_profile_image(file: UploadFile = File(...)):
    """Upload a profile image for use in resume templates. Returns optimized base64 data URI."""
    import base64
    from io import BytesIO
    from PIL import Image

    # Validate file type
    allowed_types = {"image/png", "image/jpeg", "image/jpg", "image/gif", "image/webp"}
    if file.content_type not in allowed_types:
        raise HTTPException(status_code=400, detail="Only image files (PNG, JPEG, GIF, WebP) are allowed")

    # Read and validate file size (max 2MB)
    content = await file.read()
    if len(content) > 2 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File size must be less than 2MB")

    try:
        # Open image and optimize
        img = Image.open(BytesIO(content))
        # Convert RGBA to RGB if needed (for JPEG compatibility)
        if img.mode in ('RGBA', 'LA', 'P'):
            bg = Image.new('RGB', img.size, (255, 255, 255))
            if img.mode == 'P':
                img = img.convert('RGBA')
            bg.paste(img, mask=img.split()[-1] if img.mode in ('RGBA', 'LA') else None)
            img = bg
        # Keep the stored image small enough for PDF rendering.
        img.thumbnail((120, 120), Image.Resampling.LANCZOS)
        # Save as JPEG for smaller size
        output = BytesIO()
        img.save(output, format='JPEG', quality=85, optimize=True)
        optimized_content = output.getvalue()
        mime_type = "image/jpeg"
    except Exception as e:
        logger.error(f"Image processing failed: {e}")
        raise HTTPException(status_code=400, detail=f"Failed to process image: {str(e)}")

    # Convert to base64 data URI
    data_uri = f"data:{mime_type};base64,{base64.b64encode(optimized_content).decode()}"

    # Return the data URI that can be stored directly in settings
    return {"profile_image_path": data_uri}
