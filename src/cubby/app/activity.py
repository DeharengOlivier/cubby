"""What cubby did over a recent window, read from the run ledger.

``cubby status`` shows it: how many runs, moves and failures, and the failures
grouped by kind of error, plus how many files lost their content to a broken
converter
(sorted anyway, so counted apart from the failures). Forty files refused with
the same permission error are one problem to fix, not forty, so they read as
one line, with the files it hit, when it was last seen and which cubby versions
saw it. The agent's runs that recorded their duration and the files they left
to settle give its run time of the day (p50, p95, longest) and backlog.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

from ..adapters.ledger import RunRecord

#: A quoted part of a message, not an apostrophe inside a word ("can't").
_QUOTED = re.compile(r"(?<!\w)('[^']*'|\"[^\"]*\")(?!\w)")


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
    complete: bool  # False when the ledger was trimmed of runs inside the window
    extraction_failures: int  # files sorted without their content: converters broke
    pass_ms: PassLatency | None = None  # None when no agent run of the window was timed
    backlog: Backlog | None = None  # None when no agent run of the window counted its waiting


@dataclass(frozen=True)
class PassLatency:
    """How long the agent's timed runs of the window took, in milliseconds (nearest rank)."""

    count: int
    p50: int
    p95: int
    max: int


@dataclass(frozen=True)
class Backlog:
    """Files the agent's runs of the window left to settle: the oldest, the newest, the most."""

    first: int
    last: int
    peak: int


def error_kind(error: str) -> str:
    """What went wrong in ``error``, without the file it went wrong on.

    ``error`` reads ``Type: detail``. The detail stops at its first ": ",
    which is where Python's OS errors (``[Errno 13] Permission denied: 'path'``)
    and cubby's own messages put the paths; a quoted part left after that is
    blanked out.
    """
    kind, colon, detail = error.partition(": ")
    if colon:
        kind = f"{kind}: {detail.partition(': ')[0]}"
    return _QUOTED.sub("'...'", kind)


@dataclass
class _Group:
    last_seen: str
    count: int = 0
    runs: int = 0
    files: list[str] = field(default_factory=list)
    versions: set[str] = field(default_factory=set)


def summarize(
    records: Iterable[RunRecord],
    *,
    since: datetime,
    now: datetime | None = None,
    trimmed: bool = False,
    top: int = 5,
    files: int = 3,
) -> Activity:
    """The runs that finished between ``since`` and ``now``, in local time.

    A record whose date is unreadable is left out, and so is one dated after
    ``now`` (written before the clock was set back). ``trimmed`` says the
    ledger dropped its oldest lines: unless a run older than the window was
    kept, some of the window may be missing, and the result says so.
    """
    now = now or datetime.now()
    dated = [(when, record) for record in records if (when := _finished(record)) is not None]
    recent = [
        record
        for when, record in sorted(dated, key=lambda item: item[0], reverse=True)
        if since <= when <= now
    ]
    complete = not trimmed or any(when < since for when, _ in dated)
    agent = [record for record in recent if record.mode == "watch"]
    groups: dict[str, _Group] = {}
    for record in recent:
        for kind in dict.fromkeys(error_kind(f.error) for f in record.failures):
            groups.setdefault(kind, _Group(last_seen=record.finished)).runs += 1
        for failure in record.failures:
            group = groups[error_kind(failure.error)]
            group.count += 1
            if len(group.files) < files and failure.file not in group.files:
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
        complete=complete,
        extraction_failures=sum(record.extraction_failures for record in recent),
        # The agent's runs only: a manual `cubby run`, maybe on another folder,
        # says nothing about how close the agent's passes come to its interval.
        pass_ms=_latency([r.duration_ms for r in agent if r.duration_ms is not None]),
        backlog=_backlog([r.waiting for r in reversed(agent) if r.waiting is not None]),
    )


def _latency(durations: list[int]) -> PassLatency | None:
    if not durations:
        return None
    durations.sort()

    def rank(share: float) -> int:
        return durations[math.ceil(share * len(durations)) - 1]

    return PassLatency(count=len(durations), p50=rank(0.5), p95=rank(0.95), max=durations[-1])


def _backlog(waiting: list[int]) -> Backlog | None:
    """``waiting`` is oldest first."""
    return Backlog(first=waiting[0], last=waiting[-1], peak=max(waiting)) if waiting else None


def _frozen(kind: str, group: _Group) -> ErrorGroup:
    return ErrorGroup(
        kind=kind,
        count=group.count,
        runs=group.runs,
        last_seen=group.last_seen,
        files=tuple(group.files),
        versions=tuple(sorted(group.versions)),
    )


def _finished(record: RunRecord) -> datetime | None:
    """When ``record`` finished, in naive local time as cubby writes it; None if unreadable."""
    try:
        when = datetime.fromisoformat(record.finished)
    except ValueError:
        return None
    # A date with an offset (a foreign or hand-edited line) cannot be compared
    # with a naive one: read it in local time instead of failing on it.
    return when.astimezone().replace(tzinfo=None) if when.tzinfo else when
