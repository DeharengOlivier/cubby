"""Whether the agent can do its job now, as opposed to whether it is alive.

``cubby status`` reads liveness from the heartbeat: a process that still
passes. A live agent can still sort nothing: its folder was removed or lost
its permissions, the state folder became read-only, a file stands where a
category folder goes, or someone paused it. Readiness names those. A missing
content converter does not stop sorting (files go by name and type), so it
only degrades readiness.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..adapters.extraction import formats_without_converter
from ..adapters.filesystem import blocked_folders
from ..domain.category import Settings


@dataclass(frozen=True)
class Readiness:
    problems: tuple[str, ...]  # what stops the sorting, apart from a pause
    paused: bool
    degraded: tuple[str, ...] = ()  # formats sorted without their content: no converter

    @property
    def ready(self) -> bool:
        return not self.problems and not self.paused

    def to_json(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "problems": list(self.problems),
            "degraded": list(self.degraded),
        }


def check_readiness(
    settings: Settings,
    managed: frozenset[str],
    *,
    state_folder: Path,
    paused: bool,
    converters: Mapping[str, bool],
) -> Readiness:
    """What stands between the agent and sorting ``settings.source`` now."""
    if source_problem := _source_problem(settings.source):
        problems = [source_problem]
    else:
        problems = [
            f"a file named {name} is in the way of the {name}/ folder cubby sorts into"
            for name in blocked_folders(settings, managed)
        ]
    if state_problem := _state_problem(state_folder):
        problems.append(state_problem)
    degraded = formats_without_converter(dict(converters)) if settings.content_scan else []
    return Readiness(problems=tuple(problems), paused=paused, degraded=tuple(degraded))


def _source_problem(source: Path) -> str | None:
    try:
        exists, is_dir = source.exists(), source.is_dir()
    except OSError as exc:  # a folder above it cannot be searched
        return f"source folder cannot be checked: {source} ({exc.strerror or exc})"
    if not exists:
        return f"source folder does not exist: {source}"
    if not is_dir:
        return f"source is not a folder: {source}"
    # Listing needs read and search; moving out of it and creating the
    # category folders in it needs write and search.
    if not os.access(source, os.R_OK | os.X_OK):
        return f"source folder is not readable: {source}"
    if not os.access(source, os.W_OK | os.X_OK):
        return f"source folder is not writable: {source}"
    return None


def _state_problem(folder: Path) -> str | None:
    """Whether the journal, ledger and heartbeat can be written in ``folder``.

    A folder not made yet counts as writable when the nearest one that exists
    is: cubby creates it on the first write.
    """
    existing = folder
    while not os.path.lexists(existing) and existing != existing.parent:
        existing = existing.parent
    # lexists never raises: a folder that cannot be searched reads as absent,
    # so the walk stops at one that exists and can be stat'ed.
    writable = existing.is_dir() and os.access(existing, os.W_OK | os.X_OK)
    return None if writable else f"state folder is not writable: {folder}"
