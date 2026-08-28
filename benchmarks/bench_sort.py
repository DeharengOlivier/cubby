"""Measure what sorting a folder costs, so the README numbers can be re-run.

    python benchmarks/bench_sort.py
    python benchmarks/bench_sort.py 500 5000

Each size is measured in a process of its own, because peak resident memory is a
high-water mark that never comes back down: measuring three sizes in one process
reports the second and third as free.
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from cubby.app.sorter import Sorter
from cubby.domain.category import Category, Config, Settings
from cubby.domain.engine import Engine

DEFAULT_SIZES = (1_000, 5_000, 20_000)


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
        sorter = Sorter(config, Engine(config))

        before = peak_rss_mb()
        started = time.perf_counter()
        planned = sorter.sort_once(apply=False)
        plan_s = time.perf_counter() - started

        started = time.perf_counter()
        sorter.sort_once(apply=True)
        apply_s = time.perf_counter() - started

        return {
            "n": n,
            "plan_s": plan_s,
            "apply_s": apply_s,
            "memory_mb": peak_rss_mb() - before,
            "planned": len(planned),
        }
    finally:
        shutil.rmtree(root, ignore_errors=True)


def measure_in_a_fresh_process(n: int) -> dict[str, float]:
    completed = subprocess.run(
        [sys.executable, __file__, "--single", str(n)],
        capture_output=True,
        text=True,
        check=True,
    )
    result: dict[str, float] = json.loads(completed.stdout)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sizes", nargs="*", type=int, default=list(DEFAULT_SIZES))
    parser.add_argument("--single", type=int, default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.single is not None:
        print(json.dumps(measure(args.single)))
        return 0

    print("| Files | Plan | Apply | Memory |")
    print("| --- | --- | --- | --- |")
    for n in sorted(args.sizes):
        row = measure_in_a_fresh_process(n)
        print(
            f"| {int(row['n']):,} | {row['plan_s']:.2f} s | "
            f"{row['apply_s']:.2f} s | ~{row['memory_mb']:.0f} MB |"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover - a script, not an import
    raise SystemExit(main())
