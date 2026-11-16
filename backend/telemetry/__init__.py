"""Telemetry to the maintainer's Umami (docs/superpowers/specs/2026-10-26-telemetry-design.md).

level.py decides what may be sent, events.py builds the events from rows the app already writes,
umami.py sends them. Nothing is sent until the owner has chosen Yes (full) or Essentials, and
JN_TELEMETRY=off turns it off for good.
"""
