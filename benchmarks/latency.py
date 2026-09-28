"""Latency percentiles of the paths a user or the agent waits on.

    python benchmarks/latency.py
    python benchmarks/latency.py --files 1000 --passes 30 --invocations 50 --json

``bench_sort.py`` says what a pass costs and how it grows (median and slowest of
a handful of runs); this says how the cost is spread, over samples large enough
for a percentile to mean something. Reported per path: p50, p90, p95, p99 and
the maximum, nearest-rank, with the sample size; a p99 differs from the maximum
only from 100 samples on.

- move: one file of the agent's pass, from the outcome of the file before to
  its own (eligibility, classification, the move, its journal line). The first
  file of each pass is left out: its interval also holds the folder listing.
- pass / idle pass: one pass of the agent (``Watcher``, one cycle: lock,
  journal, ledger, compaction, heartbeat) over a fresh folder of ``--files``
  settled files, each in a process of its own; then the next pass, once the
  folder is sorted.
- cubby COMMAND: the wall time of the command a person types, each invocation a
  fresh process (``python -m cubby``), so interpreter start and imports count.
  The state is a full one: a ledger of ``--ledger-runs`` runs, a journal of
  ``--journal-entries`` undoable moves, and a folder of ``--files`` files.
  Commands take turns within each round, so a change of machine load spreads
  over all of them; each round starts from the same state (a ``run`` then its
  ``undo``, which puts the folder back).

Everything runs in a throwaway folder: ``HOME``, ``CUBBY_STATE_DIR``,
``CUBBY_CONFIG`` and ``TMPDIR`` of every child point into it, and ``XDG_*`` and
other ``CUBBY_*`` variables are dropped. Times are ``time.perf_counter_ns``.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any

from bench_sort import SECONDS_PER_THOUSAND_FILES, _config, _folder_of, _throwaway_root

from cubby import __version__
from cubby.adapters.journal import Entry, Journal
from cubby.adapters.ledger import Ledger, RunRecord
from cubby.app.sorter import Sorter
from cubby.app.watcher import Watcher
from cubby.domain.engine import Engine

REPO = Path(__file__).resolve().parents[1]
PERCENTILES = (50, 90, 95, 99)
MOVES_PER_JOURNAL_RUN = 100
#: Budget per path, milliseconds at p95, set before measuring (docs/PERFORMANCE.md,
#: "Latency percentiles", says why each).
BUDGET_MS = {
    "move": 10.0,
    "pass": 10_000.0,
    "idle pass": 100.0,
    "cubby --version": 1_000.0,
    "cubby status": 1_000.0,
    "cubby history": 1_000.0,
    "cubby explain FILE": 1_000.0,
    "cubby plan": 2_000.0,
    "cubby run": 5_000.0,
    "cubby undo": 5_000.0,
}
CLI_TIMEOUT_S = 120.0


def summarize(samples: Iterable[float]) -> dict[str, Any]:
    """Sample size, nearest-rank percentiles and maximum of ``samples``."""
    ordered = sorted(samples)
    if not ordered:
        raise ValueError("no samples to summarize")
    n = len(ordered)
    summary: dict[str, Any] = {"n": n}
    for p in PERCENTILES:
        summary[f"p{p}"] = ordered[-(-p * n // 100) - 1]  # rank ceil(p n / 100), in integers
    summary["max"] = ordered[-1]
    return summary


# --- the agent's pass, one per fresh process ----------------------------------


class _StampedSorter(Sorter):
    """The agent's sorter, stamping the moment each outcome is made."""

    stamps: list[int]

    def sort_pass(self, **kwargs: Any) -> Any:
        if "on_outcome" not in kwargs:
            raise RuntimeError("the agent no longer hands on_outcome to sort_pass by keyword")
        hand_on = kwargs.pop("on_outcome")

        def stamped(outcome: Any) -> None:
            self.stamps.append(time.perf_counter_ns())
            hand_on(outcome)

        self.stamps = []
        return super().sort_pass(on_outcome=stamped, **kwargs)


def measure_pass(n: int) -> dict[str, Any]:
    """One pass of the agent over ``n`` files, then the next. Must run in a fresh process."""
    root = _throwaway_root()
    try:
        config = _config(_folder_of(n, root))
        sorter = _StampedSorter(
            config,
            Engine(config),
            journal=Journal(root / "state" / "journal.jsonl"),
            ledger=Ledger(root / "state"),
            mode="watch",
        )
        agent = Watcher(sorter, 60.0, ledger=Ledger(root / "state"))
        started = time.perf_counter_ns()
        moved = agent.run(max_cycles=1)
        pass_ns = time.perf_counter_ns() - started
        stamps = sorter.stamps
        started = time.perf_counter_ns()
        left = agent.run(max_cycles=1)
        idle_ns = time.perf_counter_ns() - started
        if moved != n or left or len(stamps) != n:
            raise RuntimeError(
                f"expected {n} moved, 0 left and {n} stamps; got {moved}, {left} and {len(stamps)}"
            )
        return {
            "pass_ns": pass_ns,
            "idle_ns": idle_ns,
            "move_ns": [after - before for before, after in pairwise(stamps)],
        }
    finally:
        shutil.rmtree(root, ignore_errors=True)


