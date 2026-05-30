import plistlib

import pytest

from cubby.adapters.service import launchd as launchd_mod
from cubby.adapters.service import systemd as systemd_mod
from cubby.adapters.service.base import ServiceSpec
from cubby.adapters.service.factory import detect_service
from cubby.adapters.service.launchd import LaunchdService
from cubby.adapters.service.systemd import SystemdService


@pytest.fixture(autouse=True)
def _no_subprocess(monkeypatch):
    # Never actually call launchctl / systemctl in tests.
    monkeypatch.setattr("subprocess.run", lambda *a, **k: None)


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
    path = service.install(ServiceSpec(program_args=["/bin/cubby", "watch"]))
    body = path.read_text()
    assert "ExecStart=/bin/cubby watch" in body
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
