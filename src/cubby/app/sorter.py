"""The core use case: classify the source folder and (optionally) move files."""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from dataclasses import replace
from datetime import date
from pathlib import Path

from ..adapters.filesystem import (
    build_ref,
    duplicate_in,
    iter_candidates,
    move_into,
    not_yet_reason,
)
from ..adapters.journal import Entry, Journal, new_run_id
from ..adapters.ledger import Failure, Ledger, RunRecord, now_iso
from ..adapters.logging import run_context
from ..domain.category import Category, Config
from ..domain.engine import Engine
from ..domain.file_ref import FileRef
from ..domain.invoices import Placement, plan_placement
from .report import SortOutcome

Logger = Callable[[str], None]


def _noop(_: str) -> None:
    return None


def _mtime_date(path: Path) -> date:
    """The file's modification date, used as the invoice-date fallback.

    A file that cannot be stat'ed here is about to fail its move anyway, and
    that failure is reported; today's date is only a placeholder for it.
    """
    try:
        return date.fromtimestamp(path.stat().st_mtime)
    except OSError:
        return date.today()


def describe_error(exc: BaseException) -> str:
    """One line naming what went wrong, for the ledger and the terminal."""
    detail = str(exc) or "no detail"
    return f"{type(exc).__name__}: {detail}"


def _never() -> bool:
    return False


def _ignore(_: Path, __: str) -> None:
    return None


