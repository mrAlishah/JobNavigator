"""Cross-run fetch reuse: a run whose target was fetched by another run in the last
REUSE_SECONDS (or is being fetched right now) gets that fetch instead of downloading again,
when the earlier fetch covers what it asks for."""
import asyncio
import threading
import time

import pytest

from backend.scraper import targets as T


@pytest.fixture(autouse=True)
def _clean_registry():
    T.reset_shared_fetches()
    yield
    T.reset_shared_fetches()


def test_same_key_within_the_window_reuses_the_fetch():
    a = T.shared_fetch_for(("keyword", "pm"), {"results_wanted": 50, "hours_old": 24})
    calls = []
    assert a.get_sync(lambda: calls.append(1) or ["job"]) == ["job"]
    b = T.shared_fetch_for(("keyword", "pm"), {"results_wanted": 50, "hours_old": 24})
    assert b is a
    assert b.get_sync(lambda: calls.append(1) or ["other"]) == ["job"]
    assert calls == [1]


def test_a_smaller_request_is_covered_a_larger_one_is_not():
    a = T.shared_fetch_for(("keyword", "pm"), {"results_wanted": 100, "hours_old": 72})
    a.get_sync(lambda: ["job"])
    assert T.shared_fetch_for(("keyword", "pm"), {"results_wanted": 50, "hours_old": 24}) is a
    bigger = T.shared_fetch_for(("keyword", "pm"), {"results_wanted": 200, "hours_old": 24})
    assert bigger is not a
    older = T.shared_fetch_for(("keyword", "pm"), {"results_wanted": 50, "hours_old": 168})
    assert older is not a


def test_a_different_key_is_a_different_fetch():
    a = T.shared_fetch_for(("company", (("https://boards.greenhouse.io/acme", "greenhouse"),), 5, ""))
    a.get_sync(lambda: ["job"])
    b = T.shared_fetch_for(("company", (("https://boards.greenhouse.io/other", "greenhouse"),), 5, ""))
    assert b is not a


def test_an_expired_fetch_is_not_reused(monkeypatch):
    a = T.shared_fetch_for(("trulyremote",))
    a.get_sync(lambda: ["job"])
    from types import SimpleNamespace
    later = time.monotonic() + T.REUSE_SECONDS + 1
    monkeypatch.setattr(T, "time", SimpleNamespace(time=time.time, monotonic=lambda: later))
    assert T.shared_fetch_for(("trulyremote",)) is not a


def test_a_throttle_stop_lasts_one_run_but_read_pages_are_kept():
    a = T.shared_fetch_for(("trulyremote",))
    a.get_sync(lambda: ["job"])
    a.memo["trulyremote_halt"] = {"stopped": True}
    a.memo["trulyremote_details"] = {"https://trw.test/1": "page"}
    b = T.shared_fetch_for(("trulyremote",))
    assert b is a
    assert "trulyremote_halt" not in b.memo
    assert b.memo["trulyremote_details"] == {"https://trw.test/1": "page"}


def test_a_failed_or_empty_fetch_is_not_reused_by_a_later_run():
    a = T.shared_fetch_for(("levels_fyi", "u", 50))
    with pytest.raises(RuntimeError):
        a.get_sync(lambda: (_ for _ in ()).throw(RuntimeError("429")))
    assert T.shared_fetch_for(("levels_fyi", "u", 50)) is not a
    b = T.shared_fetch_for(("freehire", ()), {"results_wanted": 10})
    b.get_sync(lambda: ([], {"linkedin": "blocked"}))
    assert T.shared_fetch_for(("freehire", ()), {"results_wanted": 10}) is not b


def test_reuse_hands_out_copies():
    a = T.shared_fetch_for(("trulyremote",))
    first = a.get_sync(lambda: [{"title": "PM"}])
    first[0]["title"] = "changed by one account's ingest"
    again = T.shared_fetch_for(("trulyremote",)).get_sync(lambda: [])
    assert again == [{"title": "PM"}]


@pytest.mark.asyncio
async def test_a_second_run_joins_a_fetch_in_flight():
    gate, calls = asyncio.Event(), []

    async def fetch():
        calls.append(1)
        await gate.wait()
        return ["job"]

    a = T.shared_fetch_for(("levels_fyi", "u", 50))
    first = asyncio.create_task(a.get(fetch))
    await asyncio.sleep(0.01)
    b = T.shared_fetch_for(("levels_fyi", "u", 50))
    assert b is a
    second = asyncio.create_task(b.get(fetch))
    await asyncio.sleep(0.01)
    gate.set()
    assert await first == ["job"] and await second == ["job"]
    assert calls == [1]


def test_sync_fetches_in_two_threads_run_once():
    a = T.shared_fetch_for(("keyword", "pm"), {"results_wanted": 50, "hours_old": 24})
    calls, out = [], []

    def fetch():
        calls.append(1)
        time.sleep(0.1)
        return ["job"]

    threads = [threading.Thread(target=lambda: out.append(a.get_sync(fetch))) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert calls == [1] and out == [["job"], ["job"]]


def test_unshareable_keys_are_never_registered():
    a = T.shared_fetch_for(("apify", "own", "s1"))
    a.get_sync(lambda: ["job"])
    assert T.shared_fetch_for(("apify", "own", "s1")) is not a
