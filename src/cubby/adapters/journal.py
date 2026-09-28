"""Append-only move journal, so that every move cubby makes can be undone.

This is the only way back from an automated operation on the user's files, so
it is built to survive the crash it exists for.

Format (version 2), one JSON object per line, appended as each move happens::

    {"v": 2, "run": "<id>", "seq": 0, "op": "move",   "from": "...", "to": "..."}
    {"v": 2, "run": "<id>", "seq": 1, "op": "dedupe", "from": "...", "to": "..."}
    {"v": 2, "run": "<id>", "seq": 0, "op": "restored"}
    {"v": 2, "run": "<id>", "seq": 1, "op": "gone"}

- A move is recorded right after it happens, not at the end of the run, so a
  run that fails or is killed part way still leaves every completed move
  undoable.
- ``dedupe`` records a duplicate that was deleted because a byte-identical copy
  already sat at ``to``; undoing it copies ``to`` back to ``from``.
- Undo never rewrites history: it appends ``restored`` (put back) or ``gone``
  (nothing left to put back) for each entry it settles. An entry that failed to
  restore stays pending, so the next ``cubby undo`` retries it.
- Version 1 lines (``{"ts": ..., "moves": [...]}``, one line per run, written
  by cubby 0.1) are still read, as runs of plain moves.
- A damaged line (a crash mid-append) is skipped, never fatal.
- Past :data:`MAX_BYTES` the oldest runs that have nothing left to undo are
  dropped (the :data:`KEEP_RUNS` most recent always stay). What can still be
  undone is never dropped, so the file is bounded by the moves not yet undone.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from . import state

VERSION = 2
MAX_BYTES = 5_000_000
KEEP_RUNS = 200

MoveOp = Literal["move", "dedupe"]
Resolution = Literal["restored", "gone"]
Warn = Callable[[str], None]


def default_journal_path() -> Path:
    return state.state_dir() / "journal.jsonl"


def new_run_id() -> str:
    """A sortable, unique identifier for one sort run."""
    return f"{datetime.now():%Y%m%dT%H%M%S}-{secrets.token_hex(4)}"


@dataclass(frozen=True)
class Entry:
    """One recorded move of a run."""

    run: str
    seq: int
    op: MoveOp
    source: Path  # where the file was before the run
    destination: Path  # where the run put it (or the kept copy, for dedupe)


@dataclass(frozen=True)
class Settlement:
    """An undo's verdict on one entry."""

    run: str
    seq: int
    resolution: Resolution


@dataclass
class Run:
    """A run as read back from the journal."""

    run_id: str
    entries: list[Entry] = field(default_factory=list)
    settled: dict[int, Resolution] = field(default_factory=dict)

    @property
    def pending(self) -> list[Entry]:
        """Entries not yet restored or given up on, in the order they happened."""
        return [e for e in self.entries if e.seq not in self.settled]


def _ignore(_: str) -> None:
    return None


class Journal:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_journal_path()
        # Size after the last compaction. What compaction keeps is still
        # undoable, so it can stay above MAX_BYTES; trying again before the file
        # has doubled would re-read it every pass to drop nothing.
        self._compacted_size = 0

    # --- writing -------------------------------------------------------------

    def _append(self, record: dict[str, Any]) -> None:
        state.append_line(self.path, json.dumps({"v": VERSION, **record}))

    def record(self, entry: Entry) -> None:
        """Append one move, right after it happened.

        Raises:
            OSError: The journal could not be written.
        """
        self._append(
            {
                "run": entry.run,
                "seq": entry.seq,
                "op": entry.op,
                "from": str(entry.source),
                "to": str(entry.destination),
            }
        )

    def settle(self, entry: Entry, resolution: Resolution) -> None:
        """Mark ``entry`` as dealt with by an undo.

        Raises:
            OSError: The journal could not be written.
        """
        self._append({"run": entry.run, "seq": entry.seq, "op": resolution})

    def compact(self) -> None:
        """Drop the oldest runs once the file passes :data:`MAX_BYTES`.

        When what remains is still above the limit, the next attempt waits until
        the file has doubled, so a long-lived agent pays for compaction in
        proportion to what it writes, not once per pass.

        Raises:
            OSError: The journal could not be rewritten. It is left untouched.
        """
        try:
            size = self.path.stat().st_size
        except FileNotFoundError:
            return
        if size <= max(MAX_BYTES, 2 * self._compacted_size):
            return
        runs = self.runs()
        # Recent runs stay for history; a run with anything left to undo stays
        # whatever its age, because dropping it would take away its way back.
        keep = {r.run_id for r in runs[-KEEP_RUNS:]} | {r.run_id for r in runs if r.pending}
        lines = state.read_lines(self.path)
        kept = [line for line in lines if _run_of(line) in keep]
        if len(kept) < len(lines):
            state.replace_text(self.path, "".join(line + "\n" for line in kept))
        self._compacted_size = self.path.stat().st_size

    # --- reading -------------------------------------------------------------

    def runs(self) -> list[Run]:
        """Every run in the journal, oldest first.

        Raises:
            OSError: The journal exists but cannot be read.
        """
        runs: dict[str, Run] = {}
        for line in state.read_lines(self.path):
            for item in _parse(line):
                run = runs.setdefault(item.run, Run(item.run))
                if isinstance(item, Entry):
                    run.entries.append(item)
                else:
                    run.settled[item.seq] = item.resolution
        return [run for run in runs.values() if run.entries]

    def run(self, run_id: str) -> Run | None:
        return next((r for r in self.runs() if r.run_id == run_id), None)

    def last_pending_run(self) -> Run | None:
        """The most recent run with something left to undo."""
        return next((r for r in reversed(self.runs()) if r.pending), None)


def _v1_run_id(line: str) -> str:
    """A version 1 run's id: derived from its content, so it survives compaction."""
    return "v1-" + hashlib.sha256(line.encode("utf-8")).hexdigest()[:12]


def _run_of(line: str) -> str | None:
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(record, dict):
        return None
    if "moves" in record and "v" not in record:
        return _v1_run_id(line)
    run = record.get("run")
    return run if isinstance(run, str) else None


def _seq(value: object) -> int:
    """A sequence number as written by cubby: a non-negative int, nothing else.

    Raises:
        ValueError: Anything else (a float, a bool, a string, a huge exponent).
    """
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"not a sequence number: {value!r}")
    return value


def _parse(line: str) -> Iterator[Entry | Settlement]:
    """Yield the entries or settlements one line holds.

    Anything malformed yields nothing: a damaged line costs that line only.
    """
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return
    if not isinstance(record, dict):
        return
    try:
        if "moves" in record and "v" not in record:  # version 1: one line per run
            run_id = _v1_run_id(line)
            for seq, move in enumerate(record["moves"]):
                yield Entry(run_id, seq, "move", Path(move["from"]), Path(move["to"]))
            return
        if record.get("v") != VERSION:
            return
        op = record["op"]
        if op in ("move", "dedupe"):
            yield Entry(
                str(record["run"]),
                _seq(record["seq"]),
                op,
                Path(record["from"]),
                Path(record["to"]),
            )
        elif op in ("restored", "gone"):
            yield Settlement(str(record["run"]), _seq(record["seq"]), op)
    except (KeyError, TypeError, ValueError):
        return
