"""Where cubby keeps its state, per platform and environment."""

from __future__ import annotations

from pathlib import Path

import pytest

from cubby.adapters import state


def test_an_explicit_state_folder_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("CUBBY_STATE_DIR", str(tmp_path / "s"))
    assert state.state_dir() == tmp_path / "s"
    assert state.log_path() == tmp_path / "s" / "cubby.log"


def test_xdg_state_home_is_honored(monkeypatch, tmp_path):
    monkeypatch.delenv("CUBBY_STATE_DIR")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg"))
    assert state.state_dir() == tmp_path / "xdg" / "cubby"


def test_the_default_is_under_the_home_folder(monkeypatch, tmp_path):
    monkeypatch.delenv("CUBBY_STATE_DIR")
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert state.state_dir() == tmp_path / ".local" / "state" / "cubby"


@pytest.mark.parametrize(
    ("platform", "expected"),
    [("darwin", Path("Library/Logs/cubby.log")), ("linux", Path(".local/state/cubby/cubby.log"))],
)
def test_the_log_goes_where_the_platform_looks(monkeypatch, tmp_path, platform, expected):
    monkeypatch.delenv("CUBBY_STATE_DIR")
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(state.sys, "platform", platform)
    assert state.log_path() == tmp_path / expected


def test_bounding_a_small_file_leaves_it_alone(tmp_path):
    path = tmp_path / "f"
    state.append_line(path, "one")
    state.keep_last_lines(path, max_bytes=1000, keep=1)
    state.keep_last_lines(tmp_path / "absent", max_bytes=1, keep=1)
    assert state.read_lines(path) == ["one"]
