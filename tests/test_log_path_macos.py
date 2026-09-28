"""On macOS the agent and the user's shell read and write the same log.

``cubby install`` gives the agent ``CUBBY_STATE_DIR`` (launchd starts it
without the login shell's variables), while ``cubby log`` and ``cubby status``
run from a shell that does not set it. Both must name the same file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cubby.adapters import state


@pytest.fixture
def macos_home(monkeypatch, tmp_path):
    monkeypatch.setattr(state.sys, "platform", "darwin")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.delenv("CUBBY_STATE_DIR", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    return tmp_path


def test_the_agent_and_the_shell_use_the_same_log(macos_home, monkeypatch):
    shell = state.log_path()
    monkeypatch.setenv("CUBBY_STATE_DIR", str(state.state_dir()))  # what install bakes

    assert state.log_path() == shell == macos_home / "Library" / "Logs" / "cubby.log"


def test_a_state_folder_set_elsewhere_keeps_its_log_beside_it(macos_home, monkeypatch):
    monkeypatch.setenv("CUBBY_STATE_DIR", str(macos_home / "elsewhere"))

    assert state.log_path() == macos_home / "elsewhere" / "cubby.log"


def test_linux_keeps_the_log_in_the_state_folder(monkeypatch, tmp_path):
    monkeypatch.setattr(state.sys, "platform", "linux")
    monkeypatch.setenv("CUBBY_STATE_DIR", str(tmp_path / "s"))

    assert state.log_path() == tmp_path / "s" / "cubby.log"
