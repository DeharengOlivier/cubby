"""Append-only move journal so a sort run can be undone.

Each run is one JSON line: a timestamp and the list of ``{from, to}`` moves it
made. ``cubby undo`` replays the most recent line in reverse. The journal lives
under the user state directory and is best-effort: a failure to record never
aborts a sort.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

DEFAULT_JOURNAL = Path.home() / ".local" / "state" / "cubby" / "journal.jsonl"

Move = tuple[Path, Path]  # (source_before, destination_after)


class Journal:
    def __init__(self, path: Path | None = None):
        # Resolve the default at call time so it can be patched in tests.
        self.path = path or DEFAULT_JOURNAL

    def record_run(self, moves: list[Move]) -> None:
        if not moves:
            return
        entry = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "moves": [{"from": str(src), "to": str(dst)} for src, dst in moves],
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def _lines(self) -> list[str]:
        if not self.path.exists():
            return []
        return [ln for ln in self.path.read_text("utf-8").splitlines() if ln.strip()]

    def last_run(self) -> list[Move] | None:
        lines = self._lines()
        if not lines:
            return None
        entry = json.loads(lines[-1])
        return [(Path(m["from"]), Path(m["to"])) for m in entry["moves"]]

    def drop_last_run(self) -> None:
        lines = self._lines()
        if not lines:
            return
        self.path.write_text("".join(ln + "\n" for ln in lines[:-1]), encoding="utf-8")
