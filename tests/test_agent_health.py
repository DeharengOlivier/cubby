"""What cubby knows about its own health: the ledger, the heartbeat, the lock,
the watch loop's resilience, and what `cubby status` makes of all of it.

Audit 1 found `status` printing "running" whenever the unit file existed, and
an agent that died on the first file it could not move.
"""

from __future__ import annotations

import json
import multiprocessing
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from cubby.adapters import ledger as ledger_module
from cubby.adapters.ledger import Failure, Ledger, RunRecord
from cubby.adapters.lock import Busy, exclusive
from cubby.app import sorter as sorter_module
from cubby.app.sorter import Sorter
from cubby.app.watcher import Watcher
from cubby.cli import EXIT_FAILED, EXIT_OK, main
from cubby.cli import agent as cli_agent
from cubby.cli import sorting as cli_sorting
from tests.helpers import config_for


class Recorder:
    def __init__(self) -> None:
        self.lines: list[tuple[str, str]] = []

    def __call__(self, message: str, *, level: str = "INFO") -> None:
        self.lines.append((level, message))

    def at(self, level: str) -> list[str]:
        return [m for lvl, m in self.lines if lvl == level]


# --- the ledger --------------------------------------------------------------


def _record(run: str, moved: int = 1, failed: int = 0) -> RunRecord:
    return RunRecord(
        run=run,
        mode="run",
        source="/d",
        started="2026-09-28T10:00:00",
        finished="2026-09-28T10:00:01",
        moved=moved,
        failed=failed,
        failures=tuple(Failure(f"f{i}", "PermissionError: no") for i in range(failed)),
    )


def test_the_ledger_returns_runs_most_recent_first(tmp_path):
    ledger = Ledger(tmp_path)
    for run in ("a", "b", "c"):
        ledger.record(_record(run))

    assert [r.run for r in ledger.runs()] == ["c", "b", "a"]
    assert [r.run for r in ledger.runs(limit=2)] == ["c", "b"]


@pytest.mark.parametrize(
    ("moved", "failed", "status"), [(3, 0, "ok"), (2, 1, "partial"), (0, 2, "failed")]
)
def test_a_run_status_follows_its_counts(moved, failed, status):
    assert _record("r", moved, failed).status == status


def test_damaged_ledger_lines_are_skipped(tmp_path):
    ledger = Ledger(tmp_path)
    ledger.record(_record("good"))
    with ledger.runs_path.open("a", encoding="utf-8") as handle:
        handle.write('{"v": 1, "run": "half"\n[1]\n{"v": 1, "run": "x"}\n')

    assert [r.run for r in ledger.runs()] == ["good"]


def test_a_run_keeps_a_bounded_number_of_failures(tmp_path):
    ledger = Ledger(tmp_path)
    ledger.record(_record("big", moved=0, failed=500))

    stored = json.loads(ledger.runs_path.read_text("utf-8"))
    assert stored["failed"] == 500
    assert len(stored["failures"]) == ledger_module.MAX_FAILURES_RECORDED


def test_the_ledger_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(ledger_module, "MAX_BYTES", 500)
    monkeypatch.setattr(ledger_module, "KEEP_LINES", 3)
    ledger = Ledger(tmp_path)
    for index in range(10):
        ledger.record(_record(f"r{index}"))

    assert len(ledger.runs()) <= 4


def test_a_sort_records_its_run_with_failures(tmp_path, monkeypatch):
    source = tmp_path / "Downloads"
    source.mkdir()
    (source / "a.txt").write_text("a")
    (source / "b.txt").write_text("b")
    real_move = sorter_module.move_into

    def refuse_b(path, *args, **kwargs):
        if path.name == "b.txt":
            raise PermissionError(13, "Permission denied")
        return real_move(path, *args, **kwargs)

    monkeypatch.setattr(sorter_module, "move_into", refuse_b)
    ledger = Ledger(tmp_path / "state")

    Sorter(config_for(source), ledger=ledger, mode="watch").sort_once(apply=True)

    (record,) = ledger.runs()
    assert (record.mode, record.moved, record.failed, record.status) == ("watch", 1, 1, "partial")
    assert record.failures[0].file == "b.txt"
    assert "PermissionError" in record.failures[0].error


def test_a_pass_with_nothing_to_do_leaves_no_ledger_line(tmp_path):
    source = tmp_path / "Downloads"
    source.mkdir()
    ledger = Ledger(tmp_path / "state")

    Sorter(config_for(source), ledger=ledger).sort_once(apply=True)

    assert ledger.runs() == []


