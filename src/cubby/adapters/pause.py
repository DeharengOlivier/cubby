"""The pause switch: stop the agent moving files without uninstalling it.

``cubby pause`` writes ``paused.json`` in the state folder and the agent skips
its passes while it is there; ``cubby resume`` removes it. A pause can end on
its own (``--for 2h``). This is the switch to reach for when cubby is doing
something unexpected: it takes effect at the next pass, needs no service
manager, and leaves the journal and the agent untouched.

A pause file that cannot be read counts as a pause (fail closed): when in
doubt, the agent does not move files.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import state

VERSION = 1
#: The longest pause ``--for`` accepts; longer is "until resumed".
MAX_DURATION = 366 * 86400.0
#: Any ``until`` past this (year 3000) is not a time cubby wrote.
_LATEST = 32_503_680_000.0


def pause_path() -> Path:
    return state.state_dir() / "paused.json"


@dataclass(frozen=True)
class Pause:
    since: str  # ISO timestamp, local time
    until: float | None  # epoch seconds when it ends by itself, or None
    damaged: bool = False  # the file exists but could not be read

    def describe(self) -> str:
        if self.damaged:
            return f"paused (unreadable {pause_path().name}; run 'cubby resume' to clear it)"
        if self.until is None:
            return f"paused since {self.since}"
        ends = datetime.fromtimestamp(self.until).isoformat(timespec="seconds")
        return f"paused since {self.since}, until {ends}"

    def to_json(self) -> dict[str, object]:
        return {"since": self.since, "until": self.until, "damaged": self.damaged}


def set_pause(duration: float | None = None, *, now: float | None = None) -> Pause:
    """Pause the agent, for ``duration`` seconds or until resumed.

    Raises:
        OSError: The pause file could not be written; the agent is not paused.
    """
    now = time.time() if now is None else now
    pause = Pause(
        since=datetime.fromtimestamp(now).isoformat(timespec="seconds"),
        until=now + duration if duration is not None else None,
    )
    record = {"v": VERSION, "since": pause.since, "until": pause.until}
    state.replace_text(pause_path(), json.dumps(record) + "\n")
    return pause


def clear_pause() -> bool:
    """Resume. True if a pause was lifted.

    Raises:
        OSError: The pause file exists but could not be removed.
    """
    try:
        pause_path().unlink()
    except FileNotFoundError:
        return False
    return True


_DAMAGED = Pause(since="unknown", until=None, damaged=True)


def current_pause(now: float | None = None) -> Pause | None:
    """The pause in force, if any. An expired pause is no pause."""
    try:
        raw = pause_path().read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError):
        return _DAMAGED
    pause = _parse(raw)
    now = time.time() if now is None else now
    if pause.until is not None and now >= pause.until:
        return None
    return pause


def _parse(raw: str) -> Pause:
    """The pause a file records; anything cubby did not write is a damaged one."""
    try:
        data = state.parse_json(raw)
        since, until = data["since"], data["until"]
    except (ValueError, KeyError, TypeError):  # JSONDecodeError is a ValueError
        return _DAMAGED
    valid_until = until is None or (
        not isinstance(until, bool)
        and isinstance(until, (int, float))
        and math.isfinite(until)
        and until <= _LATEST
    )
    if not isinstance(since, str) or not valid_until:
        return _DAMAGED
    return Pause(since=since, until=None if until is None else float(until))
