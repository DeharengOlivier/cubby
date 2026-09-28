"""``scripts/flaky_rate.py`` counts commits whose CI both failed and passed."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "flaky_rate.py"


@pytest.fixture(scope="module")
def flaky_rate():
    spec = importlib.util.spec_from_file_location("flaky_rate", SCRIPT)
    assert spec
    assert spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["flaky_rate"] = module
    spec.loader.exec_module(module)
    return module


def _run(sha, conclusion, attempt=1, name="CI", status="completed"):
    return {
        "name": name,
        "head_sha": sha,
        "conclusion": conclusion,
        "run_attempt": attempt,
        "status": status,
    }


def test_a_real_failure_fixed_by_a_new_commit_is_not_flaky(flaky_rate):
    report = flaky_rate.measure([_run("a", "failure"), _run("b", "success")])

    assert report.flaky == ()
    assert report.commits == 2


def test_a_commit_that_failed_then_passed_is_flaky(flaky_rate):
    report = flaky_rate.measure([_run("a", "failure"), _run("a", "success"), _run("b", "success")])

    assert report.flaky == ("a",)
    assert report.rate == 0.5


def test_a_rerun_that_went_green_is_flaky(flaky_rate):
    assert flaky_rate.measure([_run("a", "success", attempt=2)]).flaky == ("a",)


def test_other_workflows_and_unfinished_runs_are_ignored(flaky_rate):
    report = flaky_rate.measure(
        [
            _run("a", "failure", name="Release"),
            _run("a", "success", name="Release"),
            _run("b", None, status="in_progress"),
            _run("c", "cancelled"),
        ]
    )

    assert report.flaky == ()
    assert report.commits == 0
    assert report.rate == 0.0


def test_the_command_fails_over_budget(flaky_rate, tmp_path, capsys):
    runs = tmp_path / "runs.json"
    runs.write_text(
        json.dumps({"workflow_runs": [_run("a", "failure"), _run("a", "success")]}),
        encoding="utf-8",
    )

    assert flaky_rate.main([str(runs)]) == 1
    assert "1 flaky commit(s) of 1" in capsys.readouterr().out
    assert flaky_rate.main([str(runs), "--budget", "1"]) == 0


def test_a_file_that_is_not_a_run_list_is_refused(flaky_rate, tmp_path):
    runs = tmp_path / "runs.json"
    runs.write_text('"nope"', encoding="utf-8")

    with pytest.raises(ValueError, match="expected a list of runs"):
        flaky_rate.main([str(runs)])
