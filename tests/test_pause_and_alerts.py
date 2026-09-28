"""The pause switch and the agent's alerts.

``cubby pause`` is the switch to pull when cubby does something unexpected: the
agent stops moving files at its next pass, without the service manager. The
alerts are how an unattended agent reaches its user when it cannot sort.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from cubby.adapters import notify as notify_module
from cubby.adapters.ledger import Ledger
from cubby.adapters.pause import clear_pause, current_pause, pause_path, set_pause
from cubby.app.report import SortOutcome
from cubby.app.watcher import Watcher
from cubby.cli import EXIT_FAILED, EXIT_OK, main

# --- the switch ----------------------------------------------------------------


def test_pause_until_resumed(capsys):
    assert main(["pause"]) == EXIT_OK
    assert "cubby resume" in capsys.readouterr().out
    assert current_pause() is not None
    assert current_pause().until is None

    assert main(["resume"]) == EXIT_OK
    assert "Resumed" in capsys.readouterr().out
    assert current_pause() is None

    assert main(["resume"]) == EXIT_OK
    assert "Not paused" in capsys.readouterr().out


def test_a_timed_pause_ends_by_itself():
    pause = set_pause(3600, now=1000.0)
    assert pause.until == 4600.0
    assert current_pause(now=4599.0) is not None
    assert current_pause(now=4600.0) is None


@pytest.mark.parametrize("value", ["0", "0s", "-1", "soon"])
def test_pause_for_needs_a_positive_duration(value, capsys):
    with pytest.raises(SystemExit) as info:
        main(["pause", "--for", value])
    assert info.value.code == 2
    assert current_pause() is None


@pytest.mark.parametrize("content", ["not json", '{"v": 1}', '{"since": "x", "until": "later"}'])
def test_a_damaged_pause_file_still_pauses(content):
    # Fail closed: when cubby cannot tell whether it may move files, it does not.
    pause_path().parent.mkdir(parents=True, exist_ok=True)
    pause_path().write_text(content, encoding="utf-8")

    pause = current_pause()

    assert pause is not None
    assert pause.damaged
    assert "cubby resume" in pause.describe()
    assert clear_pause() is True
    assert current_pause() is None


def test_status_reports_the_pause(capsys, monkeypatch):
    monkeypatch.setattr("cubby.cli.detect_service", lambda: None)
    set_pause(None)

    main(["status", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["paused"]["until"] is None

    main(["status"])
    assert "no file is moved" in capsys.readouterr().out


def test_a_manual_run_while_paused_proceeds_and_says_so(tmp_path, capsys):
    (tmp_path / "a.txt").write_text("x")
    set_pause(None)

    code = main(["run", "--source", str(tmp_path), "--delay", "0"])

    assert code == EXIT_OK
    assert "this manual run proceeds" in capsys.readouterr().err
    assert not (tmp_path / "a.txt").exists()


# --- the agent honours it ------------------------------------------------------


class FakeSorter:
    def __init__(self, source: Path, outcomes=None, error: Exception | None = None):
        self.source = source
        self.passes = 0
        self._outcomes = outcomes or []
        self._error = error

    def sort_once(self, *, apply: bool, stop=None):
        self.passes += 1
        if self._error:
            raise self._error
        return self._outcomes


def _watcher(sorter, **kwargs):
    logs: list[str] = []
    alerts: list[str] = []
    watcher = Watcher(
        sorter,
        1.0,
        sleep=lambda _: None,
        log=lambda message, level="INFO": logs.append(message),
        alert=alerts.append,
        **kwargs,
    )
    return watcher, logs, alerts


def test_a_paused_agent_moves_nothing_but_stays_alive(tmp_path):
    sorter = FakeSorter(tmp_path)
    states = iter(["paused since now", "paused since now", None])
    ledger = Ledger()
    watcher, logs, _ = _watcher(sorter, paused=lambda: next(states), ledger=ledger)

    watcher.run(max_cycles=3)

    assert sorter.passes == 1  # only after the resume
    assert ledger.heartbeat() is not None  # a pause is not a stall
    assert [line for line in logs if "paused" in line or line == "resumed"] == [
        "paused since now: passes skipped until 'cubby resume'",
        "resumed",
    ]


def test_the_real_agent_stops_at_a_pause_and_sorts_after_resume(tmp_path):
    source = tmp_path / "Downloads"
    source.mkdir()
    env = {
        "HOME": str(tmp_path / "home"),
        "CUBBY_STATE_DIR": str(pause_path().parent),
        "PATH": "/usr/bin:/bin",
    }
    download = source / "notes.txt"
    download.write_text("x")
    set_pause(None)

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
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 20
        while not (pause_path().parent / "heartbeat.json").exists():
            assert time.monotonic() < deadline, "the paused agent never beat"
            time.sleep(0.1)
        time.sleep(1.5)  # at least one more pass while paused
        assert download.exists()

        clear_pause()
        filed = source / "Documents" / "notes.txt"
        while not filed.exists():
            assert time.monotonic() < deadline + 20, "the agent did not resume"
            time.sleep(0.1)
    finally:
        agent.terminate()
        agent.wait(timeout=20)


# --- alerts ----------------------------------------------------------------------


def test_a_missing_folder_is_announced_once(tmp_path):
    sorter = FakeSorter(tmp_path / "unplugged")
    watcher, _, alerts = _watcher(sorter)

    watcher.run(max_cycles=3)

    assert len(alerts) == 1
    assert "is missing" in alerts[0]


def test_a_failing_pass_is_announced_once_per_streak(tmp_path):
    sorter = FakeSorter(tmp_path, error=OSError(5, "I/O error"))
    watcher, _, alerts = _watcher(sorter)

    watcher.run(max_cycles=3)
    assert len(alerts) == 1
    assert alerts[0].startswith("Sorting failed:")

    sorter._error = None
    watcher.run(max_cycles=1)
    sorter._error = OSError(5, "I/O error")
    watcher.run(max_cycles=1)
    assert len(alerts) == 2


def test_each_file_that_cannot_be_sorted_is_announced_once(tmp_path):
    failed = [SortOutcome.failed(tmp_path / f"f{i}.pdf", "Permission denied") for i in range(5)]
    sorter = FakeSorter(tmp_path, outcomes=failed)
    watcher, _, alerts = _watcher(sorter)

    watcher.run(max_cycles=3)

    assert alerts == ["Could not sort f0.pdf, f1.pdf, f2.pdf and 2 more. See 'cubby status'."]


# --- the notification channel ------------------------------------------------------


def test_macos_passes_the_message_as_an_argument_not_as_script(monkeypatch):
    monkeypatch.setattr(notify_module.shutil, "which", lambda name: f"/usr/bin/{name}")
    message = 'a "quoted" name"; do shell script "rm -rf ~"'

    cmd = notify_module.command(message, platform="darwin")

    assert cmd is not None
    assert cmd[0] == "/usr/bin/osascript"
    assert cmd[-1] == message
    assert all(message not in part for part in cmd[:-1])


def test_linux_uses_notify_send(monkeypatch):
    monkeypatch.setattr(notify_module.shutil, "which", lambda name: f"/usr/bin/{name}")
    assert notify_module.command("hello", platform="linux") == [
        "/usr/bin/notify-send",
        "--app-name",
        "cubby",
        "cubby",
        "hello",
    ]


def test_a_long_message_is_shortened(monkeypatch):
    monkeypatch.setattr(notify_module.shutil, "which", lambda name: f"/usr/bin/{name}")
    cmd = notify_module.command("x" * 1000, platform="linux")
    assert cmd is not None
    assert len(cmd[-1]) == notify_module.MAX_CHARS


def test_no_tool_is_warned_once_and_never_raises(monkeypatch):
    monkeypatch.setattr(notify_module.shutil, "which", lambda name: None)
    warnings: list[str] = []
    notify = notify_module.notifier(True, warn=warnings.append)

    notify("one")
    notify("two")

    assert len(warnings) == 1
    assert "no notification tool" in warnings[0]


@pytest.mark.parametrize(
    "outcome",
    [
        subprocess.CompletedProcess([], 1, "", "no session bus"),
        subprocess.TimeoutExpired("notify-send", 5),
        FileNotFoundError(2, "No such file"),
    ],
)
def test_a_failing_notification_is_warned_and_never_raises(monkeypatch, outcome):
    monkeypatch.setattr(notify_module.shutil, "which", lambda name: f"/usr/bin/{name}")

    def run(*args, **kwargs):
        assert kwargs["timeout"] == notify_module.TIMEOUT
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(notify_module.subprocess, "run", run)
    warnings: list[str] = []

    notify_module.notifier(True, warn=warnings.append)("hello")

    assert len(warnings) == 1
    assert warnings[0].startswith("notification failed")


def test_notifications_can_be_turned_off(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("no notification may be sent")

    monkeypatch.setattr(notify_module.subprocess, "run", refuse)
    notify_module.notifier(False)("hello")


def test_doctor_can_send_a_test_notification(monkeypatch, tmp_path, capsys):
    sent: list[str] = []

    def fake_notifier(enabled, *, warn):
        return sent.append

    monkeypatch.setattr("cubby.cli.notifier", fake_notifier)
    assert main(["doctor", "--source", str(tmp_path), "--notify"]) == EXIT_OK
    assert sent == ["Test notification: cubby can reach you."]

    def broken_notifier(enabled, *, warn):
        return lambda message: warn("no notification tool (osascript or notify-send) found")

    monkeypatch.setattr("cubby.cli.notifier", broken_notifier)
    assert main(["doctor", "--source", str(tmp_path), "--notify"]) == EXIT_FAILED
    assert "notification test failed" in capsys.readouterr().err


def test_notify_is_a_setting(tmp_path):
    from cubby.adapters.config import load_config

    config = tmp_path / "c.toml"
    config.write_text("[settings]\nnotify = false\n", encoding="utf-8")
    assert load_config(user_path=config).settings.notify is False
    assert load_config(user_path=None).settings.notify is True
