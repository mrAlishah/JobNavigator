"""Every job the scheduler registers shows a readable name in the schedules list, not its id."""
import re
from pathlib import Path

SCHEDULER = Path(__file__).resolve().parents[1] / "scheduler.py"


def _registered_ids():
    src = SCHEDULER.read_text(encoding="utf-8")
    ids = set(re.findall(r'id="([a-z_]+)"', src))
    ids |= set(re.findall(r'_add_cron_job\(\w+, "([a-z_]+)"', src))
    return ids


def test_every_scheduled_job_has_a_display_name():
    from backend.main import SCHEDULE_NAMES
    ids = _registered_ids()
    assert "scrape_all" in ids
    assert sorted(ids - set(SCHEDULE_NAMES)) == []
