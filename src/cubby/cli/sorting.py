"""Commands that sort, watch, undo and pause."""

from __future__ import annotations

import argparse
import contextlib
import signal
import sys
import threading

from ..adapters.filesystem import blocked_folders
from ..adapters.journal import Journal
from ..adapters.ledger import Ledger
from ..adapters.lock import exclusive
from ..adapters.logging import file_logger
from ..adapters.notify import notifier
from ..adapters.pause import clear_pause, current_pause, set_pause
from ..adapters.ui import banner
from ..app.report import LeftAlone, SortOutcome, render_json, render_plan
from ..app.sorter import Sorter
from ..app.undo import undo_run
from ..app.watcher import StopRequest, Watcher
from ..domain.duration import format_duration
from .common import (
    EXIT_FAILED,
    EXIT_OK,
    load_from_args,
    make_loud,
    palette,
    source_error,
)


def _print_outcomes(
    outcomes: list[SortOutcome], *, applied: bool, left_alone: list[LeftAlone], blocked: list[str]
) -> None:
    pal = palette()
    if pal.enabled:
        print(banner(pal))
    print(
        render_plan(outcomes, applied=applied, palette=pal, left_alone=left_alone, blocked=blocked)
    )


def cmd_plan(args: argparse.Namespace) -> int:
    config = load_from_args(args)
    if source_error(config, args):
        return EXIT_FAILED
    left_alone: list[LeftAlone] = []
    outcomes = Sorter(config).sort_once(
        apply=False,
        respect_age=False,
        on_waiting=lambda path, reason: left_alone.append((path.name, reason)),
    )
    blocked = blocked_folders(config.settings, config.managed_dirs)
    if getattr(args, "json", False):
        print(render_json(outcomes, applied=False, left_alone=left_alone, blocked=blocked))
        return EXIT_OK
    _print_outcomes(outcomes, applied=False, left_alone=left_alone, blocked=blocked)
    return EXIT_OK


def cmd_run(args: argparse.Namespace) -> int:
    config = load_from_args(args)
    if source_error(config, args):
        return EXIT_FAILED
    log = file_logger(echo=args.verbose)
    loud = make_loud(log)

    def warn(message: str) -> None:
        loud(message, level="WARNING")

    if (pause := current_pause()) is not None:
        print(
            f"cubby: note: the agent is {pause.describe()}; this manual run proceeds.",
            file=sys.stderr,
        )
    sorter = Sorter(config, log=log, warn=warn, journal=Journal(), ledger=Ledger())
    left_alone: list[LeftAlone] = []
    with exclusive():
        outcomes = sorter.sort_once(
            apply=True, on_waiting=lambda path, reason: left_alone.append((path.name, reason))
        )
    blocked = blocked_folders(config.settings, config.managed_dirs)
    _print_outcomes(outcomes, applied=True, left_alone=left_alone, blocked=blocked)
    return EXIT_FAILED if any(o.needs_attention for o in outcomes) else EXIT_OK


def cmd_undo(args: argparse.Namespace) -> int:
    with exclusive():
        try:
            result = undo_run(Journal(), getattr(args, "run", None), log=print)
        except KeyError:
            print(
                f"cubby: no run {args.run!r} in the journal; see 'cubby history'", file=sys.stderr
            )
            return EXIT_FAILED
    print(f"Restored {result.restored} file(s).")
    if result.gone:
        print(
            f"cubby: {result.gone} no longer where the run put it (moved or deleted since).",
            file=sys.stderr,
        )
    if result.replaced:
        print(
            f"cubby: {result.replaced} changed or replaced since the run, left in place.",
            file=sys.stderr,
        )
    if result.failed:
        print(
            f"cubby: {len(result.failed)} file(s) could not be restored and stay pending; "
            f"run 'cubby undo --run {result.run_id}' again once the cause is fixed.",
            file=sys.stderr,
        )
        return EXIT_FAILED
    # Not every file is back: say so to scripts too (see docs/usage.md, exit codes).
    return EXIT_FAILED if result.gone or result.replaced else EXIT_OK


def cmd_watch(args: argparse.Namespace) -> int:
    config = load_from_args(args)
    # The agent waits for a folder that is not there yet (a drive mounted after
    # login) instead of exiting into a restart loop; a person is told at once.
    if not getattr(args, "wait_for_source", False) and source_error(config, args):
        return EXIT_FAILED
    # Under launchd or systemd, stdout is appended to the log file already
    # written by the logger: echo only to a person at a terminal.
    log = file_logger(echo=sys.stdout.isatty() or getattr(args, "verbose", False))
    loud = make_loud(log) if sys.stderr.isatty() else log

    def warn(message: str) -> None:
        loud(message, level="WARNING")

    ledger = Ledger()
    sorter = Sorter(config, log=log, warn=warn, journal=Journal(), ledger=ledger, mode="watch")
    # launchd and systemd stop the agent with SIGTERM. Dying on it could fall
    # between a move and its journal line; instead the pass stops between two
    # files and the loop ends. The units allow STOP_TIMEOUT for that.
    stopping = StopRequest()
    on_main_thread = threading.current_thread() is threading.main_thread()
    previous = signal.signal(signal.SIGTERM, stopping.request) if on_main_thread else None
    watcher = Watcher(
        sorter,
        config.settings.interval,
        log=loud,
        ledger=ledger,
        sleep=stopping.sleep,
        paused=_pause_reason,
        alert=notifier(config.settings.notify, warn=warn),
    )
    log(
        f"cubby watching {config.settings.source} "
        f"(delay {format_duration(config.settings.delay)}, "
        f"every {format_duration(config.settings.interval)})"
    )
    try:
        with contextlib.suppress(KeyboardInterrupt):  # Ctrl-C at a terminal
            watcher.run(stop=stopping)
    finally:
        if on_main_thread:
            signal.signal(signal.SIGTERM, previous)
    log("cubby stopped")
    return EXIT_OK


def _pause_reason() -> str | None:
    pause = current_pause()
    return pause.describe() if pause else None


def cmd_pause(args: argparse.Namespace) -> int:
    try:
        pause = set_pause(args.duration)
    except OSError as error:
        print(f"cubby: could not pause: {error}", file=sys.stderr)
        return EXIT_FAILED
    print(f"The agent is {pause.describe()}. It stops before the next file it would move.")
    print("Resume with: cubby resume")
    return EXIT_OK


def cmd_resume(args: argparse.Namespace) -> int:
    try:
        lifted = clear_pause()
    except OSError as error:
        print(f"cubby: could not resume: {error}", file=sys.stderr)
        return EXIT_FAILED
    print("Resumed: the agent sorts again at its next pass." if lifted else "Not paused.")
    return EXIT_OK
