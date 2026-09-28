"""The core use case: classify the source folder and (optionally) move files."""

from __future__ import annotations

import contextlib
import errno
import os
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import date
from pathlib import Path

from ..adapters.extraction import ConverterFailure
from ..adapters.filesystem import (
    ExtractionFailureHandler,
    build_ref,
    duplicate_in,
    file_in_the_way,
    files_identical,
    in_the_way,
    iter_candidates,
    move_into,
    not_yet_reason,
)
from ..adapters.journal import Entry, Journal, new_run_id
from ..adapters.ledger import Ledger, RunRecord, now_iso
from ..adapters.logging import run_context
from ..domain.category import Category, Config
from ..domain.engine import Engine
from ..domain.file_ref import FileRef
from ..domain.invoices import Placement, is_month_folder, plan_placement
from .report import PassTally, SortOutcome

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


#: What to do about an error, by errno, when there is something to do.
_HINTS = {
    errno.EACCES: "check cubby's permissions there",
    errno.EPERM: "check cubby's permissions there",
    errno.EROFS: "the disk is read-only",
    errno.ENOSPC: "the disk is full",
}


def describe_error(exc: BaseException, root: Path | None = None) -> str:
    """One line naming what went wrong, for the ledger and the terminal.

    An error on a file reads ``Type: what happened: file -> destination (what
    to do)``, with paths relative to ``root`` (the sorted folder); the part
    before the second colon names the kind of error, which ``cubby status``
    groups on.
    """
    kind = type(exc).__name__
    if not isinstance(exc, OSError):
        return f"{kind}: {str(exc) or 'no detail'}"
    hint = _HINTS.get(exc.errno or 0)
    tail = f" ({hint})" if hint else ""
    if getattr(exc, "names_its_file", False):  # cubby's own message, remedy included
        return f"{kind}: {exc.strerror}"
    if not (exc.strerror and exc.filename is not None):
        return f"{kind}: {str(exc) or 'no detail'}{tail}"
    paths = " -> ".join(
        _where(Path(os.fsdecode(name)), root)
        for name in (exc.filename, exc.filename2)
        if name is not None
    )
    return f"{kind}: {exc.strerror}: {paths}{tail}"


def _where(path: Path, root: Path | None) -> str:
    """``path`` relative to ``root``, or whole when it is ``root`` or outside it."""
    if root is None or path == root:
        return str(path)
    return _shown(path, root)


def _never() -> bool:
    return False


