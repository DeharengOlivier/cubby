"""Watch mode: poll the source folder forever and sort what has settled.

A poll loop (rather than OS-specific file events) keeps cubby portable across
any Unix. The delay setting means a file is only moved once it has stopped
changing, so an in-flight download is never grabbed mid-write. ``sleep`` and
``stop`` are injected to keep the loop unit-testable without real time.

The loop outlives any single pass. A pass that fails (the folder is on a drive
that was unplugged, another cubby holds the lock, a bug) is logged as an error
and the next pass runs on schedule: the agent's job is to keep going, and to
leave a trace of every time it could not.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Protocol

from ..adapters.journal import new_run_id
from ..adapters.ledger import Ledger, PassMetrics
from ..adapters.lock import Busy, exclusive
from ..adapters.logging import Level, run_context
from .report import SortOutcome
from .sorter import Sorter, describe_error

Sleep = Callable[[float], None]
Stop = Callable[[], bool]
Paused = Callable[[], str | None]  # why passes are suspended, or None
Alert = Callable[[str], None]

#: How many failed file names are remembered, so each is announced once.
_MAX_ALERTED = 1000


class LevelLog(Protocol):
    def __call__(self, message: str, *, level: Level = "INFO") -> None: ...


def _never() -> bool:
    return False


def _quiet(message: str, *, level: Level = "INFO") -> None:
    return None


class StopRequest:
    """A stop asked for by a signal, and a sleep that notices it.

    The handler only sets an attribute. It takes no lock, so it cannot deadlock
    against the code it interrupts (``threading.Event.set`` from a handler can,
    when the signal lands while the main thread holds the event's lock). The
    sleep wakes every ``tick`` seconds to look at the flag.
    """

    def __init__(self, *, tick: float = 0.5, nap: Sleep = time.sleep) -> None:
        self.requested = False
        self._tick = tick
        self._nap = nap

    def request(self, *_: object) -> None:
        self.requested = True

    def __call__(self) -> bool:
        return self.requested

    def sleep(self, seconds: float, *, clock: Callable[[], float] = time.monotonic) -> None:
        end = clock() + seconds
        while not self.requested:
            left = end - clock()
            if left <= 0:
                return
            self._nap(min(self._tick, left))


def _not_paused() -> str | None:
    return None


def _no_alert(_: str) -> None:
    return None


class Watcher:
    # Every collaborator is an injected port (clock, log, ledger, pause switch,
    # alerts) so the loop is tested without real time, files or notifications.
    def __init__(  # noqa: PLR0913
        self,
        sorter: Sorter,
        interval: float,
        *,
        sleep: Sleep = time.sleep,
        log: LevelLog = _quiet,
        ledger: Ledger | None = None,
        lock_timeout: float = 30.0,
        paused: Paused = _not_paused,
        alert: Alert = _no_alert,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._sorter = sorter
        self._interval = interval
        self._sleep = sleep
        self._log = log
        self._ledger = ledger
        self._lock_timeout = lock_timeout
        self._source_missing = False
        self._stop: Stop = _never
        self._paused = paused
        self._was_paused = False
        self._alert = alert
        self._failing = False
        self._alerted: set[str] = set()
        self._clock = clock

    def run(self, *, stop: Stop = _never, max_cycles: int | None = None) -> int:
        """Run the poll loop. Returns the number of items sorted in total.

        ``max_cycles`` bounds the loop for tests; ``stop`` lets a caller break
        out cleanly between cycles (e.g. on a signal).
        """
        self._stop = stop
        total = 0
        cycles = 0
        while True:
            total += self._cycle()
            cycles += 1
            if max_cycles is not None and cycles >= max_cycles:
                break
            if stop():
                break
            self._sleep(self._interval)
            if stop():  # a stop that woke the sleep: no new pass after it
                break
        return total

    def _cycle(self) -> int:
        """One pass. Never raises: a failed pass is logged and the loop goes on."""
        if self._is_paused():
            self._beat()  # alive and deliberately idle, not stalled
            return 0
        if not self._source_present():
            return 0
        # The pass's run id tags every line it logs, including the summary and
        # a failure logged here, so `cubby log --run ID` shows the whole pass.
        run_id = new_run_id()
        with run_context(run_id):
            return self._sort_pass(run_id)

    def _sort_pass(self, run_id: str) -> int:
        waiting = 0

        def count_waiting(_: object, __: str) -> None:
            nonlocal waiting
            waiting += 1

        try:
            with exclusive(timeout=self._lock_timeout):
                started = self._clock()
                outcomes = self._sorter.sort_once(
                    apply=True, stop=self._stop_or_pause, run_id=run_id, on_waiting=count_waiting
                )
                seconds = self._clock() - started
        except Busy as exc:
            self._log(f"pass skipped: {exc}", level="WARNING")
            return 0
        except Exception as exc:  # noqa: BLE001 - logged, and the agent keeps its schedule
            error = describe_error(exc)
            self._log(f"pass failed: {error}", level="ERROR")
            if not self._failing:
                self._alert(f"Sorting failed: {error}. See 'cubby status'.")
            self._failing = True
            return 0
        self._failing = False
        self._announce(outcomes)
        self._beat(
            PassMetrics(
                seconds=round(seconds, 3),
                moved=sum(1 for o in outcomes if o.moved_to is not None),
                failed=sum(1 for o in outcomes if o.error is not None),
                waiting=waiting,
            )
        )
        return sum(1 for o in outcomes if o.error is None)

    def _pause_reason(self) -> str | None:
        """The pause in force. A check that fails counts as a pause (fail closed)."""
        try:
            return self._paused()
        except Exception as exc:  # noqa: BLE001 - when in doubt, do not move files
            return f"paused (pause check failed: {describe_error(exc)})"

    def _stop_or_pause(self) -> bool:
        """Checked before each file: a stop or a pause ends the pass there."""
        return self._stop() or self._pause_reason() is not None

    def _is_paused(self) -> bool:
        """True while a pause is in force, said once when it starts and ends."""
        reason = self._pause_reason()
        if reason and not self._was_paused:
            self._log(f"{reason}: passes skipped until 'cubby resume'")
        elif not reason and self._was_paused:
            self._log("resumed")
        self._was_paused = reason is not None
        return self._was_paused

    def _source_present(self) -> bool:
        """False while the folder is absent (an unplugged drive), said once per outage."""
        present = self._sorter.source.is_dir()
        if not present and not self._source_missing:
            self._log(f"source folder missing: {self._sorter.source}", level="ERROR")
            self._alert(f"The folder {self._sorter.source} is missing; cubby waits for it.")
        elif present and self._source_missing:
            self._log(f"source folder back: {self._sorter.source}")
        self._source_missing = not present
        return present

    def _beat(self, last_pass: PassMetrics | None = None) -> None:
        if self._ledger is None:
            return
        try:
            self._ledger.beat(self._sorter.source, self._interval, last_pass)
        except OSError as exc:
            self._log(f"could not write the heartbeat ({exc})", level="WARNING")

    def _announce(self, outcomes: list[SortOutcome]) -> None:
        sorted_count = sum(1 for o in outcomes if o.error is None)
        failed = len(outcomes) - sorted_count
        if sorted_count:
            self._log(f"sorted {sorted_count} item(s)")
        if failed:
            self._log(f"{failed} item(s) could not be sorted", level="WARNING")
        self._alert_new_failures(outcomes)

    def _alert_new_failures(self, outcomes: list[SortOutcome]) -> None:
        """Announce each file that cannot be sorted once, not at every pass."""
        failing = [o.name for o in outcomes if o.error]
        new = [name for name in failing if name not in self._alerted]
        # Remember only what fails now: a file that recovers and fails again
        # is announced again, and the set never outgrows one pass.
        self._alerted = set(failing[:_MAX_ALERTED])
        if not new:
            return
        shown = ", ".join(new[:3]) + (f" and {len(new) - 3} more" if len(new) > 3 else "")
        self._alert(f"Could not sort {shown}. See 'cubby status'.")
