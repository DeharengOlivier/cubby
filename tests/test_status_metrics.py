"""What ``cubby status`` measures: the last pass, and the errors of the last day.

The last pass (how long it took, what it moved, how many files still wait to
settle) rides on the heartbeat the agent writes anyway. The day's activity is
read from the run ledger, with failures grouped by the kind of error, so one
permission problem on forty files reads as one problem, tagged with the cubby
versions that hit it.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from cubby.adapters.ledger import Failure, Ledger, PassMetrics, RunRecord
from cubby.app.activity import error_kind, summarize
from cubby.app.report import SortOutcome
from cubby.app.sorter import Sorter
from cubby.app.watcher import StopRequest, Watcher
from cubby.cli import agent as cli_agent
from cubby.cli import main
from tests.helpers import aged_file, config_for

NOW = datetime(2026, 9, 28, 12, 0, 0)


def _record(
    run: str, finished: datetime, *failures: tuple[str, str], moved: int = 1, version="0.3.0"
):
    return RunRecord(
        run=run,
        mode="watch",
        source="/d",
        started=finished.isoformat(timespec="seconds"),
        finished=finished.isoformat(timespec="seconds"),
        moved=moved,
        failed=len(failures),
        failures=tuple(Failure(name, error) for name, error in failures),
        version=version,
    )


# --- grouping errors -----------------------------------------------------------


def test_errors_on_different_files_are_one_kind():
    first = "PermissionError: [Errno 13] Permission denied: '/home/a/Downloads/x.pdf'"
    second = 'PermissionError: [Errno 13] Permission denied: "/home/a/Downloads/y z.pdf"'

    assert error_kind(first) == error_kind(second)
    assert error_kind(first) == "PermissionError: [Errno 13] Permission denied: '...'"


def test_errors_of_different_kinds_stay_apart():
    assert error_kind("OSError: [Errno 28] No space left on device") != error_kind(
        "PermissionError: [Errno 13] Permission denied: 'x'"
    )


def test_the_day_counts_runs_moves_and_failures_and_groups_the_errors():
    denied = "PermissionError: [Errno 13] Permission denied: '{}'"
    records = [
        _record("old", NOW - timedelta(hours=30), ("ancient.pdf", denied.format("a")), moved=9),
        _record("r1", NOW - timedelta(hours=5), ("a.pdf", denied.format("a.pdf")), version="0.2.0"),
        _record(
            "r2",
            NOW - timedelta(hours=1),
            ("b.pdf", denied.format("b.pdf")),
            ("c.iso", "OSError: [Errno 28] No space left on device"),
            moved=3,
        ),
    ]

    day = summarize(records, since=NOW - timedelta(hours=24))

    assert (day.runs, day.moved, day.failed) == (2, 4, 3)
    top, other = day.errors
    assert top.kind == error_kind(denied.format("x"))
    assert (top.count, top.runs) == (2, 2)
    assert top.files == ("b.pdf", "a.pdf")  # most recent first
    assert top.versions == ("0.2.0", "0.3.0")
    assert top.last_seen == records[2].finished
    assert (other.kind, other.count) == ("OSError: [Errno 28] No space left on device", 1)


def test_a_record_with_an_unreadable_date_is_left_out_not_fatal():
    broken = _record("r1", NOW, ("a.pdf", "OSError: x"))
    broken = RunRecord(**{**broken.__dict__, "finished": "not a date"})

    day = summarize([broken, _record("r2", NOW)], since=NOW - timedelta(hours=24))

    assert day.runs == 1


def test_only_the_most_frequent_kinds_and_a_few_files_are_kept():
    records = [
        _record(
            f"r{i}",
            NOW - timedelta(minutes=i),
            *((f"f{i}-{j}", f"E{i}: boom") for j in range(i + 1)),
        )
        for i in range(8)
    ]

    day = summarize(records, since=NOW - timedelta(hours=24), top=3, files=2)

    assert [group.kind for group in day.errors] == ["E7: boom", "E6: boom", "E5: boom"]
    assert day.errors[0].files == ("f7-0", "f7-1")
    assert day.failed == sum(range(1, 9))  # the totals still count every failure


# --- the last pass, on the heartbeat --------------------------------------------


def test_a_pass_counts_the_files_still_settling(tmp_path):
    aged_file(tmp_path, "settled.txt")
    aged_file(tmp_path, "fresh.txt", age=0)
    aged_file(tmp_path, "also-fresh.txt", age=0)
    waiting: list[Path] = []

    outcomes = Sorter(config_for(tmp_path, delay=3600)).sort_once(
        apply=True, on_waiting=waiting.append
    )

    assert [o.name for o in outcomes] == ["settled.txt"]
    assert sorted(p.name for p in waiting) == ["also-fresh.txt", "fresh.txt"]


class _Sorter:
    def __init__(self, source: Path, outcomes, waiting: int):
        self.source = source
        self._outcomes = outcomes
        self._waiting = waiting

    def sort_once(self, *, apply, stop=None, run_id=None, on_waiting=None):
        for i in range(self._waiting):
            on_waiting(self.source / f"w{i}")
        return self._outcomes


def test_the_heartbeat_carries_the_measures_of_the_last_pass(tmp_path):
    outcomes = [
        SortOutcome(
            source=tmp_path / "a.txt",
            category="Documents",
            stage=None,
            moved_to=tmp_path / "Documents" / "a.txt",
        ),
        SortOutcome.failed(tmp_path / "b.txt", "PermissionError: no"),
    ]
    ticks = iter([100.0, 100.25])
    ledger = Ledger()
    watcher = Watcher(
        _Sorter(tmp_path, outcomes, waiting=3), 1.0, ledger=ledger, clock=lambda: next(ticks)
    )

    watcher.run(max_cycles=1)

    beat = ledger.heartbeat()
    assert beat is not None
    assert beat.last_pass == PassMetrics(seconds=0.25, moved=1, failed=1, waiting=3)


def test_a_heartbeat_from_an_older_cubby_has_no_pass_measures():
    ledger = Ledger()
    ledger.heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
    ledger.heartbeat_path.write_text(
        json.dumps({"v": 1, "at": NOW.isoformat(), "pid": 1, "source": "/d", "interval": 30.0}),
        encoding="utf-8",
    )

    beat = ledger.heartbeat()

    assert beat is not None
    assert beat.last_pass is None


def test_a_damaged_pass_measure_does_not_hide_the_heartbeat():
    ledger = Ledger()
    ledger.heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
    ledger.heartbeat_path.write_text(
        json.dumps(
            {
                "v": 1,
                "at": NOW.isoformat(),
                "pid": 1,
                "source": "/d",
                "interval": 30.0,
                "last_pass": "?",
            }
        ),
        encoding="utf-8",
    )

    beat = ledger.heartbeat()

    assert beat is not None
    assert beat.last_pass is None


# --- what status shows ------------------------------------------------------------


def test_status_shows_the_last_pass_and_the_day(capsys, monkeypatch):
    monkeypatch.setattr(cli_agent, "detect_service", lambda: None)
    ledger = Ledger()
    ledger.beat(Path("/d"), 30.0, PassMetrics(seconds=1.5, moved=2, failed=1, waiting=4))
    now = datetime.now()
    denied = "PermissionError: [Errno 13] Permission denied: '/d/{}'"
    ledger.record(_record("r1", now - timedelta(hours=2), ("a.pdf", denied.format("a.pdf"))))
    ledger.record(_record("r2", now - timedelta(minutes=5), ("b.pdf", denied.format("b.pdf"))))

    main(["status", "--json"])
    payload = json.loads(capsys.readouterr().out)
    main(["status"])
    text = capsys.readouterr().out

    assert payload["agent"]["last_pass"] == {"seconds": 1.5, "moved": 2, "failed": 1, "waiting": 4}
    day = payload["activity"]
    assert (day["hours"], day["runs"], day["moved"], day["failed"]) == (24, 2, 2, 2)
    assert day["errors"][0]["count"] == 2
    assert day["errors"][0]["files"] == ["b.pdf", "a.pdf"]
    assert "took 1.5 s, moved 2, 1 failed, 4 waiting to settle" in text
    assert "2 runs, moved 2, 2 failed" in text
    assert "2x PermissionError: [Errno 13] Permission denied: '...'" in text


def test_status_with_nothing_recorded_says_so(capsys, monkeypatch):
    monkeypatch.setattr(cli_agent, "detect_service", lambda: None)

    main(["status", "--json"])
    payload = json.loads(capsys.readouterr().out)
    main(["status"])
    text = capsys.readouterr().out

    assert payload["agent"]["last_pass"] is None
    assert payload["activity"] == {"hours": 24, "runs": 0, "moved": 0, "failed": 0, "errors": []}
    assert "last 24 h" in text


def test_a_stop_during_the_sleep_ends_the_loop_without_another_pass(tmp_path):
    # Measured on a real `cubby watch`: a SIGTERM that woke the sleep started
    # one more pass, which stopped before its first file and then overwrote
    # the heartbeat with the measures of a pass that did nothing.
    stop = StopRequest()
    sorter = _Sorter(tmp_path, [], waiting=1)
    calls: list[str] = []
    real = sorter.sort_once

    def counted(**kwargs):
        calls.append(kwargs["run_id"])
        return real(**kwargs)

    sorter.sort_once = counted
    ledger = Ledger()
    watcher = Watcher(sorter, 60.0, ledger=ledger, sleep=lambda _: stop.request())

    watcher.run(stop=stop)

    assert len(calls) == 1
    beat = ledger.heartbeat()
    assert beat is not None
    assert beat.last_pass is not None
    assert beat.last_pass.waiting == 1
