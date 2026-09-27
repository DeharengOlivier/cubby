import plistlib
import subprocess

import pytest

from cubby.adapters.service import launchd as launchd_mod
from cubby.adapters.service import systemd as systemd_mod
from cubby.adapters.service.base import ServiceSpec
from cubby.adapters.service.factory import detect_service
from cubby.adapters.service.launchd import LaunchdService
from cubby.adapters.service.systemd import SystemdService


class HealthyManager:
    """launchctl / systemctl stand-in: every command succeeds, and the agent
    runs exactly while it is loaded (launchd) or started (systemd)."""

    def __init__(self) -> None:
        self.running = False

    def __call__(self, cmd, **kwargs):
        if cmd[:2] == ["launchctl", "load"] or "restart" in cmd:
            self.running = True
        elif cmd[:2] == ["launchctl", "unload"] or "disable" in cmd:
            self.running = False
        elif "is-active" in cmd:
            state = "active" if self.running else "inactive"
            return subprocess.CompletedProcess(cmd, 0 if self.running else 3, state + "\n", "")
        elif cmd[:2] == ["launchctl", "list"]:
            if not self.running:
                return subprocess.CompletedProcess(cmd, 113, "", "Could not find service")
            return subprocess.CompletedProcess(cmd, 0, '"PID" = 1;', "")
        return subprocess.CompletedProcess(cmd, 0, "", "")


@pytest.fixture(autouse=True)
def _no_subprocess(monkeypatch):
    # Never actually call launchctl / systemctl in tests.
    monkeypatch.setattr("subprocess.run", HealthyManager())


def test_launchd_writes_valid_plist(tmp_path, monkeypatch):
    monkeypatch.setattr(launchd_mod, "_AGENTS_DIR", tmp_path / "LaunchAgents")
    spec = ServiceSpec(program_args=["/bin/cubby", "watch"], log_path=tmp_path / "log")
    path = LaunchdService().install(spec)
    assert path.exists()
    with path.open("rb") as handle:
        plist = plistlib.load(handle)
    assert plist["Label"] == "com.cubby.agent"
    assert plist["ProgramArguments"] == ["/bin/cubby", "watch"]
    assert plist["RunAtLoad"] is True


def test_launchd_uninstall_missing_returns_false(tmp_path, monkeypatch):
    monkeypatch.setattr(launchd_mod, "_AGENTS_DIR", tmp_path / "LaunchAgents")
    assert LaunchdService().uninstall("com.cubby.agent") is False


def test_systemd_unit_name_and_render(tmp_path, monkeypatch):
    monkeypatch.setattr(systemd_mod, "_UNIT_DIR", tmp_path / "user")
    service = SystemdService()
    assert service._unit_name("com.cubby.agent") == "cubby.service"
    path = service.install(
        ServiceSpec(program_args=["/bin/cubby", "watch"], log_path=tmp_path / "l")
    )
    body = path.read_text()
    assert 'ExecStart="/bin/cubby" "watch"' in body
    assert "Restart=on-failure" in body


def test_detect_service_selects_by_platform(monkeypatch):
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/" + name)
    assert isinstance(detect_service(), LaunchdService)

    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setattr(
        "shutil.which",
        lambda name: "/usr/bin/systemctl" if name == "systemctl" else None,
    )
    assert isinstance(detect_service(), SystemdService)

    monkeypatch.setattr("shutil.which", lambda name: None)
    assert detect_service() is None


def test_launchd_uninstall_removes_the_agent_file(tmp_path, monkeypatch):
    monkeypatch.setattr(launchd_mod, "_AGENTS_DIR", tmp_path / "LaunchAgents")
    service = LaunchdService()
    path = service.install(
        ServiceSpec(program_args=["/bin/cubby", "watch"], log_path=tmp_path / "l")
    )

    assert service.uninstall() is True
    assert not path.exists()


def test_systemd_uninstall_removes_the_unit(tmp_path, monkeypatch):
    monkeypatch.setattr(systemd_mod, "_UNIT_DIR", tmp_path / "user")
    service = SystemdService()
    path = service.install(
        ServiceSpec(program_args=["/bin/cubby", "watch"], log_path=tmp_path / "l")
    )

    assert service.uninstall() is True
    assert not path.exists()
    assert service.uninstall() is False
