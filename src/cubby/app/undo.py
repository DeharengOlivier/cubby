"""Undo use case: move the most recent run's files back where they came from."""

from __future__ import annotations

from collections.abc import Callable

from ..adapters.filesystem import unique_destination
from ..adapters.journal import Journal

Logger = Callable[[str], None]


def _noop(_: str) -> None:
    return None


def undo_last_run(journal: Journal, *, log: Logger = _noop) -> int:
    """Reverse the last recorded run. Returns the number of files restored."""
    moves = journal.last_run()
    if not moves:
        log("nothing to undo")
        return 0

    restored = 0
    for source, destination in moves:
        if not destination.exists():
            log(f"skip (missing): {destination.name}")
            continue
        source.parent.mkdir(parents=True, exist_ok=True)
        target = unique_destination(source.parent, source.name)
        destination.rename(target)
        log(f"restored {target.name}")
        restored += 1

    journal.drop_last_run()
    return restored
