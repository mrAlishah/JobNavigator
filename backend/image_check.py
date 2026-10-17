"""Does the backend image match the code mounted into it?

docker-compose.yml bind-mounts ./backend over /app/backend, while the Python
packages come from the prebuilt image. A `git pull` without `docker compose pull`
(or `up --build`) therefore runs new code inside an old image: missing packages
fail only when a feature first imports them, and older ones misbehave quietly.
This module compares the two once at startup:

- every `name==version` pin in the mounted backend/requirements.txt (read at run
  time, so the check can never drift from the pins) against what
  importlib.metadata says is installed;
- python-docx must also import (Word export loads it lazily);
- the release the image was built from (Dockerfile.backend stamps it into
  /opt/jobnavigator/image-version) against the code's APP_VERSION. Images built
  before 3.0 carry no stamp; the package check catches those.

A mismatch is logged as one ERROR at boot and reported on /health. The backend
keeps running: the schema migration and most features work in the old image.
Stdlib only (plus `packaging` when the image has it), so it cannot fail itself.
"""
from __future__ import annotations

import importlib.metadata
import importlib.util
import logging
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Callable, Optional

from backend.version import APP_VERSION

logger = logging.getLogger("jobnavigator.image_check")

REQUIREMENTS_FILE = Path(__file__).with_name("requirements.txt")
IMAGE_VERSION_FILE = Path(os.getenv("JN_IMAGE_VERSION_FILE", "/opt/jobnavigator/image-version"))

# Distributions whose import name is checked too: installed metadata alone does
# not prove the module loads, and these are imported lazily, so a broken one would
# otherwise surface only as a 500 on first use.
REQUIRED_IMPORTS = {"python-docx": "docx"}

FIX_OLDER = "Run `docker compose pull && docker compose up -d`, or `docker compose up --build -d`."
FIX_NEWER = "Run `git pull`, then `docker compose up -d`."

_PIN = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*==\s*([^\s;#]+)")


def canonical(name: str) -> str:
    """PEP 503 name: case-insensitive, runs of -_. are one hyphen."""
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_pins(text: str) -> dict[str, str]:
    """`name==version` lines of a requirements file -> {canonical name: version}.

    Extras (`uvicorn[standard]`), trailing comments and environment markers are
    dropped; ranges (`>=`), URL pins (`pkg @ git+…`), options and blank or comment
    lines are not exact pins and are skipped."""
    pins: dict[str, str] = {}
    for line in text.splitlines():
        m = _PIN.match(line.split("#", 1)[0])
        if m:
            pins[canonical(m.group(1))] = m.group(2)
    return pins


def installed_version(dist: str) -> Optional[str]:
    try:
        return importlib.metadata.version(dist)
    except importlib.metadata.PackageNotFoundError:
        return None


def importable(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def read_image_version(path: Path = IMAGE_VERSION_FILE) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _same(have: str, want: str) -> bool:
    try:
        from packaging.version import Version
        return Version(have) == Version(want)
    except Exception:
        return have.strip() == want.strip()


def _vtuple(v: str) -> tuple:
    return tuple(int(p) if p.isdigit() else 0 for p in re.split(r"[.\-+]", v or "")[:3])


def check(
    pins: dict[str, str],
    installed: Callable[[str], Optional[str]] = installed_version,
    can_import: Callable[[str], bool] = importable,
    image_version: Optional[str] = None,
    code_version: str = APP_VERSION,
) -> dict:
    """Compare pins and versions. Returns the /health fields:
    {image_outdated, image_message, image_details, image_version, code_version}."""
    details: list[str] = []
    for name, want in sorted(pins.items()):
        have = installed(name)
        if have is None:
            details.append(f"{name} missing")
        elif not _same(have, want):
            details.append(f"{name} {have}, code needs {want}")
    for dist, module in REQUIRED_IMPORTS.items():
        missing = f"{canonical(dist)} missing"
        if missing not in details and not can_import(module):
            details.append(f"{canonical(dist)} not importable")

    newer = False
    if image_version and image_version != code_version:
        newer = _vtuple(image_version) > _vtuple(code_version)
        details.append(f"image {image_version}, code {code_version}")

    message = None
    if details:
        if newer:
            message = (f"The backend image is newer than the code ({'; '.join(details)}). {FIX_NEWER}")
        else:
            message = (f"The backend image is older than the code ({'; '.join(details)}). {FIX_OLDER}")
    return {
        "image_outdated": bool(details),
        "image_message": message,
        "image_details": details,
        "image_version": image_version,
        "code_version": code_version,
    }


@lru_cache(maxsize=1)
def image_status() -> dict:
    """The check for this process, computed once (the image cannot change under a
    running container). Never raises: an unreadable requirements file means
    "nothing to compare", not a crash at boot or a 500 on /health."""
    try:
        pins = parse_pins(REQUIREMENTS_FILE.read_text(encoding="utf-8"))
    except OSError as error:
        logger.warning("Image check skipped: cannot read %s (%s)", REQUIREMENTS_FILE, error)
        pins = {}
    try:
        return check(pins, image_version=read_image_version())
    except Exception as error:  # the check must never take the backend down
        logger.warning("Image check failed: %s", error)
        return check({}, image_version=None)


def log_image_status() -> dict:
    """Startup hook: one ERROR line when the image does not match the code."""
    status = image_status()
    if status["image_outdated"]:
        logger.error(status["image_message"])
    return status
