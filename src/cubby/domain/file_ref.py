"""The unit the engine reasons about, and the verdict it produces.

``FileRef`` deliberately keeps the engine pure: instead of touching the
filesystem, the engine reads ``name``/``stem``/``ext`` and, only when it needs
to, calls ``read_text`` - a port the adapter layer wires to real extraction.
"""

from __future__ import annotations

import enum
from collections.abc import Callable
from dataclasses import dataclass, field


class Stage(enum.StrEnum):
    """Which rung of the cascade decided a file's destination."""

    STRONG_EXT = "strong-ext"
    NAME = "name"
    CONTENT = "content"
    TYPE = "type"
    UNSORTED = "unsorted"


@dataclass
class FileRef:
    """A lazy, IO-free view of a file to classify.

    ``read_text`` returns extracted text (or ``""``). It is provided by an
    adapter and is only invoked for the content stage, so the engine never pays
    extraction cost unless it has to. The result is memoised here.
    """

    name: str
    stem: str
    ext: str
    is_file: bool = True
    read_text: Callable[[], str] = lambda: ""
    _cached_text: str | None = field(default=None, repr=False, compare=False)

    def text(self) -> str:
        if self._cached_text is None:
            self._cached_text = self.read_text() or ""
        return self._cached_text


@dataclass(frozen=True)
class Decision:
    """Where a file should go and why."""

    category: str
    stage: Stage
