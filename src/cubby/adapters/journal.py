"""Append-only move journal so a sort run can be undone.

Each run is one JSON line: a timestamp and the list of ``{from, to}`` moves it
made. ``cubby undo`` replays the most recent line in reverse.

This is the only way back from an automated, destructive operation, so it is
built to survive the crash it exists for:

- a run interrupted mid-append leaves a partial last line, which is skipped
  rather than crashing the read;
- rewriting the file (dropping a consumed run) is staged and swapped, so an
  interrupted drop leaves the whole journal intact;
- a recording that fails still never aborts the sort (the files have already
  moved by then), but it warns instead of vanishing, because moving files with
  no way back is not something to do quietly.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

DEFAULT_JOURNAL = Path.home() / ".local" / "state" / "cubby" / "journal.jsonl"

Move = tuple[Path, Path]  # (source_before, destination_after)
Warn = Callable[[str], None]


def _ignore(_: str) -> None:
    return None


class Journal:
    def __init__(self, path: Path | None = None):
        # Resolve the default at call time so it can be patched in tests.
        self.path = path or DEFAULT_JOURNAL

    def record_run(self, moves: list[Move], *, warn: Warn = _ignore) -> bool:
        """Append one run to the journal.

        Args:
            moves: The moves the run made, in the order it made them.
            warn: Called with a human-readable message if the run could not be
                recorded. The sort is never aborted: the files have already
                moved, and losing the journal as well as the sort is worse.

        Returns:
            True if the run was recorded and can be undone.
        """
        if not moves:
            return True
        entry = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "moves": [{"from": str(src), "to": str(dst)} for src, dst in moves],
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as exc:
            warn(
                f"could not write the undo journal at {self.path} ({exc}). "
                f"{len(moves)} file(s) were moved and 'cubby undo' will not be "
                "able to put them back."
            )
            return False
        return True

    def _lines(self) -> list[str]:
        try:
            raw = self.path.read_text("utf-8")
        except OSError:
            return []
        return [line for line in raw.splitlines() if line.strip()]

    @staticmethod
    def _parse(line: str) -> list[Move] | None:
        """Return the moves in ``line``, or None if it is damaged."""
        try:
            entry = json.loads(line)
            return [(Path(m["from"]), Path(m["to"])) for m in entry["moves"]]
        except (json.JSONDecodeError, KeyError, TypeError):
            return None

    def last_run(self) -> list[Move] | None:
        """The most recent complete run, or None if there is none.

        A line left half-written by an interrupted run is skipped, so a crash
        during recording costs the run that was in flight and nothing before it.
        """
        for line in reversed(self._lines()):
            moves = self._parse(line)
            if moves is not None:
                return moves
        return None

    def drop_last_run(self) -> None:
        """Remove the most recent complete run, and any damage after it.

        Staged and swapped rather than written in place: an interrupted drop
        must not be able to truncate the journal.

        Raises:
            OSError: The journal could not be rewritten. It is left untouched.
        """
        lines = self._lines()
        for index in range(len(lines) - 1, -1, -1):
            if self._parse(lines[index]) is not None:
                kept = lines[:index]
                break
        else:
            return

        staging = self.path.with_name(self.path.name + ".staging")
        staging.write_text("".join(line + "\n" for line in kept), encoding="utf-8")
        try:
            os.replace(staging, self.path)
        except OSError:
            staging.unlink(missing_ok=True)
            raise
