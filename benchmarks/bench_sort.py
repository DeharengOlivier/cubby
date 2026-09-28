"""Measure what sorting a folder costs, so the README numbers can be re-run.

    python benchmarks/bench_sort.py
    python benchmarks/bench_sort.py 500 5000 --repeat 7

Each size is measured ``--repeat`` times, each time in a process of its own on a
fresh folder, because peak resident memory is a high-water mark and because an
applied sort leaves nothing to sort again. The table reports the median and the
slowest repetition (the p100: with a handful of samples a p90 or p99 is the
maximum anyway).

Three timings per repetition:

- plan: ``cubby plan`` over the folder, nothing moved;
- apply: one pass of the agent (``cubby watch``) that moves every file, journal
  and ledger included, in wall clock and in CPU time of the process (user +
  system), which a busy machine disturbs far less than the wall clock;
- idle: the next pass of the agent, once the folder is sorted, which is what the
  agent pays every interval in the steady state.

Two memory figures, each the growth of the peak resident set of a process of
its own over what it held before the phase: the plan (``cubby plan``, which
keeps every outcome to print it) and the agent's pass (apply). They are taken in
separate processes because the memory a plan frees stays resident and would be
reused by the pass after it, hiding what the pass itself needs.
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
from cubby.app.watcher import Watcher
from cubby.domain.category import Category, Config, Settings
from cubby.domain.engine import Engine

DEFAULT_SIZES = (1_000, 10_000, 20_000)
DEFAULT_REPEAT = 5
#: Budget per repetition: generous for 400 000 files on a busy disk, finite so a
#: hang fails the benchmark instead of blocking it.
SECONDS_PER_THOUSAND_FILES = 6.0


def peak_rss_mb() -> float:
    """The process's peak resident set since it started, in MB."""
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


def _config(source: Path) -> Config:
    return Config(
        settings=Settings(source=source, delay=0, content_scan=False),
        categories=(Category(name="Documents", extensions=frozenset({"pdf"}), strong_ext=True),),
    )


def _throwaway_root() -> Path:
    root = Path(tempfile.mkdtemp())
    # The agent's lock and anything else resolved from the state folder stay in
    # the throwaway root, never in the user's own state.
    os.environ["CUBBY_STATE_DIR"] = str(root / "state")
    return root


def measure_plan(n: int) -> dict[str, float]:
    """``cubby plan`` over ``n`` files. Must run in a fresh process."""
    root = _throwaway_root()
    try:
        config = _config(_folder_of(n, root))
        before = peak_rss_mb()
        started = time.perf_counter()
        planned = len(Sorter(config).sort_once(apply=False))
        plan_s = time.perf_counter() - started
        if planned != n:
            raise RuntimeError(f"expected {n} planned, got {planned}")
        return {"plan_s": plan_s, "plan_mb": peak_rss_mb() - before}
    finally:
        shutil.rmtree(root, ignore_errors=True)


def measure_pass(n: int) -> dict[str, float]:
    """One pass of the agent over ``n`` files, then the next. Must run in a fresh process."""
    root = _throwaway_root()
    try:
        config = _config(_folder_of(n, root))
        # The real agent journals every move and records every pass: both are
        # part of the cost being measured, and the pass is the agent's own.
        sorter = Sorter(
            config,
            Engine(config),
            journal=Journal(root / "state" / "journal.jsonl"),
            ledger=Ledger(root / "state"),
            mode="watch",
        )
        agent = Watcher(sorter, 60.0, ledger=Ledger(root / "state"))

        before = peak_rss_mb()
        started, cpu_started = time.perf_counter(), time.process_time()
        moved = agent.run(max_cycles=1)
        apply_s = time.perf_counter() - started
        apply_cpu_s = time.process_time() - cpu_started
        pass_mb = peak_rss_mb() - before

        started = time.perf_counter()
        left = agent.run(max_cycles=1)
        idle_s = time.perf_counter() - started

        if moved != n or left:
            raise RuntimeError(f"expected {n} moved and 0 left; got {moved}, {left}")
        return {
            "apply_s": apply_s,
            "apply_cpu_s": apply_cpu_s,
            "idle_s": idle_s,
            "pass_mb": pass_mb,
        }
    finally:
        shutil.rmtree(root, ignore_errors=True)


def measure_in_fresh_processes(n: int) -> dict[str, float]:
    result: dict[str, float] = {}
    for phase in ("plan", "pass"):
        completed = subprocess.run(
            [sys.executable, __file__, f"--single-{phase}", str(n)],
            capture_output=True,
            text=True,
            check=True,
            timeout=30 + n / 1000 * SECONDS_PER_THOUSAND_FILES,
        )
        result.update(json.loads(completed.stdout))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sizes", nargs="*", type=int, default=list(DEFAULT_SIZES))
    parser.add_argument("--repeat", type=int, default=DEFAULT_REPEAT)
    parser.add_argument("--single-plan", type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--single-pass", type=int, default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.single_plan is not None:
        print(json.dumps(measure_plan(args.single_plan)))
        return 0
    if args.single_pass is not None:
        print(json.dumps(measure_pass(args.single_pass)))
        return 0

    print(f"Median / slowest of {args.repeat} runs, each in fresh processes on fresh folders.")
    print("Memory: the largest of the repetitions.")
    print()
    print(
        "| Files | Plan | Apply | Apply CPU | CPU per file | Idle pass "
        "| Plan memory | Pass memory |"
    )
    print("| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for n in sorted(args.sizes):
        rows = [measure_in_fresh_processes(n) for _ in range(args.repeat)]
        cpu = statistics.median(r["apply_cpu_s"] for r in rows)
        print(
            f"| {n:,} | {_spread(rows, 'plan_s')} | {_spread(rows, 'apply_s')} | "
            f"{_spread(rows, 'apply_cpu_s')} | {cpu / n * 1e6:.0f} us | "
            f"{_spread(rows, 'idle_s')} | {max(r['plan_mb'] for r in rows):.0f} MB | "
            f"{max(r['pass_mb'] for r in rows):.0f} MB |"
        )
    return 0


def _spread(rows: list[dict[str, float]], key: str) -> str:
    values = [row[key] for row in rows]
    return f"{statistics.median(values):.2f} / {max(values):.2f} s"


if __name__ == "__main__":  # pragma: no cover - a script, not an import
    raise SystemExit(main())
