"""The suite never reaches the developer's own cubby state, whatever a test does."""

from __future__ import annotations

import os
import socket
from pathlib import Path

import pytest

from cubby.adapters import config, state


def test_the_isolation_survives_monkeypatch_undo(monkeypatch):
    # A test that undid its monkeypatches used to undo the isolation with them,
    # and its later runs wrote to ~/.local/state/cubby.
    monkeypatch.setenv("UNRELATED", "1")
    monkeypatch.undo()

    assert os.environ.get("CUBBY_STATE_DIR")
    assert not state.state_dir().is_relative_to(Path.home() / ".local" / "state")
    assert config.find_user_config() is None
    with pytest.raises(AssertionError, match="network"):
        socket.create_connection(("127.0.0.1", 9))
