"""The activity log: one JSON object per line, bounded in size.

Lines carry a timestamp, a level, a message, the cubby version and, inside a
pass, the run id, as JSON so they can be filtered
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
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol, get_args

from .. import __version__
from . import state

#: How much a log line matters. Typed, so a level cubby would later read back
#: as foreign text (see :func:`_parse`) cannot be written in the first place.
Level = Literal["INFO", "WARNING", "ERROR"]

#: The run a log line belongs to, so it can be matched with the ledger and the
#: journal (``jq 'select(.run == "...")'``). Set by :func:`run_context`.
_current_run: ContextVar[str | None] = ContextVar("cubby_run", default=None)


@contextmanager
def run_context(run_id: str) -> Iterator[None]:
    """Tag every log line written inside the block with ``run_id``."""
    token = _current_run.set(run_id)
    try:
        yield
    finally:
        _current_run.reset(token)


#: Past this size the log is rotated to ``cubby.log.1`` (one generation kept).
MAX_BYTES = 1_000_000


class LevelLogger(Protocol):
    """A logger that can mark a line as more than routine.

    The level has a default, so a LevelLogger also satisfies the plain
    ``Callable[[str], None]`` that the use cases ask for: they log events, and
    deciding what is worth shouting about is the caller's business.
    """

    def __call__(self, message: str, *, level: Level = "INFO") -> None: ...


def human_line(record: dict[str, str]) -> str:
    """A log record as a person reads it."""
    ts, level, msg = (record.get(key) for key in ("ts", "level", "msg"))
    return f"{ts or '?'}  {level or '?'!s:<7} {'' if msg is None else msg}"


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
    echoing = echo

    def log(message: str, *, level: Level = "INFO") -> None:
        nonlocal reported, echoing
        record = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "level": level,
            "msg": message,
            "version": __version__,
        }
        if (run := _current_run.get()) is not None:
            record["run"] = run
        if echoing:
            try:
                print(human_line(record), flush=True)
            except OSError:
                echoing = False  # stdout closed (`cubby run -v | head`): the file still gets it
        try:
            _rotate(destination)
            state.append_line(destination, json.dumps(record))
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
    return [_parse(line) for line in state.read_lines(path or state.log_path())[-limit:]]


def read_all(path: Path | None = None) -> list[dict[str, str]]:
    """Every record kept, oldest first: the rotated file, then the current one.

    Both are capped at :data:`MAX_BYTES`, so this reads at most twice that.

    Raises:
        OSError: A log file exists but cannot be read.
    """
    current = path or state.log_path()
    rotated = current.with_name(current.name + ".1")
    # The current file first: a rotation between the two reads then shows its
    # lines twice, where the other order would lose them.
    newer = state.read_lines(current)
    return [_parse(line) for line in state.read_lines(rotated) + newer]


#: The levels cubby writes; a record with another is not one of cubby's.
LEVELS: tuple[Level, ...] = get_args(Level)


def _parse(line: str) -> dict[str, str]:
    """A log line as a record.

    The service manager appends the agent's own output to the same file, so a
    line can be anything. Only cubby's records (a string ``msg`` and a known
    level) are taken as records; any other line is kept whole, as text.
    """
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict) and isinstance(data.get("msg"), str) and data.get("level") in LEVELS:
        return data
    return {"msg": line}
