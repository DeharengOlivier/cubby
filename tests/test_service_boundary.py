"""The boundary with launchd and systemd: what is written, and what is believed.

Reproducers for audit 1: the systemd command line was built by joining the
arguments with spaces, so a folder named "My Downloads" became two arguments,
and `cubby install` reported success whatever the service manager answered.
"""

from __future__ import annotations

import plistlib
import shlex
import subprocess
from pathlib import Path

import pytest

from cubby import cli
from cubby.adapters.service import ServiceError, ServiceSpec, get_service
from cubby.adapters.service import launchd as launchd_mod
from cubby.adapters.service import systemd as systemd_mod
from cubby.adapters.service.launchd import LaunchdService
from cubby.adapters.service.systemd import SystemdService
from cubby.cli import EXIT_FAILED, main


class FakeManager:
    """Stands in for launchctl/systemctl: records calls, answers as told."""

    def __init__(self, *, fail_on: str | None = None, active: bool = True) -> None:
        self.calls: list[list[str]] = []
        self.fail_on = fail_on
        self.active = active

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        assert kwargs.get("timeout"), f"no timeout on {cmd}"
        joined = " ".join(cmd)
        if self.fail_on and self.fail_on in joined:
            return subprocess.CompletedProcess(cmd, 1, "", "Failed: unit not found")
        if "is-active" in cmd:
            state = "active" if self.active else "inactive"
            return subprocess.CompletedProcess(cmd, 0 if self.active else 3, state + "\n", "")
        if cmd[:2] == ["launchctl", "list"] and not self.active:
            return subprocess.CompletedProcess(cmd, 113, "", "Could not find service")
        if cmd[:2] == ["launchctl", "list"]:
            return subprocess.CompletedProcess(cmd, 0, '{\n\t"PID" = 4242;\n};\n', "")
        return subprocess.CompletedProcess(cmd, 0, "", "")


@pytest.fixture
def unit_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(systemd_mod, "_UNIT_DIR", tmp_path / "systemd")
    monkeypatch.setattr(launchd_mod, "_AGENTS_DIR", tmp_path / "LaunchAgents")
    return tmp_path


def _exec_start(unit: Path) -> str:
    line = next(x for x in unit.read_text().splitlines() if x.startswith("ExecStart="))
    return line.removeprefix("ExecStart=")


@pytest.mark.parametrize(
    "folder",
    ["/home/x/My Downloads", '/home/x/quote"d', "/home/x/back\\slash", "/home/x/tab\there"],
)
def test_a_folder_reaches_systemd_as_one_argument(unit_dirs, monkeypatch, folder):
    monkeypatch.setattr("subprocess.run", FakeManager())
    args = ["/usr/bin/cubby", "watch", "--source", folder]

    unit = SystemdService().install(ServiceSpec(program_args=args, log_path=unit_dirs / "log"))

    # systemd's quoting for double-quoted words matches POSIX shell quoting.
    assert shlex.split(_exec_start(unit)) == args


def test_a_newline_cannot_end_the_exec_line(unit_dirs, monkeypatch):
    monkeypatch.setattr("subprocess.run", FakeManager())
    args = ["/usr/bin/cubby", "watch", "--source", "/x\nExecStartPre=/bin/evil"]

    unit = SystemdService().install(ServiceSpec(program_args=args, log_path=unit_dirs / "log"))

    lines = unit.read_text().splitlines()
    assert not any(line.startswith("ExecStartPre") for line in lines)
    assert '"/x\\nExecStartPre=/bin/evil"' in _exec_start(unit)


def test_systemd_specifiers_and_variables_are_escaped(unit_dirs, monkeypatch):
    monkeypatch.setattr("subprocess.run", FakeManager())
    args = ["/usr/bin/cubby", "watch", "--source", "/home/x/100% $HOME"]

    unit = SystemdService().install(ServiceSpec(program_args=args, log_path=unit_dirs / "log"))

    exec_start = _exec_start(unit)
    assert "100%% $$HOME" in exec_start
    unescaped = exec_start.replace("%%", "%").replace("$$", "$")
    assert shlex.split(unescaped) == args


