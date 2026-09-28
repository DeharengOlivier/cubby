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
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import state

VERSION = 1


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


def current_pause(now: float | None = None) -> Pause | None:
    """The pause in force, if any. An expired pause is no pause."""
    path = pause_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        return Pause(since="unknown", until=None, damaged=True)
    try:
        data = json.loads(raw)
        since = str(data["since"])
        until = data["until"]
        if until is not None and (isinstance(until, bool) or not isinstance(until, (int, float))):
            raise TypeError("until")
    except (json.JSONDecodeError, KeyError, TypeError):
        return Pause(since="unknown", until=None, damaged=True)
    now = time.time() if now is None else now
    if until is not None and now >= until:
        return None
    return Pause(since=since, until=None if until is None else float(until))
