"""On macOS the agent and the user's shell read and write the same log.

``cubby install`` gives the agent the environment it bakes (launchd starts it
without the login shell's variables), while ``cubby log`` and ``cubby status``
run from a shell. Both must name the same file, and the unit must send the
agent's own output there too.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cubby.adapters import state
from cubby.cli import EXIT_OK, main
from cubby.cli import agent as cli_agent

_STATE_VARS = ("CUBBY_STATE_DIR", "XDG_STATE_HOME")


class _Recorder:
    name = "launchd"

    def __init__(self) -> None:
        self.spec = None

    def install(self, spec):  # type: ignore[no-untyped-def]
        self.spec = spec
        return Path("/unit")


@pytest.fixture
def macos_home(monkeypatch, tmp_path):
    monkeypatch.setattr(state.sys, "platform", "darwin")
    monkeypatch.setenv("HOME", str(tmp_path))
    for name in _STATE_VARS:
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "Downloads").mkdir()
    return tmp_path


def _install_then_agent(monkeypatch) -> tuple[Path, Path, Path]:
    """The shell's log, the unit's log and the log the agent writes."""
    recorder = _Recorder()
    monkeypatch.setattr(cli_agent, "get_service", lambda: recorder)
    shell = state.log_path()
    assert main(["install"]) == EXIT_OK
    assert recorder.spec is not None
    # The agent: the shell's state variables gone, the baked ones set.
    for name in _STATE_VARS:
        monkeypatch.delenv(name, raising=False)
    for name, value in recorder.spec.environment.items():
        monkeypatch.setenv(name, value)
    return shell, recorder.spec.log_path, state.log_path()


def test_by_default_everyone_uses_library_logs(macos_home, monkeypatch):
    shell, unit, agent = _install_then_agent(monkeypatch)

    assert shell == unit == agent == macos_home / "Library" / "Logs" / "cubby.log"


def test_with_xdg_state_home_everyone_still_agrees(macos_home, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(macos_home / "xdg"))

    shell, unit, agent = _install_then_agent(monkeypatch)

    assert shell == unit == agent == macos_home / "Library" / "Logs" / "cubby.log"


def test_a_state_folder_set_elsewhere_keeps_its_log_beside_it(macos_home, monkeypatch):
    # Named "cubby" like the default one, but somewhere else.
    monkeypatch.setenv("CUBBY_STATE_DIR", str(macos_home / "elsewhere" / "cubby"))

    shell, unit, agent = _install_then_agent(monkeypatch)

    assert shell == unit == agent == macos_home / "elsewhere" / "cubby" / "cubby.log"


def test_the_default_folder_reached_through_a_symlink_is_the_default(macos_home, monkeypatch):
    real = macos_home / "real"
    (real / ".local" / "state").mkdir(parents=True)
    (macos_home / "link").symlink_to(real)
    monkeypatch.setenv("XDG_STATE_HOME", str(real / ".local" / "state"))
    monkeypatch.setenv("CUBBY_STATE_DIR", str(macos_home / "link" / ".local" / "state" / "cubby"))

    assert state.log_path() == macos_home / "Library" / "Logs" / "cubby.log"


def test_linux_keeps_the_log_in_the_state_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(state.sys, "platform", "linux")
    monkeypatch.setenv("CUBBY_STATE_DIR", str(tmp_path / "s"))

    assert state.log_path() == tmp_path / "s" / "cubby.log"
