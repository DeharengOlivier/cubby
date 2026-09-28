"""Reproducers for the code defects found by executing docs/RUNBOOK.md.

An operator following the runbook in a throwaway home found that a failed undo
left a second name for the file, that `cubby uninstall` on Linux reported
success while the agent kept running, and that `cubby status` said "not
installed" about an agent that was still sorting.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from cubby.adapters.filesystem import move_no_clobber
from cubby.adapters.journal import Journal
from cubby.adapters.ledger import Ledger
from cubby.adapters.service import ServiceError, ServiceSpec
from cubby.adapters.service.launchd import LaunchdService
from cubby.adapters.service.systemd import SystemdService
from cubby.app.sorter import Sorter
from cubby.app.undo import undo_run
from cubby.cli import agent as cli_agent
from cubby.cli import main
from tests.helpers import config_for, process_named_cubby_watch
from tests.test_service_boundary import FakeManager, unit_dirs  # noqa: F401 - fixture

needs_permissions = pytest.mark.skipif(os.geteuid() == 0, reason="root ignores folder permissions")


# --- D12: a move whose source cannot be removed leaves one name, not two -------


@needs_permissions
def test_a_move_that_cannot_remove_its_source_leaves_no_second_name(tmp_path):
    held = tmp_path / "held"
    held.mkdir()
    source = held / "track.mp3"
    source.write_text("music")
    destination = tmp_path / "track.mp3"
    held.chmod(0o555)  # the file cannot be unlinked from here
    try:
        with pytest.raises(PermissionError):
            move_no_clobber(source, destination)
    finally:
        held.chmod(0o755)

    assert source.read_text() == "music"
    assert not destination.exists()


@needs_permissions
def test_a_retried_undo_does_not_leave_a_duplicate(tmp_path):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    (downloads / "notes.txt").write_text("x")
    journal = Journal(tmp_path / "journal.jsonl")
    Sorter(config_for(downloads), journal=journal).sort_once(apply=True)
    filed = downloads / "Documents"

    filed.chmod(0o555)
    try:
        first = undo_run(journal)
    finally:
        filed.chmod(0o755)
    assert first.failed
    second = undo_run(journal)

    assert second.restored == 1
    assert sorted(p.name for p in downloads.iterdir() if p.is_file()) == ["notes.txt"]


# --- D2: uninstall fails while the agent survives, and keeps the unit ---------


@pytest.mark.parametrize("service_class", [SystemdService, LaunchdService])
def test_uninstall_fails_and_keeps_the_unit_while_the_agent_survives(
    unit_dirs,  # noqa: F811 - the fixture imported above
    monkeypatch,
    service_class,
):
    monkeypatch.setattr("subprocess.run", FakeManager(active=True))
    service = service_class()
    unit = service.install(
        ServiceSpec(program_args=["/usr/bin/cubby", "watch"], log_path=unit_dirs / "l")
    )

    with pytest.raises(ServiceError, match="still running"):
        service.uninstall()

    # Kept so the manual stop in the runbook still has a unit to name.
    assert unit.exists()


@pytest.fixture
def agent_pid():
    with process_named_cubby_watch() as pid:
        yield pid


# --- D1: status does not call a live agent "not installed" and leave it there --


def _beat(pid: int) -> None:
    ledger = Ledger()
    ledger.heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
    ledger.heartbeat_path.write_text(
        json.dumps(
            {
                "at": datetime.now().isoformat(timespec="seconds"),
                "pid": pid,
                "source": "/d",
                "interval": 30.0,
            }
        ),
        encoding="utf-8",
    )


def test_status_names_a_cubby_that_still_sorts_without_an_agent(monkeypatch, capsys, agent_pid):
    monkeypatch.setattr(cli_agent, "detect_service", lambda: None)
    _beat(agent_pid)

    main(["status"])
    out = capsys.readouterr().out
    assert "not installed" in out
    assert f"pid {agent_pid}" in out

    main(["status", "--json"])
    assert json.loads(capsys.readouterr().out)["agent"]["live_pid"] == agent_pid


def test_status_ignores_the_heartbeat_of_a_process_that_is_gone(monkeypatch, capsys):
    monkeypatch.setattr(cli_agent, "detect_service", lambda: None)
    _beat(2**22 + 12345)  # above the default pid_max: no such process

    main(["status", "--json"])
    assert json.loads(capsys.readouterr().out)["agent"]["live_pid"] is None


# --- second drill ----------------------------------------------------------------


def test_the_log_line_of_a_move_says_where_the_file_went(tmp_path):
    # The runbook sends the operator to `cubby log --run ID` to move files back
    # by hand; a line naming only the old name pointed at the wrong file when
    # the name was taken and the file landed as "name (1)".
    downloads = tmp_path / "Downloads"
    (downloads / "Documents").mkdir(parents=True)
    (downloads / "Documents" / "notes.txt").write_text("older")
    (downloads / "notes.txt").write_text("newer")
    lines: list[str] = []

    Sorter(config_for(downloads), log=lambda message, **_: lines.append(message)).sort_once(
        apply=True
    )

    (line,) = lines
    assert line.endswith("notes.txt -> Documents/notes (1).txt")


def test_a_file_undo_could_not_restore_is_called_pending_not_skipped(tmp_path, monkeypatch):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    (downloads / "notes.txt").write_text("x")
    journal = Journal(tmp_path / "journal.jsonl")
    Sorter(config_for(downloads), journal=journal).sort_once(apply=True)

    def refuse(source, destination):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr("cubby.app.undo.move_no_clobber", refuse)
    lines: list[str] = []
    result = undo_run(journal, log=lines.append)

    (line,) = lines
    assert line.startswith("pending (cannot restore notes.txt)")
    assert f"cubby undo --run {result.run_id}" in line


class _Uninstalled:
    name = "fake"

    def uninstall(self, label: str = "com.cubby.agent") -> bool:
        return True


def test_uninstall_fails_when_a_cubby_is_still_sorting_afterwards(monkeypatch, capsys, agent_pid):
    # The service manager said the agent stopped, but a fresh heartbeat from a
    # live process says something still sorts: the operator must hear it.
    monkeypatch.setattr(cli_agent, "detect_service", _Uninstalled)
    _beat(agent_pid)

    assert main(["uninstall"]) == 1

    err = capsys.readouterr().err
    assert f"pid {agent_pid}" in err
    assert f"kill -TERM {agent_pid}" in err


def test_status_names_the_pid_of_a_running_agent(monkeypatch, capsys, agent_pid):
    class Running:
        name = "fake"

        def program_args(self, label: str = "com.cubby.agent") -> list[str] | None:
            return None

        def is_installed(self, label: str = "com.cubby.agent") -> bool:
            return True

        def is_running(self, label: str = "com.cubby.agent") -> bool:
            return True

        def unit_path(self, label: str):
            return "/tmp/fake.agent"

    monkeypatch.setattr(cli_agent, "detect_service", Running)
    _beat(agent_pid)

    main(["status"])

    assert f"running (fake, pid {agent_pid})" in capsys.readouterr().out


def test_uninstall_script_keeps_the_cli_when_the_agent_cannot_be_stopped(tmp_path):
    # Removing the CLI after a failed `cubby uninstall` left a running agent and
    # no command to stop it with.
    home = tmp_path / "home"
    venv = home / ".local" / "share" / "cubby" / "venv"
    venv.mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "cubby"
    fake.write_text("#!/bin/sh\necho 'cubby: still running' >&2\nexit 1\n")
    fake.chmod(0o755)
    script = Path(__file__).resolve().parents[1] / "uninstall.sh"

    completed = subprocess.run(
        ["/bin/sh", str(script)],
        env={"HOME": str(home), "PATH": f"{bin_dir}:/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode != 0
    assert venv.exists()
    assert "cubby removed" not in completed.stdout
