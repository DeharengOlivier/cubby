"""Reproducers for the independent review of PR 7."""

from __future__ import annotations

import errno
import json
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from cubby.adapters import journal as journal_module
from cubby.adapters.filesystem import move_no_clobber
from cubby.adapters.journal import Entry, Journal
from cubby.adapters.ledger import Heartbeat, Ledger
from cubby.adapters.logging import file_logger, human_line
from cubby.app.sorter import Sorter
from cubby.app.watcher import Watcher
from cubby.cli import agent as cli_agent
from cubby.cli import main
from tests.helpers import config_for, process_named_cubby_watch

# --- major 1: the clean-up of a failed move never removes the file's last name --


def test_a_delete_that_happened_but_reported_an_error_leaves_the_file_moved(tmp_path, monkeypatch):
    # Network filesystems can report a failed delete that did happen.
    source = tmp_path / "a.txt"
    source.write_text("only copy")
    destination = tmp_path / "Documents" / "a.txt"
    destination.parent.mkdir()
    real_unlink = Path.unlink

    def unlink_then_fail(self, missing_ok=False):
        real_unlink(self, missing_ok=missing_ok)
        if self == source:
            raise OSError(errno.EIO, "Input/output error", str(self))

    monkeypatch.setattr(Path, "unlink", unlink_then_fail)

    move_no_clobber(source, destination)  # the move did complete

    assert destination.read_text() == "only copy"


def test_a_source_replaced_by_another_file_keeps_ours_at_the_destination(tmp_path, monkeypatch):
    source = tmp_path / "a.txt"
    source.write_text("ours")
    destination = tmp_path / "Documents" / "a.txt"
    destination.parent.mkdir()
    real_unlink = Path.unlink

    def replaced_then_fail(self, missing_ok=False):
        if self == source:
            # Another program swapped the name for its own file, then ours cannot be removed.
            real_unlink(self)
            source.write_text("theirs")
            raise PermissionError(errno.EACCES, "Permission denied", str(self))
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", replaced_then_fail)

    move_no_clobber(source, destination)

    assert destination.read_text() == "ours"
    assert source.read_text() == "theirs"


def test_a_move_that_cannot_check_its_source_after_a_failed_delete_fails_loudly(
    tmp_path, monkeypatch
):
    # Re-review of PR 7: any error from the check was read as "the source is
    # gone", so a transient error reported a move that had left two names.
    source = tmp_path / "a.txt"
    source.write_text("x")
    destination = tmp_path / "Documents" / "a.txt"
    destination.parent.mkdir()
    real_unlink, real_stat = Path.unlink, Path.stat
    failing = {"now": False}

    def refuse_unlink(self, missing_ok=False):
        if self == source:
            failing["now"] = True  # from here on, the source cannot be examined
            raise OSError(errno.EIO, "Input/output error", str(self))
        real_unlink(self, missing_ok=missing_ok)

    def refuse_stat(self, *, follow_symlinks=True):
        if failing["now"] and self == source:
            raise OSError(errno.EIO, "Input/output error", str(self))
        return real_stat(self, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "unlink", refuse_unlink)
    monkeypatch.setattr(Path, "stat", refuse_stat)

    with pytest.raises(OSError, match="Input/output error") as info:
        move_no_clobber(source, destination)

    assert info.value.errno == errno.EIO
    assert failing["now"]  # raised by the delete, not before the link


# --- major 2: uninstall.sh can still remove a broken install --------------------


