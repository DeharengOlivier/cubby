"""Keep the test session away from the real home, whatever the code under test does.

A pytest plugin (conftest re-exports its hooks). For the whole session it points
HOME and the XDG folders at a fresh session folder, so code that falls back to
``Path.home()`` lands there, even a mutant that ignores every cubby variable.
And it fails the session when anything under the real home's cubby locations
appeared, changed or disappeared, which catches code that reaches the real
home by another road.

The real home is read once, when this module is imported, before any redirect.
Importing it has no other effect: the redirect starts in ``pytest_configure``.
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path

import pytest

#: The home of whoever runs the suite, before the session redirects it.
REAL_HOME = Path("~").expanduser()
#: The variables through which cubby can reach real state, as they were then.
REAL_ENV = {
    key: os.environ[key]
    for key in ("XDG_STATE_HOME", "XDG_CONFIG_HOME", "CUBBY_STATE_DIR", "CUBBY_CONFIG")
    if os.environ.get(key)
}

_SERVICE_LABEL = "com.cubby.agent"

Snapshot = dict[str, tuple[int, int]]

_BEFORE = pytest.StashKey[Snapshot]()
_SESSION_HOME = pytest.StashKey[Path]()
_PATCH = pytest.StashKey[pytest.MonkeyPatch]()


def real_state_locations(home: Path, env: Mapping[str, str]) -> list[Path]:
    """Every file or folder where cubby keeps state, config, log or agent for ``home``."""
    locations = [
        home / ".local" / "state" / "cubby",
        home / ".config" / "cubby",
        home / ".cubby.toml",
        home / "Library" / "Logs" / "cubby.log",
        home / ".config" / "systemd" / "user" / f"{_SERVICE_LABEL}.service",
        home / "Library" / "LaunchAgents" / f"{_SERVICE_LABEL}.plist",
    ]
    if env.get("XDG_STATE_HOME"):
        locations.append(Path(env["XDG_STATE_HOME"]).expanduser() / "cubby")
    if env.get("XDG_CONFIG_HOME"):
        locations.append(Path(env["XDG_CONFIG_HOME"]).expanduser() / "cubby")
    for key in ("CUBBY_STATE_DIR", "CUBBY_CONFIG"):
        if env.get(key):
            locations.append(Path(env[key]).expanduser())
    return locations


def snapshot(locations: Iterable[Path]) -> Snapshot:
    """Size and modification time of each location and everything below it.

    Reads only: a location that does not exist is left absent, not created.
    """
    taken: Snapshot = {}
    for location in locations:
        _record(taken, location)
        if location.is_dir():
            for folder, subfolders, files in os.walk(location):
                for name in (*subfolders, *files):
                    _record(taken, Path(folder) / name)
    return taken


def _record(taken: Snapshot, path: Path) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    taken[str(path)] = (info.st_size, info.st_mtime_ns)


def changes(before: Snapshot, after: Snapshot) -> list[str]:
    """What differs between two snapshots, one line per path, sorted."""
    found = [f"appeared: {path}" for path in after.keys() - before.keys()]
    found += [f"disappeared: {path}" for path in before.keys() - after.keys()]
    found += [
        f"changed: {path}" for path in before.keys() & after.keys() if before[path] != after[path]
    ]
    return sorted(found, key=lambda line: line.split(": ", 1)[1])


def _locations() -> list[Path]:
    return real_state_locations(REAL_HOME, REAL_ENV)


def pytest_configure(config: pytest.Config) -> None:
    config.stash[_BEFORE] = snapshot(_locations())
    home = Path(tempfile.mkdtemp(prefix="cubby-test-home-"))
    patch = pytest.MonkeyPatch()
    patch.setenv("HOME", str(home))
    patch.setenv("XDG_STATE_HOME", str(home / ".local" / "state"))
    patch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    patch.setenv("XDG_DATA_HOME", str(home / ".local" / "share"))
    # Code running outside a test (collection, imports) gets a session folder
    # too; conftest still gives each test its own.
    patch.setenv("CUBBY_STATE_DIR", str(home / "cubby-state"))
    patch.delenv("CUBBY_CONFIG", raising=False)
    config.stash[_SESSION_HOME] = home
    config.stash[_PATCH] = patch


def pytest_sessionfinish(session: pytest.Session) -> None:
    found = changes(session.config.stash[_BEFORE], snapshot(_locations()))
    if not found:
        return
    # Fails closed: a cubby agent writing to the real state during the run
    # trips it too. Stop the agent, or run the suite with a throwaway HOME.
    report = "\n".join(
        [
            "",
            f"real_state_guard: this session touched cubby state under {REAL_HOME}:",
            *(f"  {line}" for line in found),
        ]
    )
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_line(report, red=True, bold=True)
    else:
        print(report, file=sys.stderr)
    session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_unconfigure(config: pytest.Config) -> None:
    config.stash[_PATCH].undo()
    shutil.rmtree(config.stash[_SESSION_HOME])
