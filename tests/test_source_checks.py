"""Commands that act on the source folder refuse a folder that is not there.

Reproducer for audit 1: `cubby watch --source /nonexistent` polled an absent
folder forever without a word, and `cubby install` baked it into an agent.
"""

from __future__ import annotations

import pytest

from cubby import cli
from cubby.cli import EXIT_FAILED, main


class ExplodingWatcher:
    def __init__(self, *args, **kwargs):
        raise AssertionError("the watch loop must not start on a missing folder")


@pytest.mark.parametrize("command", ["watch", "install", "run", "plan"])
def test_a_missing_source_is_refused_before_anything_starts(command, tmp_path, monkeypatch, capsys):
    missing = tmp_path / "gone"
    monkeypatch.setattr(cli, "Watcher", ExplodingWatcher)
    monkeypatch.setattr(cli, "get_service", lambda: pytest.fail("must not install"))

    assert main([command, "--source", str(missing)]) == EXIT_FAILED

    assert "does not exist" in capsys.readouterr().err