def _shown(destination: Path, root: Path) -> str:
    """Where a file went, relative to the sorted folder when it is inside it."""
    try:
        return destination.relative_to(root).as_posix()
    except ValueError:
        return str(destination)


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
        ledger: Ledger | None = None,
        mode: str = "run",
    ):
        self._config = config
        self._engine = engine or Engine(config)
        self._log = log
        # Warnings default to the log, but a caller that can reach the user
        # (the CLI) passes something louder: a lost undo journal must be seen.
        self._warn = warn or log
        self._journal = journal
        self._ledger = ledger
        self._mode = mode
        self._by_name: dict[str, Category] = {c.name: c for c in config.categories}

    @property
    def source(self) -> Path:
        return self._config.settings.source

    def placement_for(self, path: Path, ref: FileRef, category: str) -> Placement:
        """Month subfolder and optional rename for a finance file (else empty)."""
        rules = self._by_name.get(category)
        if rules is None or not rules.date_folders:
            return Placement(subdir="")
        settings = self._config.settings
        text = ref.text() if settings.content_scan else ""
        return plan_placement(
            name=ref.name,
            text=text,
            fallback_date=_mtime_date(path),
            vendor_rename=rules.vendor_rename,
            month_style=settings.month_style,
            month_lang=settings.month_lang,
            vendors=settings.vendors,
        )

    def _outcome(self, path: Path, *, apply: bool, run_id: str, seq: int) -> SortOutcome:
        """Classify one entry and, when applying, move it and journal the move.

        Raises:
            OSError: The entry could not be read or moved.
            ValueError: The destination was refused (outside the watched folder).
        """
        settings = self._config.settings
        ref = build_ref(path, settings.content_max_bytes)
        decision = self._engine.classify(ref)
        placement = self.placement_for(path, ref, decision.category)
        outcome = SortOutcome(
            source=path,
            category=decision.category,
            stage=decision.stage,
            rule=decision.rule,
            subdir=placement.subdir,
            renamed_to=placement.new_name,
        )
        folder = settings.source / decision.category / placement.subdir
        if not apply:
            if settings.dedupe:  # what the run would do: delete a byte-identical duplicate
                name = placement.new_name or path.name
                return replace(outcome, duplicate_of=duplicate_in(path, folder, name))
            return outcome

        moved = move_into(
            path,
            folder,
            root=settings.source,
            dedupe=settings.dedupe,
            rename_to=placement.new_name,
        )
        # Journal first: once the file has moved, nothing (not even a log line
        # to a closed pipe) may come between the move and its way back.
        journaled = self._journal_move(
            Entry(run_id, seq, moved.op, path, moved.destination, moved.ident)
        )
        if journaled and moved.ident is None:
            self._warn(
                f"could not read {moved.destination} after the move; 'cubby undo' will "
                f"put it back without checking it is still the file this run moved"
            )
        result = outcome.moved(moved.destination, journaled=journaled)
        if moved.op == "dedupe":
            result = replace(result, duplicate_of=moved.destination)
        # The move and its journal entry are done; a lost log line changes neither.
        with contextlib.suppress(OSError):
            where = _shown(moved.destination, self.source)
            done = f"deleted, duplicate of {where}" if moved.op == "dedupe" else f"-> {where}"
            self._log(f"[{decision.category}] ({decision.stage.value}) {path.name} {done}")
        return result

    def _journal_move(self, entry: Entry) -> bool:
        """Record ``entry``. False when a journal was expected and could not be written."""
        if self._journal is None:
            return True
        try:
            self._journal.record(entry)
        except OSError as exc:
            # The file has already moved: aborting now would lose the journal
            # and the rest of the sort. Say so loudly instead, once per file.
            self._warn(
                f"could not write the undo journal at {self._journal.path} ({exc}); "
                f"'cubby undo' will not be able to put back {entry.source.name}"
            )
            return False
        return True

    def sort_once(
        self,
        *,
        apply: bool,
        respect_age: bool = True,
        stop: Callable[[], bool] = _never,
        run_id: str | None = None,
        on_waiting: Callable[[Path, str], None] = _ignore,
    ) -> list[SortOutcome]:
        """Classify every candidate once.

        When ``apply`` is true, eligible files are moved, each move is journaled
        as it happens, and a file that cannot be moved is reported in its
        outcome's ``error`` without stopping the others. When ``respect_age`` is
        false (used by ``plan``), the age is ignored so the caller sees the
        folder as it will be sorted once everything has settled; an in-progress
        download is still left alone. ``stop`` is
        checked before each file, so a stop request ends the pass between two
        files, never in the middle of one. ``run_id`` names the pass in the
        journal, the ledger and the log; a fresh one is made when omitted.
        ``on_waiting`` is called with each file left for a later pass because
        it has not settled yet, and why.
        """
        run_id = run_id or new_run_id()
        started = now_iso()
        outcomes: list[SortOutcome] = []
        with run_context(run_id):
            self._sort_each(
                outcomes,
                apply=apply,
                respect_age=respect_age,
                stop=stop,
                run_id=run_id,
                on_waiting=on_waiting,
            )
            if apply:
                self._record_run(run_id, started, outcomes)
                if self._journal is not None:
                    self._compact_journal(self._journal)
        return outcomes

    def _sort_each(
        self,
        outcomes: list[SortOutcome],
        *,
        apply: bool,
        respect_age: bool,
        stop: Callable[[], bool],
        run_id: str,
        on_waiting: Callable[[Path, str], None],
    ) -> None:
        settings = self._config.settings
        for path in iter_candidates(settings, self._config.managed_dirs):
            if stop():
                break
            if reason := not_yet_reason(path, settings, ignore_age=not respect_age):
                on_waiting(path, reason)
                continue
            try:
                outcomes.append(self._outcome(path, apply=apply, run_id=run_id, seq=len(outcomes)))
            except Exception as exc:  # noqa: BLE001 - reported below, and the others still sort
                # Whatever stops one file (permissions, a file that vanished, a
                # refused destination, a parser bug) must not stop the rest: the
                # agent would otherwise fail on the same file every minute and
                # never reach the files after it. The failure is not hidden: it
                # goes to stderr or the log, the ledger, and the exit code.
                error = describe_error(exc)
                self._warn(f"could not sort {path.name}: {error}")
                outcomes.append(SortOutcome.failed(path, error))

    def _record_run(self, run_id: str, started: str, outcomes: list[SortOutcome]) -> None:
        if self._ledger is None or not outcomes:
            return
        failures = tuple(Failure(o.name, o.error) for o in outcomes if o.error)
        record = RunRecord(
            run=run_id,
            mode=self._mode,
            source=str(self._config.settings.source),
            started=started,
            finished=now_iso(),
            moved=sum(1 for o in outcomes if o.moved_to is not None),
            failed=len(failures),
            failures=failures,
        )
        try:
            self._ledger.record(record)
        except OSError as exc:
            self._warn(f"could not write the run ledger at {self._ledger.runs_path} ({exc})")

    def _compact_journal(self, journal: Journal) -> None:
        try:
            journal.compact()
        except OSError as exc:
            self._warn(f"could not compact the undo journal at {journal.path} ({exc})")
