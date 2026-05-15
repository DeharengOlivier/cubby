"""Watch mode: poll the source folder forever and sort what has settled.

A poll loop (rather than OS-specific file events) keeps cubby portable across
any Unix. The delay setting means a file is only moved once it has stopped
changing, so an in-flight download is never grabbed mid-write. ``sleep`` and
``stop`` are injected to keep the loop unit-testable without real time.
"""
from __future__ import annotations

import time
from collections.abc import Callable

from .report import SortOutcome
from .sorter import Sorter

Sleep = Callable[[float], None]
Stop = Callable[[], bool]


def _never() -> bool:
    return False


class Watcher:
    def __init__(
        self,
        sorter: Sorter,
        interval: float,
        *,
        sleep: Sleep = time.sleep,
        log: Callable[[str], None] = lambda _: None,
    ):
        self._sorter = sorter
        self._interval = interval
        self._sleep = sleep
        self._log = log

    def run(self, *, stop: Stop = _never, max_cycles: int | None = None) -> int:
        """Run the poll loop. Returns the number of items sorted in total.

        ``max_cycles`` bounds the loop for tests; ``stop`` lets a caller break
        out cleanly between cycles (e.g. on a signal).
        """
        total = 0
        cycles = 0
        while True:
            outcomes = self._sorter.sort_once(apply=True)
            total += len(outcomes)
            self._announce(outcomes)

            cycles += 1
            if max_cycles is not None and cycles >= max_cycles:
                break
            if stop():
                break
            self._sleep(self._interval)
        return total

    def _announce(self, outcomes: list[SortOutcome]) -> None:
        if outcomes:
            self._log(f"sorted {len(outcomes)} item(s)")
