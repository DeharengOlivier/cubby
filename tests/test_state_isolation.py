"""The suite must never touch the state of the cubby installed on this machine.

Measured: 34 of the 38 entries in the developer's real undo journal
(~/.local/state/cubby/journal.jsonl) had been written by test runs, alongside
45 KB of test lines in ~/Library/Logs/cubby.log. A real `cubby undo` would have
replayed a test's temp-directory moves instead of the user's last real sort:
the tests had quietly broken the recovery mechanism they exist to protect.

It happened again during mutation testing: a mutant of `log_path()` that
ignored `CUBBY_STATE_DIR` sent "a line from the test suite" to the real
~/.local/state/cubby/cubby.log, and the guard of the time, which only looked
for the session's temp path in those files, let a line without a path through.

So the isolation no longer depends on the code under test reading its
variables correctly. `tests/real_state_guard.py`, loaded by conftest, points
HOME and the XDG folders at a session folder before any test runs, and fails
the whole session when a file under the real home's cubby locations appeared,
changed or disappeared. The per-test `CUBBY_STATE_DIR` in conftest stays on
top. These tests are what keeps both honest.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

from cubby.adapters import state
from cubby.adapters.journal import Entry, Journal
from cubby.adapters.logging import file_logger
from tests import real_state_guard as guard

_REPO = Path(__file__).resolve().parent.parent


def test_the_state_folder_is_redirected_away_from_home():
    assert Path.home() not in state.state_dir().parents
    assert Journal().path.parent == state.state_dir()


def test_the_log_is_redirected_away_from_home():
    assert Path.home() not in state.log_path().parents


def test_writing_a_move_lands_in_the_redirected_journal():
    journal = Journal()
    journal.record(Entry("r1", 0, "move", Path("/from/a.txt"), Path("/to/a.txt")))
    assert journal.path.exists()
    assert Path.home() not in journal.path.parents


def test_logging_lands_in_the_redirected_file():
    log = file_logger()
    log("a line from the test suite")
    assert "test suite" in state.log_path().read_text("utf-8")


def test_home_and_the_xdg_folders_point_at_a_session_folder():
    home = Path.home()
    assert home != guard.REAL_HOME
    # Path.home() and expanduser read HOME when called, so code that ignores
    # every cubby variable still lands in the session folder.
    assert Path("~").expanduser() == home == Path(os.environ["HOME"])
    assert os.environ["XDG_STATE_HOME"] == str(home / ".local" / "state")
    assert os.environ["XDG_DATA_HOME"] == str(home / ".local" / "share")


def test_the_default_state_folder_is_the_session_one(monkeypatch):
    # What a mutant falling back to the default folder would reach.
    monkeypatch.delenv("CUBBY_STATE_DIR")
    assert state.state_dir() == Path.home() / ".local" / "state" / "cubby"
    monkeypatch.delenv("XDG_STATE_HOME")
    assert state.state_dir() == Path.home() / ".local" / "state" / "cubby"


def test_the_real_locations_cover_state_config_log_and_agent(tmp_path):
    env = {
        "XDG_STATE_HOME": str(tmp_path / "xs"),
        "XDG_CONFIG_HOME": str(tmp_path / "xc"),
        "CUBBY_STATE_DIR": str(tmp_path / "cs"),
        "CUBBY_CONFIG": str(tmp_path / "cc.toml"),
    }
    locations = guard.real_state_locations(tmp_path / "h", env)
    for expected in (
        tmp_path / "h" / ".local" / "state" / "cubby",
        tmp_path / "h" / ".config" / "cubby",
        tmp_path / "h" / "Library" / "Logs" / "cubby.log",
        tmp_path / "xs" / "cubby",
        tmp_path / "xc" / "cubby",
        tmp_path / "cs",
        tmp_path / "cc.toml",
    ):
        assert expected in locations


def test_a_snapshot_creates_nothing(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    taken = guard.snapshot(guard.real_state_locations(home, {}))
    assert taken == {}
    assert list(home.iterdir()) == []


def test_an_untouched_home_shows_no_change(tmp_path):
    log = tmp_path / ".local" / "state" / "cubby" / "cubby.log"
    log.parent.mkdir(parents=True)
    log.write_text("the maintainer's own line\n")
    locations = guard.real_state_locations(tmp_path, {})
    before = guard.snapshot(locations)
    assert guard.changes(before, guard.snapshot(locations)) == []


def test_a_line_without_any_path_is_caught(tmp_path):
    # The line that escaped the old guard.
    log = tmp_path / ".local" / "state" / "cubby" / "cubby.log"
    log.parent.mkdir(parents=True)
    log.write_text("the maintainer's own line\n")
    locations = guard.real_state_locations(tmp_path, {})
    before = guard.snapshot(locations)
    with log.open("a") as handle:
        handle.write('{"msg": "a line from the test suite"}\n')
    assert guard.changes(before, guard.snapshot(locations)) == [f"changed: {log}"]


def test_a_new_state_folder_and_a_removed_file_are_caught(tmp_path):
    config = tmp_path / ".config" / "cubby" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text("")
    locations = guard.real_state_locations(tmp_path, {})
    before = guard.snapshot(locations)
    config.unlink()
    journal = tmp_path / ".local" / "state" / "cubby" / "journal.jsonl"
    journal.parent.mkdir(parents=True)
    journal.write_text("{}\n")
    found = guard.changes(before, guard.snapshot(locations))
    assert f"appeared: {journal}" in found
    assert f"appeared: {journal.parent}" in found
    assert f"disappeared: {config}" in found


def _run_session(tmp_path: Path, test_body: str) -> subprocess.CompletedProcess[str]:
    """Run a one-test pytest session with only the guard loaded, HOME=tmp_path/real."""
    real_home = tmp_path / "real"
    real_home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    (project / "pytest.ini").write_text("[pytest]\n")
    (project / "test_one.py").write_text(textwrap.dedent(test_body))
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("XDG_", "CUBBY_", "PYTEST_"))
    }
    env |= {"HOME": str(real_home), "PYTHONPATH": str(_REPO)}
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "tests.real_state_guard", "-p", "no:cacheprovider"],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_a_session_writing_to_the_real_home_fails(tmp_path):
    result = _run_session(
        tmp_path,
        """
        from tests.real_state_guard import REAL_HOME

        def test_writes_where_it_must_not():
            log = REAL_HOME / ".local" / "state" / "cubby" / "cubby.log"
            log.parent.mkdir(parents=True)
            log.write_text("a line from the test suite\\n")
        """,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "1 passed" in result.stdout
    real_log = tmp_path / "real" / ".local" / "state" / "cubby" / "cubby.log"
    assert f"appeared: {real_log}" in result.stdout + result.stderr


def test_a_session_using_the_default_folders_leaves_the_real_home_alone(tmp_path):
    result = _run_session(
        tmp_path,
        """
        from pathlib import Path

        def test_writes_under_home():
            log = Path.home() / ".local" / "state" / "cubby" / "cubby.log"
            log.parent.mkdir(parents=True)
            log.write_text("a line from the test suite\\n")
        """,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert list((tmp_path / "real").iterdir()) == []