def measure_passes(files: int, passes: int, env: dict[str, str]) -> dict[str, list[int]]:
    samples: dict[str, list[int]] = {"move": [], "pass": [], "idle pass": []}
    for _ in range(passes):
        completed = subprocess.run(
            [sys.executable, __file__, "--single-pass", str(files)],
            capture_output=True,
            text=True,
            check=True,
            env=env,
            timeout=30 + files / 1000 * SECONDS_PER_THOUSAND_FILES,
        )
        one = json.loads(completed.stdout)
        samples["move"].extend(one["move_ns"])
        samples["pass"].append(one["pass_ns"])
        samples["idle pass"].append(one["idle_ns"])
    return samples


# --- the commands, each invocation a fresh process ----------------------------


def sandbox_env(root: Path) -> dict[str, str]:
    """The environment of every child: the caller's, pointed into ``root``."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("XDG_", "CUBBY_"))}
    (root / "home").mkdir(exist_ok=True)
    env.update(
        HOME=str(root / "home"),
        TMPDIR=str(root),
        CUBBY_STATE_DIR=str(root / "state"),
        CUBBY_CONFIG=str(root / "config.toml"),
    )
    return env


def build_state(root: Path, files: int, ledger_runs: int, journal_entries: int) -> Path:
    """A full state in ``root``: config, ledger, journal and a folder of files.

    Returns the folder. The last ``journal_entries / 100`` runs of the ledger
    are in the journal, 100 undoable moves each, as a user who never undid
    anything would have them; their files are long gone.
    """
    source = _folder_of(files, root)
    (root / "config.toml").write_text(
        # A JSON string is a valid TOML basic string, escapes included.
        f'[settings]\nsource = {json.dumps(str(source))}\ndelay = "0s"\ncontent_scan = false\n',
        encoding="utf-8",
    )
    state = root / "state"
    state.mkdir()
    now = datetime.now()
    run_ids = [
        f"{now - timedelta(minutes=10 * (ledger_runs - i)):%Y%m%dT%H%M%S}-{i:016x}"
        for i in range(ledger_runs)
    ]
    with (state / "runs.jsonl").open("w", encoding="utf-8") as ledger:
        for i, run_id in enumerate(run_ids):
            at = (now - timedelta(minutes=10 * (ledger_runs - i))).isoformat(timespec="seconds")
            record = RunRecord(run_id, "watch", str(source), at, at, MOVES_PER_JOURNAL_RUN, 0)
            ledger.write(json.dumps(record.to_json()) + "\n")
    journal = Journal(state / "journal.jsonl")
    gone = root / "gone"
    journaled = run_ids[-max(1, journal_entries // MOVES_PER_JOURNAL_RUN) :]
    for index in range(journal_entries):
        run_id = journaled[index // MOVES_PER_JOURNAL_RUN % len(journaled)]
        name = f"old{index:06d}.pdf"
        entry = Entry(
            run_id, index % MOVES_PER_JOURNAL_RUN, "move", gone / name, gone / "Documents" / name
        )
        journal.record(entry)
    return source


def _timed_cli(args: list[str], env: dict[str, str]) -> int:
    started = time.perf_counter_ns()
    completed = subprocess.run(
        [sys.executable, "-m", "cubby", *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=CLI_TIMEOUT_S,
        check=False,
    )
    elapsed = time.perf_counter_ns() - started
    if completed.returncode != 0:
        raise RuntimeError(
            f"cubby {' '.join(args)} exited {completed.returncode}: {completed.stderr[-2000:]}"
        )
    return elapsed


def measure_commands(root: Path, source: Path, files: int, rounds: int) -> dict[str, list[int]]:
    env = sandbox_env(root)
    state = root / "state"
    pristine = root / "pristine"
    shutil.copytree(state, pristine)
    target = str(next(source.iterdir()))
    commands: list[tuple[str, Callable[[], int]]] = [
        ("cubby --version", lambda: _timed_cli(["--version"], env)),
        # Not installed in the sandbox: status reports that and exits 0.
        ("cubby status", lambda: _timed_cli(["status"], env)),
        ("cubby history", lambda: _timed_cli(["history"], env)),
        ("cubby explain FILE", lambda: _timed_cli(["explain", target], env)),
        ("cubby plan", lambda: _timed_cli(["plan"], env)),
        ("cubby run", lambda: _timed_cli(["run"], env)),
        ("cubby undo", lambda: _timed_cli(["undo"], env)),
    ]
    samples: dict[str, list[int]] = {name: [] for name, _ in commands}
    for _ in range(rounds):
        shutil.rmtree(state)
        shutil.copytree(pristine, state)
        for name, timed in commands:
            samples[name].append(timed())
        if len(list(source.glob("*.pdf"))) != files:
            raise RuntimeError("undo did not put the folder back")
    return samples


# --- conditions ---------------------------------------------------------------


def _first_line(path: str, prefix: str) -> str:
    try:
        with Path(path).open(encoding="utf-8") as handle:
            for line in handle:
                if line.startswith(prefix):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return "unknown"


def _filesystem_of(folder: Path) -> str:
    """The type of the mount holding ``folder`` (Linux; "unknown" elsewhere)."""
    best, kind = "", "unknown"
    try:
        mounts = _read_mounts().splitlines()
    except OSError:
        return kind
    for line in mounts:
        fields = line.split()
        if len(fields) < 3:
            continue
        # /proc/mounts writes a space in a mount point as \040 (octal escapes).
        point = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), fields[1])
        if folder.is_relative_to(point) and len(point) > len(best):
            best, kind = point, fields[2]
    return f"{kind} ({best})"


def _read_mounts() -> str:
    return Path("/proc/mounts").read_text(encoding="utf-8")


def _commit() -> str:
    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(REPO), *args], capture_output=True, text=True, timeout=10, check=True
        ).stdout.strip()

    try:
        dirty = "-dirty" if git("status", "--porcelain", "--untracked-files=no") else ""
        return git("rev-parse", "--short", "HEAD") + dirty
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _load(averages: tuple[float, float, float]) -> str:
    return " / ".join(f"{a:.1f}" for a in averages) + " (1, 5, 15 min)"


def conditions(root: Path) -> dict[str, Any]:
    return {
        "machine": (
            f"{_first_line('/proc/cpuinfo', 'model name')}, {os.cpu_count()} CPUs, "
            f"{_first_line('/proc/meminfo', 'MemTotal')} RAM"
        ),
        "os": platform.platform(),
        "python": platform.python_version(),
        "filesystem": _filesystem_of(root),
        "commit": _commit(),
        "cubby": __version__,
        "date": datetime.now().isoformat(timespec="seconds"),
    }


# --- report -------------------------------------------------------------------


def report(samples: dict[str, list[int]]) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for name, values in samples.items():
        row = summarize(v / 1e6 for v in values)  # milliseconds
        row["budget"] = BUDGET_MS[name]
        row["met"] = row["p95"] <= BUDGET_MS[name]
        rows[name] = row
    return rows


def _print_table(result: dict[str, Any]) -> None:
    for key, value in result["conditions"].items():
        print(f"- {key}: {value}")
    print()
    print("| Path | n | p50 | p90 | p95 | p99 | max | Budget (p95) | Met |")
    print("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |")
    for name, row in result["paths"].items():
        cells = " | ".join(_ms(row[k]) for k in ("p50", "p90", "p95", "p99", "max"))
        met = "yes" if row["met"] else "**no**"
        print(f"| {name} | {row['n']:,} | {cells} | {_ms(row['budget'])} | {met} |")


def _ms(value: float) -> str:
    return f"{value:,.2f} ms" if value < 1000 else f"{value / 1000:,.2f} s"


def run(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(tempfile.mkdtemp(prefix="cubby-latency-"))
    try:
        env = sandbox_env(root)
        load_before = os.getloadavg()
        samples = measure_passes(args.files, args.passes, env)
        cli_root = root / "cli"
        cli_root.mkdir()
        source = build_state(cli_root, args.files, args.ledger_runs, args.journal_entries)
        journal_mb = (cli_root / "state" / "journal.jsonl").stat().st_size / 1e6
        samples.update(measure_commands(cli_root, source, args.files, args.invocations))
        found = conditions(root)
        found.update(
            load_before=_load(load_before),
            load_after=_load(os.getloadavg()),
            state=(
                f"{args.files} files, ledger of {args.ledger_runs} runs, journal of "
                f"{args.journal_entries} undoable moves ({journal_mb:.1f} MB)"
            ),
        )
        return {"conditions": found, "paths": report(samples)}
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--files", type=int, default=1_000, help="files per folder (1000)")
    parser.add_argument("--passes", type=int, default=30, help="fresh agent passes (30)")
    parser.add_argument(
        "--invocations", type=int, default=50, help="invocations of each command (50)"
    )
    parser.add_argument("--ledger-runs", type=int, default=2_000, help="runs in the ledger")
    parser.add_argument("--journal-entries", type=int, default=50_000, help="journal moves")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    parser.add_argument("--single-pass", type=int, default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.single_pass is not None:
        print(json.dumps(measure_pass(args.single_pass)))
        return 0
    result = run(args)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        _print_table(result)
    return 0


if __name__ == "__main__":  # pragma: no cover - a script, not an import
    raise SystemExit(main())
