"""Undo use case: put the files of a run back where they came from."""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field

from ..adapters.filesystem import identity, move_no_clobber, unique_destination
from ..adapters.journal import Entry, Journal, Run

Logger = Callable[[str], None]


def _noop(_: str) -> None:
    return None


@dataclass
class UndoResult:
    """What an undo did. ``failed`` entries stay pending and can be retried."""

    run_id: str | None = None
    restored: int = 0
    gone: int = 0  # no longer where the run put them
    replaced: int = 0  # another file stands there now: left alone
    failed: list[str] = field(default_factory=list)


def _restore(entry: Entry) -> str:
    """Put one entry back. Returns the name it was restored under.

    Raises:
        OSError: The file could not be put back; nothing was changed.
    """
    entry.source.parent.mkdir(parents=True, exist_ok=True)
    target = unique_destination(entry.source.parent, entry.source.name)
    if entry.op == "dedupe":
        # The run deleted this duplicate because the same bytes were already
        # filed at the destination. That filed copy stays where it is; the
        # duplicate is recreated from it.
        shutil.copy2(entry.destination, target)
    else:
        move_no_clobber(entry.destination, target)
    return target.name


def undo_run(journal: Journal, run_id: str | None = None, *, log: Logger = _noop) -> UndoResult:
    """Reverse one recorded run: the given one, else the latest with anything left.

    Entries are undone newest first. Every entry that can be restored is, even
    if another cannot: an undo that stops at the first problem is worse than
    one that puts back everything it can and says what it could not. An entry
    whose file no longer exists is settled as gone; one that fails (a
    permission, a full disk) stays pending, so running undo again retries it.

    Raises:
        OSError: The journal could not be read.
        KeyError: ``run_id`` names no run in the journal.
    """
    run: Run | None
    if run_id is None:
        run = journal.last_pending_run()
    else:
        run = journal.run(run_id)
        if run is None:
            raise KeyError(run_id)
    result = UndoResult(run_id=run.run_id if run else None)
    if run is None or not run.pending:
        log("nothing to undo")
        return result

    for entry in reversed(run.pending):
        if not os.path.lexists(entry.destination):  # a dangling symlink is still there
            log(f"skip (no longer at {entry.destination}): {entry.source.name}")
            journal.settle(entry, "gone")
            result.gone += 1
            continue
        if entry.ident is not None and identity(entry.destination) != entry.ident:
            # Another file took the name, or the file was changed since: moving
            # it could take a file cubby never moved, so it stays, and the user
            # decides (the file is named, and where it would have gone).
            log(
                f"skip (changed or replaced since the run: {entry.destination} is left "
                f"in place; move it back to {entry.source} by hand if it is yours): "
                f"{entry.source.name}"
            )
            journal.settle(entry, "gone")
            result.replaced += 1
            continue
        try:
            name = _restore(entry)
        except OSError as exc:
            log(
                f"pending (cannot restore {entry.destination.name}): {exc}; "
                f"fix the cause, then retry with 'cubby undo --run {run.run_id}'"
            )
            result.failed.append(entry.destination.name)
            continue
        journal.settle(entry, "restored")
        wanted = entry.source.name
        log(
            f"restored {wanted}"
            if name == wanted
            else f"restored {wanted} as {name} ({wanted} is taken)"
        )
        result.restored += 1
    return result


def undo_last_run(journal: Journal, *, log: Logger = _noop) -> int:
    """Reverse the latest run with anything left to undo. Returns files put back.

    Raises:
        OSError: The journal could not be read or written.
    """
    return undo_run(journal, log=log).restored
