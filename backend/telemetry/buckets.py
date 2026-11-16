"""Counts and durations as coarse buckets: telemetry never sends an exact volume."""


def bucket(n) -> str:
    n = int(n or 0)
    if n <= 0:
        return "0"
    if n == 1:
        return "1"
    if n <= 5:
        return "2_5"
    if n <= 20:
        return "6_20"
    if n <= 100:
        return "21_100"
    return "101_plus"


def accounts_bucket(n) -> str:
    n = int(n or 0)
    return str(max(n, 1)) if n <= 5 else "6_plus"


_DURATIONS = ((1, "lt_1s"), (5, "1_5s"), (15, "5_15s"), (30, "15_30s"), (60, "30_60s"),
              (120, "1_2m"), (300, "2_5m"), (600, "5_10m"))


def duration_bucket(seconds) -> str:
    if seconds is None:
        return "unknown"
    for limit, name in _DURATIONS:
        if seconds < limit:
            return name
    return "10m_plus"


def interval_bucket(minutes) -> str:
    m = int(minutes or 0)
    if m <= 0:
        return "off"
    if m < 60:
        return "lt_1h"
    if m < 360:
        return "1_6h"
    if m < 1440:
        return "6_24h"
    return "daily_plus"


def tokens_bucket(n) -> str:
    n = int(n or 0)
    if n <= 0:
        return "0"
    for limit, name in ((1_000, "lt_1k"), (10_000, "1_10k"), (100_000, "10_100k"), (1_000_000, "100k_1m")):
        if n < limit:
            return name
    return "1m_plus"


def median(values):
    vals = sorted(v for v in values if v is not None)
    return vals[len(vals) // 2] if vals else None