def _run_uninstall_script(tmp_path: Path, cubby_body: str, *args: str):
    home = tmp_path / "home"
    venv = home / ".local" / "share" / "cubby" / "venv"
    venv.mkdir(parents=True, exist_ok=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    fake = bin_dir / "cubby"
    fake.write_text("#!/bin/sh\n" + cubby_body)
    fake.chmod(0o755)
    script = Path(__file__).resolve().parents[1] / "uninstall.sh"
    completed = subprocess.run(
        ["/bin/sh", str(script), *args],
        env={"HOME": str(home), "PATH": f"{bin_dir}:/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    return completed, venv


def test_uninstall_script_removes_an_install_whose_cubby_cannot_run(tmp_path):
    # A venv whose interpreter is gone: the shell answers 127, cubby never ran.
    completed, venv = _run_uninstall_script(tmp_path, "exit 127\n")

    assert completed.returncode == 0
    assert not venv.exists()
    assert "warning" in completed.stderr


def test_uninstall_script_can_be_forced_past_a_running_agent(tmp_path):
    completed, venv = _run_uninstall_script(tmp_path, "exit 1\n", "--force")

    assert completed.returncode == 0
    assert not venv.exists()


# --- minor 3: the pid behind a heartbeat must be a cubby -----------------------


@pytest.mark.parametrize("pid", [0, -1])
def test_a_heartbeat_with_a_pid_no_process_can_have_is_not_alive(pid):
    assert not Heartbeat(at=datetime.now(), pid=pid, source="/d", interval=30).process_alive()


def test_a_live_process_that_is_not_cubby_is_not_taken_for_one(tmp_path):
    other = subprocess.Popen(["sleep", "30"])
    try:
        beat = Heartbeat(at=datetime.now(), pid=other.pid, source="/d", interval=30)
        assert not beat.process_alive()
    finally:
        other.kill()
        other.wait(timeout=10)


def _beat(pid: int, *, age: float = 0) -> None:
    ledger = Ledger()
    ledger.heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
    at = datetime.fromtimestamp(datetime.now().timestamp() - age)
    ledger.heartbeat_path.write_text(
        json.dumps(
            {"at": at.isoformat(timespec="seconds"), "pid": pid, "source": "/d", "interval": 30.0}
        ),
        encoding="utf-8",
    )


class _Uninstalled:
    name = "fake"

    def uninstall(self, label: str = "com.cubby.agent") -> bool:
        return True


def test_uninstall_succeeds_when_the_last_heartbeat_is_stale(monkeypatch):
    monkeypatch.setattr(cli_agent, "detect_service", _Uninstalled)
    with process_named_cubby_watch() as pid:  # alive and named like the agent
        _beat(pid, age=3600)  # but silent for an hour: not the one sorting now
        assert main(["uninstall"]) == 0
        _beat(pid)  # the same process, beating now: it is
        assert main(["uninstall"]) == 1


def test_uninstall_succeeds_when_the_process_behind_the_heartbeat_is_gone(monkeypatch):
    monkeypatch.setattr(cli_agent, "detect_service", _Uninstalled)
    _beat(2**22 + 12345)

    assert main(["uninstall"]) == 0


# --- minor 4: a journal that shrank is compacted at the usual size again -------


def test_the_compaction_threshold_follows_a_journal_that_shrank(tmp_path, monkeypatch):
    journal = Journal(tmp_path / "j.jsonl")
    for index in range(4):
        journal.record(Entry(f"r{index}", 0, "move", Path(f"/a/{index}"), Path(f"/b/{index}")))
    monkeypatch.setattr(journal_module, "MAX_BYTES", 1)
    journal.compact()  # all pending: kept, and the threshold is now twice this size
    journal.path.unlink()  # someone removed it (the runbook's removal, a reset)
    old = Entry("old", 0, "move", Path("/a/o"), Path("/b/o"))
    journal.record(old)
    journal.settle(old, "restored")  # undone, and not among the most recent: droppable
    journal.record(Entry("new", 0, "move", Path("/a/n"), Path("/b/n")))
    monkeypatch.setattr(journal_module, "KEEP_RUNS", 1)

    journal.compact()

    assert [run.run_id for run in journal.runs()] == ["new"]


# --- minor 5: the lines that end a pass carry its run id -----------------------


def test_the_summary_and_failure_lines_of_a_pass_carry_its_run_id(tmp_path):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    (downloads / "notes.txt").write_text("x")
    log_path = tmp_path / "cubby.log"
    ledger = Ledger(tmp_path / "state")
    sorter = Sorter(config_for(downloads), ledger=ledger, log=file_logger(log_path))

    Watcher(sorter, 0, sleep=lambda _: None, log=file_logger(log_path)).run(max_cycles=1)

    (record,) = ledger.runs()
    lines = [json.loads(line) for line in log_path.read_text("utf-8").splitlines()]
    summary = [line for line in lines if line["msg"].startswith("sorted")]
    assert summary
    assert summary[0].get("run") == record.run


def test_a_pass_that_fails_logs_the_failure_under_its_run_id(tmp_path):
    class Failing:
        source = tmp_path

        def sort_once(self, *, apply, stop=None, run_id=None):
            raise OSError(5, "I/O error")

    log_path = tmp_path / "cubby.log"
    Watcher(Failing(), 0, sleep=lambda _: None, log=file_logger(log_path)).run(max_cycles=1)

    lines = [json.loads(line) for line in log_path.read_text("utf-8").splitlines()]
    failed = [line for line in lines if "failed" in line["msg"]]
    assert failed
    assert failed[0].get("run")


# --- minor 6: cubby log survives odd records -----------------------------------


@pytest.mark.parametrize("record", [{"level": None, "msg": "x"}, {"msg": None}, {"ts": 5}])
def test_a_record_with_odd_fields_is_still_shown(record):
    assert isinstance(human_line(record), str)


def test_cubby_log_shows_a_record_whose_level_is_null(capsys):
    from cubby.adapters import state

    state.log_path().parent.mkdir(parents=True, exist_ok=True)
    state.log_path().write_text(json.dumps({"level": None, "msg": "odd"}) + "\n", "utf-8")

    assert main(["log", "--warnings"]) == 0
    assert "odd" in capsys.readouterr().out


# --- minor 7: a deleted duplicate is logged as a deletion ----------------------


def test_the_log_line_of_a_deleted_duplicate_says_it_was_deleted(tmp_path):
    downloads = tmp_path / "Downloads"
    (downloads / "Documents").mkdir(parents=True)
    (downloads / "Documents" / "notes.txt").write_text("same")
    (downloads / "notes.txt").write_text("same")
    lines: list[str] = []

    Sorter(
        config_for(downloads, dedupe=True), log=lambda message, **_: lines.append(message)
    ).sort_once(apply=True)

    (line,) = lines
    assert "duplicate of Documents/notes.txt" in line
    assert "->" not in line


# --- suspicion: the stop command outlives the agent's own stop grace -----------


@pytest.mark.parametrize("service_class", ["systemd", "launchd"])
def test_the_command_that_stops_the_agent_waits_longer_than_its_stop_grace(
    tmp_path, monkeypatch, service_class
):
    from cubby.adapters.service import ServiceSpec
    from cubby.adapters.service import launchd as launchd_mod
    from cubby.adapters.service import systemd as systemd_mod
    from cubby.adapters.service.base import STOP_TIMEOUT

    monkeypatch.setattr(systemd_mod, "_UNIT_DIR", tmp_path / "systemd")
    monkeypatch.setattr(launchd_mod, "_AGENTS_DIR", tmp_path / "LaunchAgents")
    timeouts: dict[str, float] = {}
    stopped = {"yes": False}

    def manager(cmd, **kwargs):
        joined = " ".join(cmd)
        timeouts[joined] = kwargs["timeout"]
        if "disable --now" in joined or "unload" in joined:
            stopped["yes"] = True
        elif "restart" in cmd or "enable" in cmd or "load" in cmd:
            stopped["yes"] = False
        if "is-active" in cmd or cmd[:2] == ["launchctl", "list"]:
            code = 3 if stopped["yes"] else 0
            out = "active\n" if "is-active" in cmd else '{ "PID" = 1; };\n'
            return subprocess.CompletedProcess(cmd, code, out, "")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr("subprocess.run", manager)
    service = (
        systemd_mod.SystemdService() if service_class == "systemd" else launchd_mod.LaunchdService()
    )
    service.install(ServiceSpec(program_args=["/usr/bin/cubby", "watch"], log_path=tmp_path / "l"))

    assert service.uninstall() is True

    stops = [
        t
        for cmd, t in timeouts.items()
        if any(verb in cmd.split() for verb in ("disable", "unload", "restart"))
    ]
    assert stops
    assert min(stops) > STOP_TIMEOUT
