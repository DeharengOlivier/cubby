"""History use case: recent runs, and how much of each has been undone."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..adapters.journal import Journal
from ..adapters.ledger import Ledger, RunRecord

#: ``undoable``: nothing undone yet. ``partly undone``: some moves were put
#: back, others are still pending. ``undone``: every move was settled by an
#: undo. ``unknown``: the journal no longer holds the run (dropped by
#: compaction, or never written), so ``cubby undo --run`` cannot reach it.
UndoState = Literal["undoable", "partly undone", "undone", "unknown"]


@dataclass(frozen=True)
class RunSummary:
    record: RunRecord
    undo: UndoState

    @property
    def undone(self) -> bool:
        return self.undo == "undone"


def undo_state_of(moves: int, pending: int) -> UndoState:
    """The state of a run with ``moves`` journaled moves, ``pending`` of them not undone."""
    if pending == 0:
        return "undone"
    return "undoable" if pending == moves else "partly undone"


def recent_runs(ledger: Ledger, journal: Journal, limit: int) -> list[RunSummary]:
    """The ``limit`` most recent runs, newest first.

    Raises:
        OSError: The ledger or the journal cannot be read.
    """
    records = ledger.runs(limit=limit)
    tallies = journal.tallies()
    return [
        RunSummary(
            record,
            undo_state_of(*tallies[record.run]) if record.run in tallies else "unknown",
        )
        for record in records
    ]
