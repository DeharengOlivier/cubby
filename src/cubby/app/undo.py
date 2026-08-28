"""Undo use case: move the most recent run's files back where they came from."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

from ..adapters.filesystem import unique_destination
from ..adapters.journal import Journal

Logger = Callable[[str], None]


def _noop(_: str) -> None:
    return None


def undo_last_run(journal: Journal, *, log: Logger = _noop) -> int:
    """Reverse the last recorded run.

    Every file that can be restored is restored, even if one of them cannot: a
    half-undone run that stops at the first problem is worse than one that puts
    back everything it can and says what it could not. The run is consumed
    either way, so running undo twice never restores the same file twice.

    Args:
        journal: The journal to read the run from and consume.
        log: Called with one line per file, and per file that could not be moved.

    Returns:
        The number of files put back.
    """
    moves = journal.last_run()
    if not moves:
        log("nothing to undo")
        return 0

    restored = 0
    for source, destination in moves:
        if not destination.exists():
            log(f"skip (missing): {destination.name}")
            continue
        try:
            source.parent.mkdir(parents=True, exist_ok=True)
            target = unique_destination(source.parent, source.name)
            # shutil.move, not Path.rename: the sort that created this move used
            # shutil.move and may have crossed a filesystem, where rename fails
            # with EXDEV and the file could never be put back.
            shutil.move(str(destination), str(target))
        except OSError as exc:
            log(f"skip (cannot restore {destination.name}): {exc}")
            continue
        log(f"restored {Path(target).name}")
        restored += 1

    journal.drop_last_run()
    return restored
