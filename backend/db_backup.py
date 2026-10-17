"""Whole-database snapshots with pg_dump (install-level, owner only).

One function for the three callers: the scheduled backup (`scheduler.run_db_backup`),
the manual trigger (`POST /api/db/backup`) and the automatic snapshot taken before an
account is removed (`DELETE /api/accounts/{id}`, MT-11).
"""
import glob
import os
import subprocess
from datetime import datetime
from urllib.parse import urlparse

BACKUP_DIR = "/app/backups"
ROTATION_PREFIX = "jobnavigator_"   # the scheduled/manual rotation (last 5 kept)
KEEP = 5                         # newest files kept per prefix (jobnavigator_*, pre_remove_*)

# QA F9 / T9-01: the image's pg_dump is newer than the postgres:16 server, and its header
# line `SET transaction_timeout = 0;` (pg17) is an unknown parameter to a 16 server, so a
# strict `psql -v ON_ERROR_STOP=1` restore aborted on it. It is dropped from the file.
_UNSUPPORTED_ON_16 = (b"SET transaction_timeout = ",)


def filter_dump(src, dst, drop_running_backup_row: bool = True) -> dict:
    """Copy a plain-SQL pg_dump from `src` to `dst` (binary file objects), line by line,
    making it restorable on postgres:16 with psql in strict mode:
    - drops `SET transaction_timeout` (T9-01);
    - drops the `db_backup` job_runs row that is still `running` (the backup being taken
      describes itself; after a restore the next boot would only mark it failed, T9-02).
    Returns {"dropped_settings": n, "dropped_runs": n}."""
    dropped = {"dropped_settings": 0, "dropped_runs": 0}
    cols = None        # (job_type index, status index) while inside COPY public.job_runs
    for line in src:
        if cols is None:
            if line.startswith(_UNSUPPORTED_ON_16):
                dropped["dropped_settings"] += 1
                continue
            if drop_running_backup_row and line.startswith(b"COPY public.job_runs ("):
                names = [c.strip() for c in line[line.index(b"(") + 1:line.index(b")")].decode().split(",")]
                if "job_type" in names and "status" in names:
                    cols = (names.index("job_type"), names.index("status"))
        else:
            if line.startswith(b"\\."):
                cols = None
            else:
                parts = line.rstrip(b"\n").split(b"\t")
                if (len(parts) > max(cols) and parts[cols[0]] == b"db_backup"
                        and parts[cols[1]] == b"running"):
                    dropped["dropped_runs"] += 1
                    continue
        dst.write(line)
    return dropped


def prune(prefix: str, keep: int = KEEP) -> int:
    """Delete all but the newest `keep` `<prefix>*.sql` files (names carry a timestamp, so
    name order is age order). Returns how many were deleted."""
    files = sorted(glob.glob(f"{BACKUP_DIR}/{prefix}*.sql"))
    n = 0
    while len(files) > keep:
        os.remove(files.pop(0))
        n += 1
    return n


def run_pg_dump(prefix: str = ROTATION_PREFIX, suffix: str = "", rotate: bool = True,
                timeout: int = 300) -> dict:
    """Dump the database to `<BACKUP_DIR>/<prefix><timestamp><suffix>.sql`.

    Returns {"path", "file", "size_mb", "pruned"}; raises RuntimeError when pg_dump fails
    (non-zero exit), so a caller that must not continue without a snapshot can abort.
    With `rotate`, only the newest KEEP files with the same prefix are kept (the scheduled
    `jobnavigator_*` rotation and the `pre_remove_*` snapshots each have their own five).
    The file is restorable on postgres:16 (see `filter_dump`)."""
    from backend.config import DATABASE_URL

    os.makedirs(BACKUP_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = f"{BACKUP_DIR}/{prefix}{timestamp}{suffix}.sql"
    raw_file = backup_file + ".raw"

    parsed = urlparse(DATABASE_URL)
    if not parsed.hostname or not parsed.scheme.startswith("postgres"):
        raise RuntimeError("pg_dump needs a PostgreSQL database")
    env = os.environ.copy()
    env["PGPASSWORD"] = parsed.password or ""

    result = subprocess.run(
        ["pg_dump", "-h", parsed.hostname, "-p", str(parsed.port or 5432),
         "-U", parsed.username, "-d", parsed.path.lstrip("/"),
         "-f", raw_file],
        env=env, capture_output=True, text=True, timeout=timeout
    )
    if result.returncode != 0:
        if os.path.exists(raw_file):
            os.remove(raw_file)
        raise RuntimeError(f"pg_dump failed: {result.stderr}")
    try:
        with open(raw_file, "rb") as src, open(backup_file, "wb") as dst:
            filter_dump(src, dst)
    finally:
        os.remove(raw_file)

    pruned = prune(prefix) if rotate else 0

    size_mb = os.path.getsize(backup_file) / (1024 * 1024)
    return {"path": backup_file, "file": os.path.basename(backup_file),
            "size_mb": size_mb, "pruned": pruned}