def test_a_ledger_that_cannot_be_written_is_reported(tmp_path):
    source = tmp_path / "Downloads"
    source.mkdir()
    (source / "a.txt").write_text("a")
    blocked = tmp_path / "state"
    blocked.write_text("a file where the folder should be")
    warnings: list[str] = []

    Sorter(config_for(source), ledger=Ledger(blocked), warn=warnings.append).sort_once(apply=True)

    assert any("run ledger" in w for w in warnings)


# --- the heartbeat -----------------------------------------------------------


def test_the_heartbeat_round_trips(tmp_path):
    ledger = Ledger(tmp_path)
    assert ledger.heartbeat() is None

    ledger.beat(Path("/d"), 30.0)

    beat = ledger.heartbeat()
    assert beat is not None
    assert beat.source == "/d"
    assert beat.interval == 30.0
    assert beat.age_seconds() < 5


def test_a_damaged_heartbeat_reads_as_none(tmp_path):
    ledger = Ledger(tmp_path)
    ledger.heartbeat_path.write_text("{not json")
    assert ledger.heartbeat() is None


# --- the lock ----------------------------------------------------------------


def _hold_lock(path: str, ready, release) -> None:
    with exclusive(Path(path)):
        ready.set()
        release.wait(10)


def test_a_second_cubby_waits_then_gives_up_with_a_clear_error(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    ready, release = ctx.Event(), ctx.Event()
    holder = ctx.Process(target=_hold_lock, args=(str(tmp_path / "lock"), ready, release))
    holder.start()
    try:
        assert ready.wait(10)
        started = time.monotonic()
        with pytest.raises(Busy, match="another cubby"), exclusive(tmp_path / "lock", timeout=0.3):
            pass
        assert time.monotonic() - started >= 0.3
    finally:
        release.set()
        holder.join(10)

    # Once the holder is gone, the lock is free again.
    with exclusive(tmp_path / "lock", timeout=1):
        pass


# --- the watch loop keeps going ----------------------------------------------


def test_a_pass_that_raises_is_logged_and_the_next_pass_runs(tmp_path, monkeypatch):
    source = tmp_path / "Downloads"
    source.mkdir()
    sorter = Sorter(config_for(source))
    calls = {"n": 0}
    real = sorter.sort_pass  # the pass the agent runs

    def flaky(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("a bug")
        return real(**kwargs)

    monkeypatch.setattr(sorter, "sort_pass", flaky)
    log = Recorder()

    Watcher(sorter, interval=0, sleep=lambda _: None, log=log).run(max_cycles=2)

    assert calls["n"] == 2
    assert any("a bug" in m for m in log.at("ERROR"))


def test_a_missing_folder_is_reported_once_and_its_return_noticed(tmp_path):
    source = tmp_path / "Drive"
    source.mkdir()
    sorter = Sorter(config_for(source))
    log = Recorder()
    watcher = Watcher(sorter, interval=0, sleep=lambda _: None, log=log)

    source.rmdir()
    watcher.run(max_cycles=3)
    source.mkdir()
    (source / "a.txt").write_text("a")
    watcher.run(max_cycles=1)

    assert len([m for m in log.at("ERROR") if "missing" in m]) == 1
    assert any("back" in m for m in log.at("INFO"))
    assert (source / "Documents" / "a.txt").exists()


def test_a_busy_lock_skips_the_pass_without_failing(tmp_path, monkeypatch):
    source = tmp_path / "Downloads"
    source.mkdir()
    log = Recorder()

    def busy(**kwargs):
        raise Busy("another cubby process is sorting")

    monkeypatch.setattr("cubby.app.watcher.exclusive", busy)
    total = Watcher(Sorter(config_for(source)), interval=0, log=log).run(max_cycles=1)

    assert total == 0
    assert any("skipped" in m for m in log.at("WARNING"))


def test_each_completed_pass_beats(tmp_path):
    source = tmp_path / "Downloads"
    source.mkdir()
    ledger = Ledger(tmp_path / "state")

    Watcher(Sorter(config_for(source)), interval=45, ledger=ledger).run(max_cycles=1)

    beat = ledger.heartbeat()
    assert beat is not None
    assert beat.interval == 45


def test_failed_items_are_announced_as_a_warning(tmp_path, monkeypatch):
    source = tmp_path / "Downloads"
    source.mkdir()
    (source / "a.txt").write_text("a")
    monkeypatch.setattr(
        sorter_module, "move_into", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full"))
    )
    log = Recorder()

    Watcher(Sorter(config_for(source)), interval=0, log=log).run(max_cycles=1)

    assert any("could not be sorted" in m for m in log.at("WARNING"))


# --- cubby status ------------------------------------------------------------


class Agent:
    name = "fake"

    def __init__(self, *, installed: bool, running: bool) -> None:
        self.installed, self.running = installed, running

    def is_installed(self, label: str = "com.cubby.agent") -> bool:
        return self.installed

    def is_running(self, label: str = "com.cubby.agent") -> bool:
        return self.running

    def unit_path(self, label: str) -> Path:
        return Path("/tmp/fake.agent")


def _status(monkeypatch, capsys, agent: Agent | None, *extra: str) -> tuple[int, str]:
    monkeypatch.setattr(cli_agent, "detect_service", lambda: agent)
    code = main(["status", *extra])
    return code, capsys.readouterr().out


def test_status_says_not_running_when_the_manager_says_so(monkeypatch, capsys):
    code, out = _status(monkeypatch, capsys, Agent(installed=True, running=False))
    assert "installed but not running" in out
    assert code == EXIT_FAILED


def test_status_of_a_healthy_agent(monkeypatch, capsys, tmp_path):
    Ledger().beat(tmp_path, 30)  # a folder that exists: the agent is ready too
    Ledger().record(_record("r9", moved=2, failed=1))

    code, out = _status(monkeypatch, capsys, Agent(installed=True, running=True))

    assert code == EXIT_OK
    assert "running (fake" in out
    assert "moved 2, 1 failed" in out
    assert "f0" in out


def test_status_flags_a_stale_heartbeat(monkeypatch, capsys):
    ledger = Ledger()
    ledger.beat(Path("/d"), 30)
    stale = (datetime.now() - timedelta(hours=2)).isoformat(timespec="seconds")
    data = json.loads(ledger.heartbeat_path.read_text())
    data["at"] = stale
    ledger.heartbeat_path.write_text(json.dumps(data))

    code, out = _status(monkeypatch, capsys, Agent(installed=True, running=True))

    assert "stale" in out
    assert code == EXIT_FAILED


def test_status_without_an_agent_is_healthy(monkeypatch, capsys):
    code, out = _status(monkeypatch, capsys, None)
    assert "not installed" in out
    assert "never" in out
    assert code == EXIT_OK


def test_status_as_json(monkeypatch, capsys):
    Ledger().record(_record("r1"))
    code, out = _status(monkeypatch, capsys, Agent(installed=True, running=False), "--json")

    payload = json.loads(out)
    assert code == EXIT_FAILED
    assert payload["healthy"] is False
    assert payload["agent"]["running"] is False
    assert payload["last_run"]["run"] == "r1"


def test_status_shows_the_recent_log(monkeypatch, capsys):
    from cubby.adapters.logging import file_logger

    file_logger()("filed invoice.pdf")
    _, out = _status(monkeypatch, capsys, None)
    assert "filed invoice.pdf" in out


# --- cubby run and undo exit codes -------------------------------------------


def test_run_exits_non_zero_when_a_file_could_not_be_sorted(tmp_path, monkeypatch, capsys):
    (tmp_path / "a.txt").write_text("a")
    monkeypatch.setattr(
        sorter_module, "move_into", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full"))
    )

    code = main(["run", "--source", str(tmp_path), "--delay", "0", "--no-content"])

    captured = capsys.readouterr()
    assert code == EXIT_FAILED
    assert "could not sort a.txt" in captured.err
    assert "Could not sort" in captured.out


def test_undo_of_an_unknown_run_is_an_error(capsys):
    assert main(["undo", "--run", "nope"]) == EXIT_FAILED
    assert "no run 'nope'" in capsys.readouterr().err


def test_undo_that_leaves_files_pending_exits_non_zero(tmp_path, monkeypatch, capsys):
    (tmp_path / "a.txt").write_text("a")
    main(["run", "--source", str(tmp_path), "--delay", "0", "--no-content"])
    from cubby.app import undo as undo_module

    def refuse(*args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(undo_module, "move_no_clobber", refuse)

    assert main(["undo"]) == EXIT_FAILED
    assert "stay pending" in capsys.readouterr().err


def test_a_busy_lock_is_reported_by_run(tmp_path, monkeypatch, capsys):
    (tmp_path / "a.txt").write_text("a")

    def busy(**kwargs):
        raise Busy("another cubby process is sorting or undoing")

    monkeypatch.setattr(cli_sorting, "exclusive", busy)

    code = main(["run", "--source", str(tmp_path), "--delay", "0"])

    assert code == EXIT_FAILED
    assert "another cubby" in capsys.readouterr().err
    assert (tmp_path / "a.txt").exists()
