"""The suite must never touch the state of the cubby installed on this machine.

Measured: 34 of the 38 entries in the developer's real undo journal
(~/.local/state/cubby/journal.jsonl) had been written by test runs, alongside
45 KB of test lines in ~/Library/Logs/cubby.log. A real `cubby undo` would have
replayed a test's temp-directory moves instead of the user's last real sort:
the tests had quietly broken the recovery mechanism they exist to protect.

The isolation is a fixture in conftest. These tests are what keeps it honest.
"""

from __future__ import annotations

from pathlib import Path

from cubby.adapters import journal as journal_module
from cubby.adapters import logging as logging_module
from cubby.adapters.journal import Journal
from cubby.adapters.logging import file_logger


def _real_home_paths() -> list[Path]:
    return [
        Path.home() / ".local" / "state" / "cubby" / "journal.jsonl",
        Path.home() / "Library" / "Logs" / "cubby.log",
    ]


def test_the_default_journal_is_redirected_away_from_home():
    default = journal_module.DEFAULT_JOURNAL
    assert Path.home() not in default.parents, default
    assert Journal().path == default


def test_the_default_log_is_redirected_away_from_home():
    default = logging_module.DEFAULT_LOG
    assert Path.home() not in default.parents, default


def test_writing_a_run_lands_in_the_redirected_journal():
    journal = Journal()
    journal.record_run([(Path("/from/a.txt"), Path("/to/a.txt"))])
    assert journal.path.exists()
    assert Path.home() not in journal.path.parents


def test_logging_lands_in_the_redirected_file():
    log = file_logger()
    log("a line from the test suite")
    assert logging_module.DEFAULT_LOG.read_text("utf-8").strip().endswith("test suite")


def test_this_session_wrote_nothing_to_the_real_state_files(tmp_path_factory):
    # Precise rather than flaky: a cubby agent may legitimately be running on
    # this machine and writing to these files. What must never appear in them is
    # a path from this test session's temp directory.
    session_tmp = str(tmp_path_factory.getbasetemp())
    for path in _real_home_paths():
        if not path.exists():
            continue
        assert session_tmp not in path.read_text("utf-8", errors="ignore"), (
            f"{path} contains paths from this test session"
        )
