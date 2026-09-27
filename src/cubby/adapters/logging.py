"""The activity log: one JSON object per line, bounded in size.

Lines carry a timestamp, a level and a message, as JSON so they can be filtered
(``jq 'select(.level != "INFO")'``) and read back by ``cubby status``. The file
is read after something has gone wrong, and the one line that matters ("your
files moved and the undo journal could not be written") must be findable among
the hundreds saying a file was filed.

Logging must never break a sort, but it must not fail silently either: the
first time the file cannot be written, the reason goes to stderr once.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Protocol

from . import state

#: Past this size the log is rotated to ``cubby.log.1`` (one generation kept).
MAX_BYTES = 1_000_000


class LevelLogger(Protocol):
    """A logger that can mark a line as more than routine.

    The level has a default, so a LevelLogger also satisfies the plain
    ``Callable[[str], None]`` that the use cases ask for: they log events, and
    deciding what is worth shouting about is the caller's business.
    """

    def __call__(self, message: str, *, level: str = "INFO") -> None: ...


def human_line(record: dict[str, str]) -> str:
    """A log record as a person reads it."""
    return f"{record.get('ts', '?')}  {record.get('level', '?'):<7} {record.get('msg', '')}"


def _rotate(path: Path) -> None:
    try:
        if path.stat().st_size > MAX_BYTES:
            path.replace(path.with_name(path.name + ".1"))
    except FileNotFoundError:
        return


def file_logger(path: Path | None = None, *, echo: bool = False) -> LevelLogger:
    """Return a logger appending to ``path`` (the platform log when omitted).

    The default is resolved when the logger is built, not when this module is
    imported, so a test or ``CUBBY_STATE_DIR`` can redirect it.
    """
    destination = path or state.log_path()
    reported = False

    def log(message: str, *, level: str = "INFO") -> None:
        nonlocal reported
        record = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "level": level,
            "msg": message,
        }
        if echo:
            print(human_line(record))
        try:
            _rotate(destination)
            state.append_line(destination, json.dumps(record, ensure_ascii=False))
        except OSError as exc:
            if not reported:
                reported = True
                print(f"cubby: warning: cannot write the log {destination}: {exc}", file=sys.stderr)

    return log


def read_tail(path: Path | None = None, limit: int = 5) -> list[dict[str, str]]:
    """The last ``limit`` records of the log; lines that are not JSON are kept as text.

    Raises:
        OSError: The log exists but cannot be read.
    """
    records: list[dict[str, str]] = []
    for line in state.read_lines(path or state.log_path())[-limit:]:
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            data = None
        records.append(data if isinstance(data, dict) else {"msg": line})
    return records
