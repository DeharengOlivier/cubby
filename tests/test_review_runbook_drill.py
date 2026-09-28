"""Reproducers for the code defects found by executing docs/RUNBOOK.md.

An operator following the runbook in a throwaway home found that a failed undo
left a second name for the file, that `cubby uninstall` on Linux reported
success while the agent kept running, and that `cubby status` said "not
installed" about an agent that was still sorting.
"""

from __future__ import annotations

import json
import os
from datetime import datetime

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
from tests.helpers import config_for
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


def test_status_names_a_cubby_that_still_sorts_without_an_agent(monkeypatch, capsys):
    monkeypatch.setattr(cli_agent, "detect_service", lambda: None)
    _beat(os.getpid())

    main(["status"])
    out = capsys.readouterr().out
    assert "not installed" in out
    assert f"pid {os.getpid()}" in out

    main(["status", "--json"])
    assert json.loads(capsys.readouterr().out)["agent"]["live_pid"] == os.getpid()


def test_status_ignores_the_heartbeat_of_a_process_that_is_gone(monkeypatch, capsys):
    monkeypatch.setattr(cli_agent, "detect_service", lambda: None)
    _beat(2**22 + 12345)  # above the default pid_max: no such process

    main(["status", "--json"])
    assert json.loads(capsys.readouterr().out)["agent"]["live_pid"] is None