def test_systemd_install_fails_loudly_when_the_manager_refuses(unit_dirs, monkeypatch):
    monkeypatch.setattr("subprocess.run", FakeManager(fail_on="enable"))

    with pytest.raises(ServiceError, match="unit not found"):
        SystemdService().install(ServiceSpec(program_args=["/usr/bin/cubby", "watch"]))


def test_systemd_install_fails_when_the_agent_does_not_start(unit_dirs, monkeypatch):
    monkeypatch.setattr("subprocess.run", FakeManager(active=False))
    monkeypatch.setattr(systemd_mod, "_START_TIMEOUT", 0.05)

    with pytest.raises(ServiceError, match="not running"):
        SystemdService().install(ServiceSpec(program_args=["/usr/bin/cubby", "watch"]))


def test_launchd_install_fails_when_the_agent_does_not_load(unit_dirs, monkeypatch):
    monkeypatch.setattr("subprocess.run", FakeManager(active=False))
    monkeypatch.setattr(launchd_mod, "_START_TIMEOUT", 0.05)

    with pytest.raises(ServiceError, match="not running"):
        LaunchdService().install(ServiceSpec(program_args=["/usr/bin/cubby", "watch"]))


def test_launchd_install_writes_the_arguments_verbatim(unit_dirs, monkeypatch):
    monkeypatch.setattr("subprocess.run", FakeManager())
    args = ["/usr/bin/cubby", "watch", "--source", "/Users/x/My Downloads"]

    path = LaunchdService().install(ServiceSpec(program_args=args, log_path=unit_dirs / "log"))

    with path.open("rb") as handle:
        assert plistlib.load(handle)["ProgramArguments"] == args


def test_is_running_asks_the_manager_not_the_unit_file(unit_dirs, monkeypatch):
    manager = FakeManager(active=False)
    monkeypatch.setattr("subprocess.run", manager)
    service = SystemdService()
    service.unit_path("com.cubby.agent").parent.mkdir(parents=True)
    service.unit_path("com.cubby.agent").write_text("[Service]\n")

    assert service.is_installed()
    assert not service.is_running()


def test_cli_install_reports_a_refusal_and_exits_non_zero(unit_dirs, tmp_path, monkeypatch, capsys):
    source = tmp_path / "Downloads"
    source.mkdir()
    monkeypatch.setattr("subprocess.run", FakeManager(fail_on="enable"))
    monkeypatch.setattr(cli, "get_service", SystemdService)

    assert main(["install", "--source", str(source)]) == EXIT_FAILED

    captured = capsys.readouterr()
    assert "Installed" not in captured.out
    assert "unit not found" in captured.err


def test_a_hung_manager_is_a_service_error_not_a_hang(monkeypatch):
    from cubby.adapters.service.base import run_manager

    def hung(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

    monkeypatch.setattr("subprocess.run", hung)
    with pytest.raises(ServiceError, match="timed out"):
        run_manager(["systemctl", "--user", "daemon-reload"])
    assert not SystemdService().is_running()
    assert not LaunchdService().is_running()


def test_no_service_manager_is_a_clear_error(monkeypatch, capsys, tmp_path):
    source = tmp_path / "Downloads"
    source.mkdir()
    monkeypatch.setattr("cubby.adapters.service.factory.detect_service", lambda: None)
    monkeypatch.setattr(cli, "get_service", get_service)

    assert main(["install", "--source", str(source)]) == EXIT_FAILED
    assert "no supported service manager" in capsys.readouterr().err


def test_launchd_uninstall_fails_loudly_if_the_agent_survives(unit_dirs, monkeypatch):
    monkeypatch.setattr("subprocess.run", FakeManager())
    service = LaunchdService()
    service.install(ServiceSpec(program_args=["/usr/bin/cubby", "watch"], log_path=unit_dirs / "l"))

    with pytest.raises(ServiceError, match="still running"):
        service.uninstall()


def test_systemd_uninstall_reports_a_refusal(unit_dirs, monkeypatch):
    monkeypatch.setattr("subprocess.run", FakeManager())
    service = SystemdService()
    service.install(ServiceSpec(program_args=["/usr/bin/cubby", "watch"], log_path=unit_dirs / "l"))
    monkeypatch.setattr("subprocess.run", FakeManager(fail_on="disable"))

    with pytest.raises(ServiceError, match="unit not found"):
        service.uninstall()
