"""The core use case: classify the source folder and (optionally) move files."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ..adapters.filesystem import build_ref, is_eligible, iter_candidates, move_into
from ..domain.category import Config
from ..domain.engine import Engine
from .report import SortOutcome

Logger = Callable[[str], None]


def _noop(_: str) -> None:
    return None


class Sorter:
    """Wires the engine to the filesystem. Holds no mutable state itself."""

    def __init__(self, config: Config, engine: Engine | None = None, *, log: Logger = _noop):
        self._config = config
        self._engine = engine or Engine(config)
        self._log = log

    def sort_once(self, *, apply: bool, respect_age: bool = True) -> list[SortOutcome]:
        """Classify every candidate once.

        When ``apply`` is true, eligible files are moved. When ``respect_age``
        is false (used by ``plan``), age and in-progress checks are ignored so
        the caller sees the full picture of the folder as it stands.
        """
        settings = self._config.settings
        managed = self._config.managed_dirs
        outcomes: list[SortOutcome] = []

        for path in iter_candidates(settings, managed):
            if respect_age and not is_eligible(path, settings):
                continue

            ref = build_ref(path, settings.content_max_bytes)
            decision = self._engine.classify(ref)

            moved_to: Path | None = None
            if apply:
                destination_dir = settings.source / decision.category
                moved_to = move_into(path, destination_dir)
                self._log(f"[{decision.category}] ({decision.stage.value}) {path.name}")

            outcomes.append(
                SortOutcome(
                    source=path,
                    category=decision.category,
                    stage=decision.stage,
                    moved_to=moved_to,
                )
            )
        return outcomes
