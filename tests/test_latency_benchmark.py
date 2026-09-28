"""``benchmarks/latency.py`` runs end to end and reports what docs/PERFORMANCE.md quotes.

The benchmark runs for real with tiny sizes: a few files, one invocation of each
command, a small ledger and journal. What is checked is the shape of its report
(every critical path, each with its sample size, percentiles and budget, and the
conditions of the run), not the timings, which depend on the machine.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

BENCHMARKS = Path(__file__).resolve().parents[1] / "benchmarks"
SCRIPT = BENCHMARKS / "latency.py"

PATHS = {
    "move",
    "pass",
    "idle pass",
    "cubby --version",
    "cubby status",
    "cubby history",
    "cubby explain FILE",
    "cubby plan",
    "cubby run",
    "cubby undo",
}


@pytest.fixture(scope="module")
def latency():
    sys.path.insert(0, str(BENCHMARKS))
    try:
        spec = importlib.util.spec_from_file_location("latency", SCRIPT)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.path.remove(str(BENCHMARKS))


def test_percentiles_are_nearest_rank(latency):
    samples = list(range(100, 0, -1))  # 1 to 100, unsorted
    summary = latency.summarize(samples)
    assert summary == {"n": 100, "p50": 50, "p90": 90, "p95": 95, "p99": 99, "max": 100}


def test_below_a_hundred_samples_p99_is_the_maximum(latency):
    assert latency.summarize(list(range(1, 31)))["p99"] == 30


def test_a_tiny_run_reports_every_path_with_its_conditions(tmp_path):
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--files",
            "5",
            "--passes",
            "2",
            "--invocations",
            "1",
            "--ledger-runs",
            "10",
            "--journal-entries",
            "40",
            "--json",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
        cwd=tmp_path,
    )
    report = json.loads(completed.stdout)

    assert set(report["paths"]) == PATHS
    for name, row in report["paths"].items():
        assert set(row) >= {"n", "p50", "p90", "p95", "p99", "max", "budget", "met"}, name
        assert row["n"] >= 1, name
        assert 0 < row["p50"] <= row["p90"] <= row["p95"] <= row["p99"] <= row["max"], name
    # Two passes of five files: four moves each, the first of a pass (which
    # also lists the folder) left out.
    assert report["paths"]["move"]["n"] == 8
    assert report["paths"]["pass"]["n"] == 2
    assert report["paths"]["cubby run"]["n"] == 1
    conditions = report["conditions"]
    assert set(conditions) >= {
        "machine",
        "os",
        "python",
        "filesystem",
        "load_before",
        "load_after",
        "commit",
        "cubby",
        "state",
    }
