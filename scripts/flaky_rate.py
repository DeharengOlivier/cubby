"""Measure how often CI fails on a commit that later passes unchanged.

A flaky test shows up in CI as a commit whose checks both failed and passed:
a rerun that went green, or a second run of the same commit. This counts those
commits among every commit CI ran on, and fails when the rate is over budget.

    uv run --locked python scripts/flaky_rate.py              # asks gh
    gh api ... --paginate > runs.json && python scripts/flaky_rate.py runs.json

It reads workflow runs (``gh api repos/OWNER/REPO/actions/runs``), including
every attempt of a rerun run, so it needs a token that can read Actions.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO = "DeharengOlivier/cubby"
WORKFLOW = "CI"
#: The rate the register accepts (docs/READINESS.md). One flaky commit in fifty.
BUDGET = 0.02


@dataclass(frozen=True)
class FlakyReport:
    commits: int
    flaky: tuple[str, ...]

    @property
    def rate(self) -> float:
        return len(self.flaky) / self.commits if self.commits else 0.0


def measure(runs: Iterable[Mapping[str, Any]], workflow: str = WORKFLOW) -> FlakyReport:
    """Commits of ``workflow`` whose runs or attempts both failed and succeeded."""
    outcomes: dict[str, set[str]] = defaultdict(set)
    for run in runs:
        if run.get("name") != workflow or run.get("status") != "completed":
            continue
        conclusion = run.get("conclusion")
        if conclusion in {"success", "failure"}:
            outcomes[str(run["head_sha"])].add(conclusion)
        # A run that went green on a later attempt failed or was cancelled on
        # an earlier one. Counting a cancelled attempt as flaky overcounts,
        # which errs on the side the budget is there for.
        if conclusion == "success" and int(run.get("run_attempt", 1)) > 1:
            outcomes[str(run["head_sha"])].add("rerun")
    flaky = sorted(
        sha for sha, seen in outcomes.items() if "rerun" in seen or {"success", "failure"} <= seen
    )
    return FlakyReport(commits=len(outcomes), flaky=tuple(flaky))


def _runs_from_gh(repo: str) -> list[dict[str, Any]]:
    out = subprocess.run(
        [
            "gh",
            "api",
            "--paginate",
            f"repos/{repo}/actions/runs?per_page=100",
            "-q",
            ".workflow_runs[]",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    ).stdout
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def _runs_from_file(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text("utf-8"))
    if isinstance(data, dict):
        data = data.get("workflow_runs", [])
    if not isinstance(data, list):
        raise ValueError(f"{path}: expected a list of runs or a workflow_runs object")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("runs", nargs="?", type=Path, help="runs JSON (default: ask gh)")
    parser.add_argument("--repo", default=REPO)
    parser.add_argument("--budget", type=float, default=BUDGET)
    args = parser.parse_args(argv)
    runs = _runs_from_file(args.runs) if args.runs else _runs_from_gh(args.repo)
    report = measure(runs)
    print(
        f"{WORKFLOW}: {len(report.flaky)} flaky commit(s) of {report.commits} "
        f"({report.rate:.1%}, budget {args.budget:.0%})"
    )
    for sha in report.flaky:
        print(f"  flaky: {sha}")
    if report.rate > args.budget:
        print("flaky rate over budget", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
