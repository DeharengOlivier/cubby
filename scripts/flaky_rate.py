"""Measure how often CI fails on a commit that later passes unchanged.

A flaky test shows up in CI as a commit whose checks both failed and passed:
a rerun that went green, or a second run of the same commit. This counts those
commits among every commit CI ran on in the last ``--days`` days, and fails
when the rate is over budget or when it measured nothing at all.

    uv run --locked python scripts/flaky_rate.py              # asks gh
    python scripts/flaky_rate.py runs.json                    # a saved answer

The runs list gives only each run's latest attempt, so the earlier attempts of
a rerun run are fetched one by one (reruns are rare). A failure that a later
commit "fixes" is never counted: telling a real defect from a flake hidden by
an unrelated change is a judgement this script does not make.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

REPO = "DeharengOlivier/cubby"
WORKFLOW = "CI"
#: The rate the register accepts (docs/READINESS.md). One flaky commit in fifty.
BUDGET = 0.02
DAYS = 90
#: Conclusions that mean the checks did not pass. A cancelled run (a newer
#: push superseded it) says nothing either way and is left out.
FAILED = frozenset({"failure", "timed_out", "startup_failure"})

Attempts = Callable[[Mapping[str, Any]], list[str]]


class MeasureError(Exception):
    """Nothing trustworthy could be measured."""


@dataclass(frozen=True)
class FlakyReport:
    commits: int
    flaky: tuple[str, ...]

    @property
    def rate(self) -> float:
        return len(self.flaky) / self.commits if self.commits else 0.0


def _no_earlier_attempts(_run: Mapping[str, Any]) -> list[str]:
    return []


def measure(
    runs: Iterable[Mapping[str, Any]],
    earlier_attempts: Attempts = _no_earlier_attempts,
    workflow: str = WORKFLOW,
) -> FlakyReport:
    """Commits of ``workflow`` whose runs or attempts both failed and passed."""
    outcomes: dict[str, set[str]] = defaultdict(set)
    for run in runs:
        if run.get("name") != workflow or run.get("status") != "completed":
            continue
        sha = str(run["head_sha"])
        conclusions = [run.get("conclusion")]
        if int(run.get("run_attempt", 1)) > 1:
            conclusions += earlier_attempts(run)
        for conclusion in conclusions:
            if conclusion == "success":
                outcomes[sha].add("passed")
            elif conclusion in FAILED:
                outcomes[sha].add("failed")
    flaky = sorted(sha for sha, seen in outcomes.items() if seen == {"passed", "failed"})
    return FlakyReport(commits=len(outcomes), flaky=tuple(flaky))


def _gh(args: list[str]) -> str:
    try:
        return subprocess.run(
            ["gh", *args], capture_output=True, text=True, check=True, timeout=120
        ).stdout
    except subprocess.CalledProcessError as exc:
        raise MeasureError(f"gh {args[1]} failed: {exc.stderr.strip()}") from exc
    except subprocess.TimeoutExpired as exc:
        raise MeasureError(f"gh {args[1]} timed out after {exc.timeout} s") from exc


def runs_from_gh(repo: str, since: datetime, gh: Callable[[list[str]], str] = _gh) -> list[Any]:
    query = f"repos/{repo}/actions/runs?per_page=100&created=>={since:%Y-%m-%d}"
    out = gh(["api", query, "--paginate", "-q", ".workflow_runs[]"])
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def attempts_from_gh(repo: str, gh: Callable[[list[str]], str] = _gh) -> Attempts:
    def earlier(run: Mapping[str, Any]) -> list[str]:
        return [
            gh(
                ["api", f"repos/{repo}/actions/runs/{run['id']}/attempts/{n}", "-q", ".conclusion"]
            ).strip()
            for n in range(1, int(run["run_attempt"]))
        ]

    return earlier


def runs_from_file(path: Path) -> list[Any]:
    data = json.loads(path.read_text("utf-8"))
    if isinstance(data, dict):
        if "workflow_runs" not in data:
            raise MeasureError(f"{path}: no workflow_runs in this object")
        data = data["workflow_runs"]
    if not isinstance(data, list):
        raise MeasureError(f"{path}: expected a list of runs or a workflow_runs object")
    return data


def main(argv: list[str] | None = None, gh: Callable[[list[str]], str] = _gh) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("runs", nargs="?", type=Path, help="runs JSON (default: ask gh)")
    parser.add_argument("--repo", default=REPO)
    parser.add_argument("--budget", type=float, default=BUDGET)
    parser.add_argument("--days", type=int, default=DAYS)
    args = parser.parse_args(argv)
    try:
        if args.runs:
            report = measure(runs_from_file(args.runs))
        else:
            since = datetime.now(UTC) - timedelta(days=args.days)
            runs = runs_from_gh(args.repo, since, gh)
            report = measure(runs, attempts_from_gh(args.repo, gh))
        if report.commits == 0:
            raise MeasureError(f"no completed {WORKFLOW} run found: nothing was measured")
    except MeasureError as exc:
        print(f"flaky-rate: {exc}", file=sys.stderr)
        return 2
    print(
        f"{WORKFLOW}: {len(report.flaky)} flaky commit(s) of {report.commits} "
        f"({report.rate:.1%}, budget {args.budget:.0%})"
    )
    for sha in report.flaky:
        print(f"  flaky: {sha}")
    if report.rate > args.budget:
        print("flaky-rate: over budget", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
