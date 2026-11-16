"""What this install may send: `full` (Yes), `essentials`, `off`, or `unset` (not asked yet).

The owner's choice is the install setting `telemetry_level`; the env value JN_TELEMETRY
(full | essentials | limited | off) overrides it and locks the choice in Settings.
"""
import os
import uuid

FULL, ESSENTIALS, OFF, UNSET = "full", "essentials", "off", "unset"
_ENV_VALUES = {"full": FULL, "essentials": ESSENTIALS, "limited": ESSENTIALS,
               **{v: OFF for v in ("off", "0", "false", "no", "none", "disabled")}}
INSTALL_ID_KEY = "telemetry_install_id"
LAST_SENT_KEY = "telemetry_last_sent_at"


def env_level():
    """The level JN_TELEMETRY sets, or None (unset or not a known value)."""
    return _ENV_VALUES.get(os.environ.get("JN_TELEMETRY", "").strip().lower())


def chosen_level(db):
    """The owner's choice: full, essentials or unset."""
    from backend.accounts.settings import get_install_setting
    value = str(get_install_setting(db, "telemetry_level") or "").strip().lower()
    return value if value in (FULL, ESSENTIALS) else UNSET


def effective_level(db) -> str:
    return env_level() or chosen_level(db)


def sends_anything(level: str) -> bool:
    return level in (FULL, ESSENTIALS)


def install_id(db) -> str:
    """A random id for this install, created once (no commit when it already exists)."""
    from backend.accounts.settings import get_install_setting, put_setting
    value = get_install_setting(db, INSTALL_ID_KEY)
    if value:
        return value
    value = str(uuid.uuid4())
    put_setting(db, INSTALL_ID_KEY, value)
    db.commit()
    return value
