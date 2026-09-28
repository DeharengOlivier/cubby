"""Measure what sorting a folder costs, so the README numbers can be re-run.

    python benchmarks/bench_sort.py
    python benchmarks/bench_sort.py 500 5000 --repeat 7

Each size is measured ``--repeat`` times, each time in a process of its own on a
fresh folder, because peak resident memory is a high-water mark that never
comes back down and because an applied sort leaves nothing to sort again. The
table reports the median and the slowest repetition (the p100: with a handful of
samples a p90 or p99 is the maximum anyway).

Three timings per repetition:

- plan: ``cubby plan`` over the folder, nothing moved;
- apply: one pass that moves every file, journal and ledger included;
- idle: the next pass of the agent, once the folder is sorted, which is what the
  agent pays every interval in the steady state.
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from cubby.adapters.journal import Journal
from cubby.adapters.ledger import Ledger
from cubby.app.sorter import Sorter
from cubby.domain.category import Category, Config, Settings
from cubby.domain.engine import Engine

DEFAULT_SIZES = (1_000, 10_000, 20_000)
DEFAULT_REPEAT = 5
#: Budget per repetition: generous for 200 000 files on a laptop disk, finite so a
#: hang fails the benchmark instead of blocking it.
SECONDS_PER_THOUSAND_FILES = 6.0


def peak_rss_mb() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024


def _folder_of(n: int, root: Path) -> Path:
    """A folder of ``n`` settled files, aged past any eligibility delay."""
    source = root / "downloads"
    source.mkdir(parents=True)
    aged = time.time() - 10_000
    for index in range(n):
        path = source / f"file{index:06d}.pdf"
        path.write_text("content", encoding="utf-8")
        os.utime(path, (aged, aged))
    return source


def measure(n: int) -> dict[str, float]:
    """Plan and then apply a sort of ``n`` files. Must run in a fresh process."""
    root = Path(tempfile.mkdtemp())
    try:
        source = _folder_of(n, root)
        config = Config(
            settings=Settings(source=source, delay=0, content_scan=False),
            categories=(
                Category(name="Documents", extensions=frozenset({"pdf"}), strong_ext=True),
            ),
        )
        # The real agent journals every move and records every pass: both are
        # part of the cost being measured.
        sorter = Sorter(
            config,
            Engine(config),
            journal=Journal(root / "state" / "journal.jsonl"),
            ledger=Ledger(root / "state"),
        )

        before = peak_rss_mb()
        started = time.perf_counter()
        planned = sorter.sort_once(apply=False)
        plan_s = time.perf_counter() - started

        started = time.perf_counter()
        applied = sorter.sort_once(apply=True)
        apply_s = time.perf_counter() - started

        started = time.perf_counter()
        left = sorter.sort_once(apply=True)
        idle_s = time.perf_counter() - started

        moved = sum(1 for outcome in applied if outcome.moved_to is not None)
        if len(planned) != n or moved != n or left:
            raise RuntimeError(
                f"expected {n} planned and moved, 0 left; got {len(planned)}, {moved}, {len(left)}"
            )
        return {
            "n": n,
            "plan_s": plan_s,
            "apply_s": apply_s,
            "idle_s": idle_s,
            "memory_mb": peak_rss_mb() - before,
        }
    finally:
        shutil.rmtree(root, ignore_errors=True)


def measure_in_a_fresh_process(n: int) -> dict[str, float]:
    completed = subprocess.run(
        [sys.executable, __file__, "--single", str(n)],
        capture_output=True,
        text=True,
        check=True,
        timeout=30 + n / 1000 * SECONDS_PER_THOUSAND_FILES,
    )
    result: dict[str, float] = json.loads(completed.stdout)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sizes", nargs="*", type=int, default=list(DEFAULT_SIZES))
    parser.add_argument("--repeat", type=int, default=DEFAULT_REPEAT)
    parser.add_argument("--single", type=int, default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.single is not None:
        print(json.dumps(measure(args.single)))
        return 0

    print(f"Median / slowest of {args.repeat} runs, each in a fresh process on a fresh folder.")
    print()
    print("| Files | Plan | Apply | Apply per file | Idle pass | Peak memory |")
    print("| ---: | ---: | ---: | ---: | ---: | ---: |")
    for n in sorted(args.sizes):
        rows = [measure_in_a_fresh_process(n) for _ in range(args.repeat)]
        print(
            f"| {n:,} | {_spread(rows, 'plan_s')} | {_spread(rows, 'apply_s')} | "
            f"{statistics.median(r['apply_s'] for r in rows) / n * 1e6:.0f} us | "
            f"{_spread(rows, 'idle_s')} | {max(r['memory_mb'] for r in rows):.0f} MB |"
        )
    return 0


def _spread(rows: list[dict[str, float]], key: str) -> str:
    values = [row[key] for row in rows]
    return f"{statistics.median(values):.2f} / {max(values):.2f} s"


if __name__ == "__main__":  # pragma: no cover - a script, not an import
    raise SystemExit(main())
