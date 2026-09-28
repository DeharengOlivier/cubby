"""``scripts/flaky_rate.py`` counts commits whose CI both failed and passed."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "flaky_rate.py"


@pytest.fixture(scope="module")
def fr():
    spec = importlib.util.spec_from_file_location("flaky_rate", SCRIPT)
    assert spec
    assert spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["flaky_rate"] = module
    spec.loader.exec_module(module)
    return module


def _run(sha, conclusion, attempt=1, name="CI", status="completed", run_id=7):
    return {
        "id": run_id,
        "name": name,
        "head_sha": sha,
        "conclusion": conclusion,
        "run_attempt": attempt,
        "status": status,
    }


def _earlier(*conclusions):
    return lambda _run: list(conclusions)


# --- what counts as flaky ------------------------------------------------------


def test_a_real_failure_fixed_by_a_new_commit_is_not_flaky(fr):
    report = fr.measure([_run("a", "failure"), _run("b", "success")])

    assert report.flaky == ()
    assert report.commits == 2


@pytest.mark.parametrize("failed", ["failure", "timed_out", "startup_failure"])
def test_a_commit_that_failed_then_passed_is_flaky(fr, failed):
    report = fr.measure([_run("a", failed), _run("a", "success"), _run("b", "success")])

    assert report.flaky == ("a",)
    assert report.rate == 0.5


def test_flaky_commits_are_listed_in_order(fr):
    runs = [_run(sha, c) for sha in ("c", "a", "b") for c in ("failure", "success")]

    assert fr.measure(runs).flaky == ("a", "b", "c")


def test_a_rerun_that_went_green_after_a_failure_is_flaky(fr):
    report = fr.measure([_run("a", "success", attempt=2)], _earlier("failure"))

    assert report.flaky == ("a",)


def test_a_rerun_that_failed_again_is_not_flaky(fr):
    report = fr.measure([_run("a", "failure", attempt=2)], _earlier("failure"))

    assert report.flaky == ()
    assert report.commits == 1


def test_a_green_rerun_of_a_green_run_is_not_flaky(fr):
    assert fr.measure([_run("a", "success", attempt=2)], _earlier("success")).flaky == ()


def test_a_green_rerun_after_a_cancellation_is_not_flaky(fr):
    assert fr.measure([_run("a", "success", attempt=2)], _earlier("cancelled")).flaky == ()


def test_earlier_attempts_are_only_fetched_for_reruns(fr):
    asked = []

    fr.measure([_run("a", "success")], lambda run: asked.append(run) or [])

    assert asked == []


def test_other_workflows_and_unfinished_or_cancelled_runs_are_ignored(fr):
    report = fr.measure(
        [
            _run("a", "failure", name="Release"),
            _run("a", "success", name="Release"),
            _run("b", "failure", status="in_progress"),
            _run("b", "success"),
            _run("c", "cancelled"),
        ]
    )

    assert report.flaky == ()
    assert report.commits == 1


# --- asking gh -------------------------------------------------------------------


def test_runs_are_asked_for_every_page_since_the_window(fr):
    calls = []

    def gh(args):
        calls.append(args)
        return json.dumps(_run("a", "success")) + "\n\n"

    runs = fr.runs_from_gh("o/r", datetime(2026, 7, 1, tzinfo=UTC), gh)

    assert runs == [_run("a", "success")]
    assert calls == [
        [
            "api",
            "repos/o/r/actions/runs?per_page=100&created=>=2026-07-01",
            "--paginate",
            "-q",
            ".workflow_runs[]",
        ]
    ]


def test_earlier_attempts_are_asked_one_by_one(fr):
    calls = []

    def gh(args):
        calls.append(args[1])
        return "failure\n"

    assert fr.attempts_from_gh("o/r", gh)(_run("a", "success", attempt=3, run_id=9)) == [
        "failure",
        "failure",
    ]
    assert calls == ["repos/o/r/actions/runs/9/attempts/1", "repos/o/r/actions/runs/9/attempts/2"]


def test_a_failing_gh_is_reported_with_its_own_message(fr, monkeypatch, capsys):
    def fail(*_args, **kwargs):
        assert kwargs["check"] is True
        assert kwargs["timeout"] == 120
        raise subprocess.CalledProcessError(1, "gh", stderr="HTTP 404: Not Found\n")

    monkeypatch.setattr(fr.subprocess, "run", fail)

    assert fr.main(["--repo", "o/nope"]) == 2
    assert "HTTP 404: Not Found" in capsys.readouterr().err


def test_a_hanging_gh_is_reported(fr, monkeypatch, capsys):
    def hang(*_args, **_kwargs):
        raise subprocess.TimeoutExpired("gh", 120)

    monkeypatch.setattr(fr.subprocess, "run", hang)

    assert fr.main([]) == 2
    assert "timed out" in capsys.readouterr().err


def test_gh_output_is_returned(fr, monkeypatch):
    monkeypatch.setattr(
        fr.subprocess, "run", lambda *_a, **_k: subprocess.CompletedProcess([], 0, stdout="ok")
    )

    assert fr._gh(["api", "x"]) == "ok"


def test_the_command_measures_through_gh(fr, capsys):
    def gh(args):
        if "attempts" in args[1]:
            return "failure"
        return json.dumps(_run("a", "success", attempt=2)) + "\n"

    assert fr.main(["--budget", "1"], gh=gh) == 0
    assert "1 flaky commit(s) of 1" in capsys.readouterr().out


# --- the command -------------------------------------------------------------------


def _saved(tmp_path, data):
    path = tmp_path / "runs.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def test_the_command_fails_over_budget(fr, tmp_path, capsys):
    runs = _saved(tmp_path, {"workflow_runs": [_run("a", "failure"), _run("a", "success")]})

    assert fr.main([runs]) == 1
    captured = capsys.readouterr()
    assert "1 flaky commit(s) of 1" in captured.out
    assert "flaky: a" in captured.out
    assert "over budget" in captured.err
    assert fr.main([runs, "--budget", "1"]) == 0


def test_the_command_passes_within_budget(fr, tmp_path, capsys):
    assert fr.main([_saved(tmp_path, [_run("a", "success")])]) == 0
    assert "0 flaky commit(s) of 1 (0.0%, budget 2%)" in capsys.readouterr().out


@pytest.mark.parametrize("data", [[], {"workflow_runs": []}, [_run("a", "cancelled")]])
def test_measuring_nothing_is_a_failure(fr, tmp_path, capsys, data):
    assert fr.main([_saved(tmp_path, data)]) == 2
    assert "nothing was measured" in capsys.readouterr().err


@pytest.mark.parametrize("data", ["nope", {"runs": []}])
def test_a_file_that_is_not_a_run_list_is_refused(fr, tmp_path, capsys, data):
    assert fr.main([_saved(tmp_path, data)]) == 2
    assert "flaky-rate:" in capsys.readouterr().err
