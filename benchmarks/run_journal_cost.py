"""What a large journal adds to ``cubby run``: the probe behind a finding.

    python benchmarks/run_journal_cost.py
    python benchmarks/run_journal_cost.py --rounds 10 --files 1000 --journal-entries 50000

In the state ``latency.py`` builds (a folder of files, a full ledger, a journal
of undoable moves), and in the same sandbox:

1. ``cubby run`` then ``cubby undo``, ``--rounds`` times with the journal as
   built and as many times with it removed, alternating, each round from a copy
   of the same state: the median and slowest wall time of ``run`` for each;
2. one ``cubby run`` under ``cProfile``: the cumulative time of the pass, of
   journal compaction, of the moves and of the log line written per move.
   The profiler's overhead inflates every figure: compare them to each other.
"""

from __future__ import annotations

import argparse
import pstats
import shutil
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

from latency import _timed_cli, build_state, sandbox_env

#: (file suffix, function) of the profiled calls reported, and their label.
PROFILED = {
    ("cubby/app/sorter.py", "sort_pass"): "the pass (sort_pass)",
    ("cubby/adapters/journal.py", "compact"): "journal compaction (Journal.compact)",
    ("cubby/adapters/filesystem.py", "move_into"): "the moves (move_into)",
    ("cubby/adapters/logging.py", "log"): "the log line per move (logging.log)",
}


def compare(root: Path, rounds: int) -> dict[str, list[float]]:
    env = sandbox_env(root)
    state, pristine = root / "state", root / "pristine"
    shutil.copytree(state, pristine)
    seconds: dict[str, list[float]] = {"journal as built": [], "journal removed": []}
    for _ in range(rounds):
        for label, times in seconds.items():
            shutil.rmtree(state)
            shutil.copytree(pristine, state)
            if label == "journal removed":
                (state / "journal.jsonl").unlink()
            times.append(_timed_cli(["run"], env) / 1e9)
            _timed_cli(["undo"], env)
    shutil.rmtree(state)
    shutil.copytree(pristine, state)
    return seconds


def profile(root: Path) -> dict[str, float]:
    out = root / "run.prof"
    subprocess.run(
        [sys.executable, "-m", "cProfile", "-o", str(out), "-m", "cubby", "run"],
        env=sandbox_env(root),
        capture_output=True,
        check=True,
        timeout=600,
    )
    stats = pstats.Stats(str(out)).stats
    found: dict[str, float] = {}
    for (filename, _, function), (*_, cumulative, _callers) in stats.items():
        for (suffix, name), label in PROFILED.items():
            if function == name and filename.endswith(suffix):
                found[label] = found.get(label, 0.0) + cumulative
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rounds", type=int, default=10)
    parser.add_argument("--files", type=int, default=1_000)
    parser.add_argument("--ledger-runs", type=int, default=2_000)
    parser.add_argument("--journal-entries", type=int, default=50_000)
    args = parser.parse_args(argv)
    root = Path(tempfile.mkdtemp(prefix="cubby-run-journal-"))
    try:
        build_state(root, args.files, args.ledger_runs, args.journal_entries)
        size = (root / "state" / "journal.jsonl").stat().st_size / 1e6
        print(
            f"cubby run of {args.files} files, journal of {args.journal_entries} moves "
            f"({size:.1f} MB), {args.rounds} rounds:"
        )
        for label, times in compare(root, args.rounds).items():
            print(f"  {label}: median {statistics.median(times):.2f} s, slowest {max(times):.2f} s")
        print("one cubby run under cProfile, cumulative:")
        found = profile(root)
        for label in PROFILED.values():
            print(f"  {label}: {found.get(label, 0.0):.2f} s")
    finally:
        shutil.rmtree(root, ignore_errors=True)
    return 0


if __name__ == "__main__":  # pragma: no cover - a script, not an import
    raise SystemExit(main())
