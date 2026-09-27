"""History use case: recent runs, and whether each one has been undone."""

from __future__ import annotations

from dataclasses import dataclass

from ..adapters.journal import Journal
from ..adapters.ledger import Ledger, RunRecord


@dataclass(frozen=True)
class RunSummary:
    record: RunRecord
    undone: bool  # every move of the run has been restored or settled


def recent_runs(ledger: Ledger, journal: Journal, limit: int) -> list[RunSummary]:
    """The ``limit`` most recent runs, newest first.

    Raises:
        OSError: The ledger or the journal cannot be read.
    """
    records = ledger.runs(limit=limit)
    journaled = {run.run_id: run for run in journal.runs()}
    summaries = []
    for record in records:
        run = journaled.get(record.run)
        summaries.append(RunSummary(record, undone=bool(run and not run.pending)))
    return summaries
