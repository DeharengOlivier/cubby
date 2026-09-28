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

from ..adapters.ledger import Ledger
from ..adapters.lock import Busy, exclusive
from .report import SortOutcome
from .sorter import Sorter, describe_error

Sleep = Callable[[float], None]
Stop = Callable[[], bool]


class LevelLog(Protocol):
    def __call__(self, message: str, *, level: str = "INFO") -> None: ...


def _never() -> bool:
    return False


def _quiet(message: str, *, level: str = "INFO") -> None:
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


class Watcher:
    def __init__(
        self,
        sorter: Sorter,
        interval: float,
        *,
        sleep: Sleep = time.sleep,
        log: LevelLog = _quiet,
        ledger: Ledger | None = None,
        lock_timeout: float = 30.0,
    ):
        self._sorter = sorter
        self._interval = interval
        self._sleep = sleep
        self._log = log
        self._ledger = ledger
        self._lock_timeout = lock_timeout
        self._source_missing = False
        self._stop: Stop = _never

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
        return total

    def _cycle(self) -> int:
        """One pass. Never raises: a failed pass is logged and the loop goes on."""
        if not self._source_present():
            return 0
        try:
            with exclusive(timeout=self._lock_timeout):
                outcomes = self._sorter.sort_once(apply=True, stop=self._stop)
        except Busy as exc:
            self._log(f"pass skipped: {exc}", level="WARNING")
            return 0
        except Exception as exc:  # noqa: BLE001 - logged, and the agent keeps its schedule
            self._log(f"pass failed: {describe_error(exc)}", level="ERROR")
            return 0
        self._announce(outcomes)
        self._beat()
        return sum(1 for o in outcomes if o.error is None)

    def _source_present(self) -> bool:
        """False while the folder is absent (an unplugged drive), said once per outage."""
        present = self._sorter.source.is_dir()
        if not present and not self._source_missing:
            self._log(f"source folder missing: {self._sorter.source}", level="ERROR")
        elif present and self._source_missing:
            self._log(f"source folder back: {self._sorter.source}")
        self._source_missing = not present
        return present

    def _beat(self) -> None:
        if self._ledger is None:
            return
        try:
            self._ledger.beat(self._sorter.source, self._interval)
        except OSError as exc:
            self._log(f"could not write the heartbeat ({exc})", level="WARNING")

    def _announce(self, outcomes: list[SortOutcome]) -> None:
        sorted_count = sum(1 for o in outcomes if o.error is None)
        failed = len(outcomes) - sorted_count
        if sorted_count:
            self._log(f"sorted {sorted_count} item(s)")
        if failed:
            self._log(f"{failed} item(s) could not be sorted", level="WARNING")
