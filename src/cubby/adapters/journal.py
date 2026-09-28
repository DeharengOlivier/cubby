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
    """A sortable, unique identifier for one sort run.

    64 random bits after the second: two runs that share an id share a journal
    entry list, and undoing one would revert both. 32 bits collided in CI
    (2 of 5 000 ids drawn in one second, a 0.3% chance by the birthday bound).
    """
    return f"{datetime.now():%Y%m%dT%H%M%S}-{secrets.token_hex(8)}"


@dataclass(frozen=True)
class Entry:
    """One recorded move of a run."""

    run: str
    seq: int
    op: MoveOp
    source: Path  # where the file was before the run
    destination: Path  # where the run put it (or the kept copy, for dedupe)


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
        if size < self._compacted_size:
            self._compacted_size = 0  # rewritten or removed since: start afresh
        if size <= max(MAX_BYTES, 2 * self._compacted_size):
            return
        lines = state.read_lines(self.path)
        tallies = _tally(lines)
        # Recent runs stay for history; a run with anything left to undo stays
        # whatever its age, because dropping it would take away its way back.
        keep = set(list(tallies)[-KEEP_RUNS:]) | {run for run, (_, left) in tallies.items() if left}
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
        return _build(state.read_lines(self.path), wanted=None)

    def run(self, run_id: str) -> Run | None:
        """One run, building the entries of that run only.

        Raises:
            OSError: The journal exists but cannot be read.
        """
        found = _build(state.read_lines(self.path), wanted=run_id)
        return found[0] if found else None

    def last_pending_run(self) -> Run | None:
        """The most recent run with something left to undo.

        Raises:
            OSError: The journal exists but cannot be read.
        """
        groups = _group(state.read_lines(self.path))
        for run_id in reversed(groups):
            if _count(groups[run_id])[1]:
                return _run_from(run_id, groups[run_id])
        return None

    def tallies(self) -> dict[str, tuple[int, int]]:
        """Each run's number of moves and of moves still to undo, oldest run first.

        What ``cubby history`` needs, without building a single entry.

        Raises:
            OSError: The journal exists but cannot be read.
        """
        return _tally(state.read_lines(self.path))


def _v1_run_id(line: str) -> str:
    """A version 1 run's id: derived from its content, so it survives compaction."""
    return "v1-" + hashlib.sha256(line.encode("utf-8")).hexdigest()[:12]


def _group(lines: list[str], wanted: str | None = None) -> dict[str, list[_Fields]]:
    """The validated fields of ``lines`` per run, in order; only ``wanted`` if given."""
    groups: dict[str, list[_Fields]] = {}
    for line in lines:
        for fields in _fields(line):
            if wanted is None or fields[0] == wanted:
                groups.setdefault(fields[0], []).append(fields)
    return groups


def _count(fields: list[_Fields]) -> tuple[int, int]:
    """A run's moves, and those not settled yet (see ``Run.pending``)."""
    moves = [seq for _, seq, op, _, _ in fields if op in ("move", "dedupe")]
    settled = {seq for _, seq, op, _, _ in fields if op not in ("move", "dedupe")}
    return len(moves), sum(1 for seq in moves if seq not in settled)


def _run_from(run_id: str, fields: list[_Fields]) -> Run | None:
    """The run the fields describe, or None if it has no move."""
    run = Run(run_id)
    for _, seq, op, source, destination in fields:
        if op == "move" or op == "dedupe":  # noqa: PLR1714 - `in` does not narrow for mypy
            run.entries.append(Entry(run_id, seq, op, Path(source), Path(destination)))
        else:
            run.settled[seq] = op
    return run if run.entries else None


def _build(lines: list[str], wanted: str | None) -> list[Run]:
    """The runs of ``lines`` with moves, oldest first; only ``wanted`` if given."""
    runs = (_run_from(run_id, fields) for run_id, fields in _group(lines, wanted).items())
    return [run for run in runs if run is not None]


def _tally(lines: list[str]) -> dict[str, tuple[int, int]]:
    """Moves and pending moves per run with moves, oldest first."""
    counts = {run_id: _count(fields) for run_id, fields in _group(lines).items()}
    return {run_id: count for run_id, count in counts.items() if count[0]}


def _run_of(line: str) -> str | None:
    """The run a line belongs to, keyed as the reads key it (see :func:`_fields`)."""
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(record, dict):
        return None
    if "moves" in record and "v" not in record:
        return _v1_run_id(line)
    # The reads take str() of any id, so a run read as "7" must be kept as "7":
    # keeping string ids only dropped such a run with its moves still to undo.
    return str(record["run"]) if "run" in record else None


def _seq(value: object) -> int:
    """A sequence number as written by cubby: a non-negative int, nothing else.

    Raises:
        ValueError: Anything else (a float, a bool, a string, a huge exponent).
    """
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"not a sequence number: {value!r}")
    return value


#: One line's worth of journal, validated but not yet turned into objects:
#: ``(run, seq, op, source, destination)``, the two paths empty for a settlement.
_Fields = tuple[str, int, "MoveOp | Resolution", str, str]


def _fields(line: str) -> Iterator[_Fields]:
    """Yield the moves or settlements one line holds, as validated raw fields.

    Anything malformed yields nothing: a damaged line costs that line only. A
    version 1 line stops at its first malformed move. Building ``Path`` objects
    is most of the cost of reading, so it is left to :func:`_run_from`.
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
                yield run_id, seq, "move", _path(move["from"]), _path(move["to"])
            return
        if record.get("v") != VERSION:
            return
        op = record["op"]
        if op in ("move", "dedupe"):
            yield (
                str(record["run"]),
                _seq(record["seq"]),
                op,
                _path(record["from"]),
                _path(record["to"]),
            )
        elif op in ("restored", "gone"):
            yield str(record["run"]), _seq(record["seq"]), op, "", ""
    except (KeyError, TypeError, ValueError):
        return


def _path(value: object) -> str:
    """A path as written by cubby: a string.

    Raises:
        TypeError: Anything else, which ``Path()`` would refuse too.
    """
    if not isinstance(value, str):
        raise TypeError(f"not a path: {value!r}")
    return value