def _drop(_: SortOutcome) -> None:
    return None


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
        """Month subfolder and optional rename for a finance file (else empty).

        A folder matched by its name is filed whole at the category's root,
        under its own name: its name is not an invoice's, and it has no
        printed date to file it by month.
        """
        rules = self._by_name.get(category)
        if rules is None or not rules.date_folders:
            return Placement(subdir="")
        settings = self._config.settings
        if not ref.is_file:
            # A folder named like a month folder would become the month folder
            # later invoices are filed into, and mix with them: it is marked.
            if is_month_folder(ref.name, settings.month_style, settings.month_lang):
                return Placement(subdir="", new_name=f"{ref.name} (folder)")
            return Placement(subdir="")
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

    def _outcome(
        self,
        path: Path,
        *,
        apply: bool,
        run_id: str,
        seq: int,
        claimed: dict[Path, Path],
        on_extraction_failure: ExtractionFailureHandler,
    ) -> SortOutcome:
        """Classify one entry and, when applying, move it and journal the move.

        ``claimed`` maps each destination a plan has given out in this pass to
        the file given it: a later file with the same bytes and destination is
        what a run would delete as its duplicate, although nothing is filed yet.

        Raises:
            OSError: The entry could not be read or moved.
            ValueError: The destination was refused (outside the watched folder).
        """
        settings = self._config.settings
        ref = build_ref(
            path, settings.content_max_bytes, on_extraction_failure=on_extraction_failure
        )
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
            return self._planned(path, outcome, folder, placement.new_name or path.name, claimed)

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

    def _planned(
        self, path: Path, outcome: SortOutcome, folder: Path, name: str, claimed: dict[Path, Path]
    ) -> SortOutcome:
        """What a run would do with ``path``: fail, delete it as a duplicate, or move it."""
        settings = self._config.settings
        # The run would fail on a file standing where a folder must go.
        if (blocker := file_in_the_way(folder, settings.source)) is not None:
            return replace(outcome, error=in_the_way(blocker, settings.source))
        target = folder / name
        if settings.dedupe:  # what the run would do: delete a byte-identical duplicate
            duplicate = duplicate_in(path, folder, name)
            if duplicate is None and target in claimed and files_identical(path, claimed[target]):
                duplicate = target
            if duplicate is not None:
                return replace(outcome, duplicate_of=duplicate)
        if not os.path.lexists(target):
            claimed.setdefault(target, path)
        return outcome

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
        """Classify every candidate once, and return every outcome.

        For the commands that print what was (or would be) done file by file:
        the list grows with the folder. The agent, which only counts, calls
        :meth:`sort_pass`. The arguments are those of :meth:`sort_pass`.
        """
        outcomes: list[SortOutcome] = []
        self.sort_pass(
            apply=apply,
            on_outcome=outcomes.append,
            respect_age=respect_age,
            stop=stop,
            run_id=run_id,
            on_waiting=on_waiting,
        )
        return outcomes

    def sort_pass(
        self,
        *,
        apply: bool,
        on_outcome: Callable[[SortOutcome], None] = _drop,
        respect_age: bool = True,
        stop: Callable[[], bool] = _never,
        run_id: str | None = None,
        on_waiting: Callable[[Path, str], None] = _ignore,
    ) -> PassTally:
        """Classify every candidate once, handing each outcome on as it is made.

        Keeps no outcome: each goes to ``on_outcome``, then only its counts
        stay, so a pass over any number of files holds what one file needs
        (plus the sorted listing of names). Returns those counts.

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
        clock_started = time.monotonic()
        tally = PassTally()
        waiting = 0

        def count_waiting(path: Path, reason: str) -> None:
            nonlocal waiting
            waiting += 1
            on_waiting(path, reason)

        def count_and_hand_on(outcome: SortOutcome) -> None:
            tally.add(outcome)
            on_outcome(outcome)

        def on_extraction_failure(path: Path, failures: tuple[ConverterFailure, ...]) -> None:
            tally.extraction_failures += 1  # counted, like the outcomes, not kept
            self._report_extraction_failure(path, failures)

        with run_context(run_id):
            self._sort_each(
                count_and_hand_on,
                apply=apply,
                respect_age=respect_age,
                stop=stop,
                run_id=run_id,
                on_waiting=count_waiting,
                on_extraction_failure=on_extraction_failure,
            )
            if apply:
                duration_ms = round((time.monotonic() - clock_started) * 1000)
                self._record_run(run_id, started, tally, duration_ms=duration_ms, waiting=waiting)
                if self._journal is not None:
                    self._compact_journal(self._journal)
        return tally

    def _sort_each(
        self,
        emit: Callable[[SortOutcome], None],
        *,
        apply: bool,
        respect_age: bool,
        stop: Callable[[], bool],
        run_id: str,
        on_waiting: Callable[[Path, str], None],
        on_extraction_failure: ExtractionFailureHandler,
    ) -> None:
        settings = self._config.settings
        claimed: dict[Path, Path] = {}
        seq = 0  # the journal numbers every outcome of the pass, failed ones included
        for path in iter_candidates(settings, self._config.managed_dirs):
            if stop():
                break
            if reason := not_yet_reason(path, settings, ignore_age=not respect_age):
                on_waiting(path, reason)
                continue
            emit(
                self._outcome_or_failure(
                    path,
                    apply=apply,
                    run_id=run_id,
                    seq=seq,
                    claimed=claimed,
                    on_extraction_failure=on_extraction_failure,
                )
            )
            seq += 1

    def _outcome_or_failure(
        self,
        path: Path,
        *,
        apply: bool,
        run_id: str,
        seq: int,
        claimed: dict[Path, Path],
        on_extraction_failure: ExtractionFailureHandler,
    ) -> SortOutcome:
        """:meth:`_outcome`, or the failed outcome saying why there is none."""
        try:
            return self._outcome(
                path,
                apply=apply,
                run_id=run_id,
                seq=seq,
                claimed=claimed,
                on_extraction_failure=on_extraction_failure,
            )
        except Exception as exc:  # noqa: BLE001 - reported below, and the others still sort
            # Whatever stops one file (permissions, a file that vanished, a
            # refused destination, a parser bug) must not stop the rest: the
            # agent would otherwise fail on the same file every minute and
            # never reach the files after it. The failure is not hidden: it
            # goes to stderr or the log, the ledger, and the exit code.
            error = describe_error(exc, self.source)
            self._warn(f"could not sort {path.name}: {error}")
            return SortOutcome.failed(path, error)

    def _report_extraction_failure(
        self, path: Path, failures: tuple[ConverterFailure, ...]
    ) -> None:
        """Warn that a converter broke on ``path``; the file still sorts by name and type.

        A missing converter never gets here (see ``extraction.extract``), so
        this is a converter that was installed and failed: worth a WARNING
        line, with the run id the log adds inside a pass.
        """
        what = "; ".join(failure.describe() for failure in failures)
        # The log line is written before stderr is tried: a closed stderr must
        # not turn an unreadable content into a file that failed to sort.
        with contextlib.suppress(OSError):
            self._warn(f"content extraction failed for {_shown(path, self.source)}: {what}")

    def _record_run(
        self, run_id: str, started: str, tally: PassTally, *, duration_ms: int, waiting: int
    ) -> None:
        if self._ledger is None or not tally.count:
            return
        record = RunRecord(
            run=run_id,
            mode=self._mode,
            source=str(self._config.settings.source),
            started=started,
            finished=now_iso(),
            moved=tally.moved,
            failed=tally.failed,
            # The first failures only, as many as the ledger writes of a run.
            failures=tuple(tally.failures),
            extraction_failures=tally.extraction_failures,
            duration_ms=duration_ms,
            waiting=waiting,
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
