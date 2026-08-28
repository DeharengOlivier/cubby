"""The core use case: classify the source folder and (optionally) move files."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path

from ..adapters.filesystem import build_ref, is_eligible, iter_candidates, move_into
from ..adapters.journal import Journal, Move
from ..domain.category import Category, Config
from ..domain.engine import Engine
from ..domain.file_ref import FileRef
from ..domain.invoices import Placement, plan_placement
from .report import SortOutcome

Logger = Callable[[str], None]


def _noop(_: str) -> None:
    return None


def _mtime_date(path: Path) -> date:
    """The file's modification date, used as the invoice-date fallback."""
    try:
        return date.fromtimestamp(path.stat().st_mtime)
    except OSError:
        return date.today()


class Sorter:
    """Wires the engine to the filesystem. Holds no mutable state itself."""

    def __init__(
        self,
        config: Config,
        engine: Engine | None = None,
        *,
        log: Logger = _noop,
        warn: Logger | None = None,
        journal: Journal | None = None,
    ):
        self._config = config
        self._engine = engine or Engine(config)
        self._log = log
        # Warnings default to the log, but a caller that can reach the user
        # (the CLI) passes something louder: a lost undo journal must be seen.
        self._warn = warn or log
        self._journal = journal
        self._by_name: dict[str, Category] = {c.name: c for c in config.categories}

    def _placement(self, path: Path, ref: FileRef, category: str) -> Placement:
        """Month subfolder and optional rename for a finance file (else empty)."""
        rules = self._by_name.get(category)
        if rules is None or not rules.date_folders:
            return Placement(subdir="")
        settings = self._config.settings
        text = ref.text() if settings.content_scan else ""
        return plan_placement(
            name=ref.name,
            ext=ref.ext,
            text=text,
            fallback_date=_mtime_date(path),
            vendor_rename=rules.vendor_rename,
            month_style=settings.month_style,
            month_lang=settings.month_lang,
            vendors=settings.vendors,
        )

    def sort_once(self, *, apply: bool, respect_age: bool = True) -> list[SortOutcome]:
        """Classify every candidate once.

        When ``apply`` is true, eligible files are moved. When ``respect_age``
        is false (used by ``plan``), age and in-progress checks are ignored so
        the caller sees the full picture of the folder as it stands.
        """
        settings = self._config.settings
        managed = self._config.managed_dirs
        outcomes: list[SortOutcome] = []
        moves: list[Move] = []

        for path in iter_candidates(settings, managed):
            if respect_age and not is_eligible(path, settings):
                continue

            ref = build_ref(path, settings.content_max_bytes)
            decision = self._engine.classify(ref)
            placement = self._placement(path, ref, decision.category)

            moved_to: Path | None = None
            if apply:
                destination_dir = settings.source / decision.category / placement.subdir
                moved_to = move_into(
                    path,
                    destination_dir,
                    root=settings.source,
                    dedupe=settings.dedupe,
                    rename_to=placement.new_name,
                )
                moves.append((path, moved_to))
                self._log(f"[{decision.category}] ({decision.stage.value}) {path.name}")

            outcomes.append(
                SortOutcome(
                    source=path,
                    category=decision.category,
                    stage=decision.stage,
                    moved_to=moved_to,
                    subdir=placement.subdir,
                    renamed_to=placement.new_name,
                )
            )

        if self._journal is not None:
            self._journal.record_run(moves, warn=self._warn)
        return outcomes
