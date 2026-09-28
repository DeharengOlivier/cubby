"""The agent's whole journey, as separate processes, the way a person meets it.

``cubby watch`` runs as a real child process (what launchd or systemd start),
sorts a download, reports itself healthy, is stopped with SIGTERM (what they
send on stop or logout), and a later ``cubby undo`` puts the file back.

Nothing here touches the real service manager or the user's state: the state
folder, the config and the watched folder are all temporary.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")

CONFIG = '[[category]]\nname = "Documents"\nextensions = ["txt"]\n'
DEADLINE = 20.0


def _cubby(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "cubby", *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=DEADLINE,
        check=False,
    )


def _wait_for(condition, what: str) -> None:
    deadline = time.monotonic() + DEADLINE
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.1)
    raise AssertionError(f"timed out waiting for {what}")


@pytest.fixture
def world(tmp_path):
    source = tmp_path / "Downloads"
    source.mkdir()
    config = tmp_path / "config.toml"
    config.write_text(CONFIG, encoding="utf-8")
    state = tmp_path / "state"
    env = {
        **os.environ,
        "HOME": str(tmp_path / "home"),
        "CUBBY_STATE_DIR": str(state),
        "CUBBY_CONFIG": str(config),
        "NO_COLOR": "1",
    }
    return source, state, env


def test_the_agent_sorts_reports_stops_cleanly_and_can_be_undone(world):
    source, state, env = world
    download = source / "notes.txt"
    download.write_text("hello", encoding="utf-8")
    errors = (state.parent / "agent-stderr.txt").open("w", encoding="utf-8")
    agent = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "cubby",
            "watch",
            "--source",
            str(source),
            "--delay",
            "0",
            "--interval",
            "1s",
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=errors,
    )
    try:
        filed = source / "Documents" / "notes.txt"
        _wait_for(filed.exists, "the agent to sort the download")
        _wait_for((state / "heartbeat.json").exists, "the first heartbeat")

        history = json.loads(_cubby(env, "history", "--json").stdout)["runs"]
        assert [(r["mode"], r["moved"], r["undo"]) for r in history] == [("watch", 1, "undoable")]

        agent.send_signal(signal.SIGTERM)
        code = agent.wait(timeout=DEADLINE)
    finally:
        if agent.poll() is None:
            agent.kill()
            agent.wait()
        errors.close()

    assert code == 0, (state.parent / "agent-stderr.txt").read_text(encoding="utf-8")
    log = next(p for p in state.rglob("*.log") if p.is_file())
    assert "cubby stopped" in log.read_text(encoding="utf-8")

    undo = _cubby(env, "undo")
    assert undo.returncode == 0, undo.stderr
    assert download.read_text(encoding="utf-8") == "hello"
    assert not filed.exists()

    history = json.loads(_cubby(env, "history", "--json").stdout)["runs"]
    assert [r["undo"] for r in history if r["mode"] == "watch"] == ["undone"]
