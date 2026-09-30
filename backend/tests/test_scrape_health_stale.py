"""The scrape health alert names only a source that ran in the current scrape:
a paused search keeps its last three failed rows forever and must not alert
after every run."""
from datetime import datetime, timedelta, timezone

import pytest

from backend.models.db import ScrapeLog


@pytest.fixture
def sent(monkeypatch):
    """Capture the Telegram alert instead of sending it."""
    import backend.notifier.telegram as tg
    messages = []

    async def fake_send(chat_id, text):
        messages.append(text)
        return True

    monkeypatch.setattr(tg, "_is_enabled", lambda: True, raising=False)
    monkeypatch.setattr(tg, "_get_chat_id", lambda: "123", raising=False)
    monkeypatch.setattr(tg, "_send_message", fake_send, raising=False)
    return messages


def _fail(db, source, when):
    db.add(ScrapeLog(source=source, jobs_found=0, new_jobs=0, error="boom", ran_at=when))
    db.commit()


async def test_a_source_that_did_not_run_this_scrape_does_not_alert(test_db, sent):
    from backend.scheduler import check_scrape_health
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    for i in range(3):
        _fail(test_db, "linkedin_personal", week_ago + timedelta(hours=i))
    await check_scrape_health(since=datetime.now(timezone.utc) - timedelta(minutes=5))
    assert sent == []


async def test_a_source_that_failed_again_this_scrape_alerts(test_db, sent):
    from backend.scheduler import check_scrape_health
    now = datetime.now(timezone.utc)
    _fail(test_db, "indeed", now - timedelta(hours=2))
    _fail(test_db, "indeed", now - timedelta(hours=1))
    _fail(test_db, "indeed", now)
    await check_scrape_health(since=now - timedelta(minutes=5))
    assert len(sent) == 1 and "indeed" in sent[0]


async def test_without_since_every_source_is_checked(test_db, sent):
    from backend.scheduler import check_scrape_health
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    for i in range(3):
        _fail(test_db, "linkedin_personal", week_ago + timedelta(hours=i))
    await check_scrape_health()
    assert len(sent) == 1 and "linkedin_personal" in sent[0]
