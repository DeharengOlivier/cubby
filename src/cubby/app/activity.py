"""What cubby did over a recent window, read from the run ledger.

``cubby status`` shows it: how many runs, moves and failures, and the failures
grouped by kind of error. Forty files refused with the same permission error
are one problem to fix, not forty, so they read as one line, with the files it
hit, when it was last seen and which cubby versions saw it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

from ..adapters.ledger import RunRecord

#: A quoted part of an error message: in practice the path it failed on.
_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")


@dataclass(frozen=True)
class ErrorGroup:
    kind: str
    count: int  # failures recorded with this kind (the ledger keeps 20 per run)
    runs: int  # how many runs hit it
    last_seen: str
    files: tuple[str, ...]  # the most recent first
    versions: tuple[str, ...]


@dataclass(frozen=True)
class Activity:
    runs: int
    moved: int
    failed: int  # exact, even when a run recorded only its first failures
    errors: tuple[ErrorGroup, ...]  # the most frequent first


def error_kind(error: str) -> str:
    """``error`` with its quoted parts (the file it failed on) blanked out."""
    return _QUOTED.sub("'...'", error)


@dataclass
class _Group:
    last_seen: str
    count: int = 0
    runs: int = 0
    files: list[str] = field(default_factory=list)
    versions: set[str] = field(default_factory=set)


def summarize(
    records: Iterable[RunRecord], *, since: datetime, top: int = 5, files: int = 3
) -> Activity:
    """The runs that finished after ``since``; a record whose date is unreadable is left out."""
    recent = sorted(
        (record for record in records if _finished_after(record, since)),
        key=lambda record: record.finished,
        reverse=True,
    )
    groups: dict[str, _Group] = {}
    for record in recent:
        for kind in dict.fromkeys(error_kind(f.error) for f in record.failures):
            groups.setdefault(kind, _Group(last_seen=record.finished)).runs += 1
        for failure in record.failures:
            group = groups[error_kind(failure.error)]
            group.count += 1
            if len(group.files) < files:
                group.files.append(failure.file)
            group.versions.add(record.version)
    # Insertion order is most recent first, and the sort is stable: among kinds
    # seen as often, the most recent comes first.
    ranked = sorted(groups.items(), key=lambda item: -item[1].count)
    return Activity(
        runs=len(recent),
        moved=sum(record.moved for record in recent),
        failed=sum(record.failed for record in recent),
        errors=tuple(_frozen(kind, group) for kind, group in ranked[:top]),
    )


def _frozen(kind: str, group: _Group) -> ErrorGroup:
    return ErrorGroup(
        kind=kind,
        count=group.count,
        runs=group.runs,
        last_seen=group.last_seen,
        files=tuple(group.files),
        versions=tuple(sorted(group.versions)),
    )


def _finished_after(record: RunRecord, since: datetime) -> bool:
    try:
        return datetime.fromisoformat(record.finished) >= since
    except ValueError:
        return False
