"""Builders shared by the tests, so a change to ``Config`` or ``Settings`` is
made once here instead of in every file that needs a small configuration."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from cubby.app.report import PassTally, SortOutcome
from cubby.domain.category import Category, Config, Settings

#: The smallest useful rule set: every ``.txt`` file is a document.
TEXT_DOCUMENTS = Category(name="Documents", extensions=frozenset({"txt"}))


def config_for(source: Path, *categories: Category, **settings: Any) -> Config:
    """A configuration sorting ``source`` at once, without reading file contents.

    Categories default to :data:`TEXT_DOCUMENTS`; ``settings`` override the
    defaults (``delay=0``, ``content_scan=False``).
    """
    base: dict[str, Any] = {"source": source, "delay": 0, "content_scan": False}
    base.update(settings)
    return Config(settings=Settings(**base), categories=categories or (TEXT_DOCUMENTS,))


def aged_file(folder: Path, name: str, content: str = "x", age: float = 10_000) -> Path:
    """Write ``name`` in ``folder`` (created if needed), dated ``age`` seconds back."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(content, encoding="utf-8")
    past = time.time() - age
    os.utime(path, (past, past))
    return path


@contextmanager
def process_named_cubby_watch() -> Iterator[int]:
    """A live process whose command line reads like a cubby agent's; yields its pid."""
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)", "cubby", "watch"]
    )
    try:
        yield process.pid
    finally:
        process.kill()
        process.wait(timeout=10)


class PassesFromSortOnce:
    """For a fake sorter that returns a list from ``sort_once``: the pass the agent calls.

    The agent calls ``Sorter.sort_pass``, which hands each outcome on and returns
    their counts; this plays the fake's list through it the same way.
    """

    def sort_once(self, **options: Any) -> list[SortOutcome]:
        raise NotImplementedError

    def sort_pass(self, *, on_outcome: Any, **options: Any) -> PassTally:
        tally = PassTally()
        for outcome in self.sort_once(**options):
            tally.add(outcome)
            on_outcome(outcome)
        return tally
