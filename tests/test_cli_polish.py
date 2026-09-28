"""Small CLI defects found by an exploratory session, each with its reproducer.

- ``watch -v`` printed nothing when its output was not a terminal.
- ``run --source ~/Downloads/Documents`` sorted a category folder into itself
  (``Documents/Documents/``).
- ``status`` said "watching" after the foreground watch had exited.
- ``doctor`` said "notifications on" with no tool able to show one.
- A missing source did not say whether it came from ``--source`` or a config.
- ``log --run`` with an unknown id printed nothing and exited 0.
- A bad ``--delay`` was reported as a config error without naming the flag.
- Two categories with the same name were accepted silently.
And ``$XDG_CONFIG_HOME/cubby/config.toml`` was not read, while the state
folder honours ``$XDG_STATE_HOME``.
"""

from __future__ import annotations

import subprocess
import sys
import time

import pytest

from cubby.adapters import config as config_module
from cubby.adapters import notify
from cubby.adapters.ledger import Ledger
from cubby.cli import agent as cli_agent
from cubby.cli import main
from tests.helpers import aged_file


def _cli(capsys, *args):
    code = main(list(args))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_watch_verbose_echoes_even_when_not_on_a_terminal(tmp_path):
    aged_file(tmp_path / "Downloads", "a.txt")
    env = {
        "HOME": str(tmp_path),
        "XDG_STATE_HOME": str(tmp_path / "state"),
        "PATH": "/usr/bin:/bin",
    }
    argv = [sys.executable, "-m", "cubby", "watch", "-v", "--source", str(tmp_path / "Downloads"),
            "--delay", "0", "--interval", "1s"]  # fmt: skip
    process = subprocess.Popen(argv, env=env, stdout=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 15
        while (tmp_path / "Downloads" / "a.txt").exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        process.terminate()
        out, _ = process.communicate(timeout=15)
    finally:
        process.kill()

    assert "cubby watching" in out
    assert "a.txt -> Documents/a.txt" in out


def test_sorting_a_category_folder_of_the_watched_folder_is_refused(tmp_path, capsys, monkeypatch):
    downloads = tmp_path / "Downloads"
    aged_file(downloads / "Documents", "q.txt")
    monkeypatch.setenv("HOME", str(tmp_path))

    code, _, err = _cli(capsys, "run", "--source", str(downloads / "Documents"))

    assert code == 2
    assert "is inside the watched folder" in err
    assert (downloads / "Documents" / "q.txt").exists()
    assert not (downloads / "Documents" / "Documents").exists()


def test_status_says_last_watched_when_nothing_is_sorting(capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(cli_agent, "detect_service", lambda: None)
    monkeypatch.setattr("os.getpid", lambda: 999_999_999)  # no such process
    Ledger().beat(tmp_path, 30.0)

    _, out, _ = _cli(capsys, "status")

    assert "last watched" in out
    assert "watching " not in out.replace("last watched", "")


def test_doctor_says_when_notifications_cannot_be_shown(capsys, monkeypatch):
    monkeypatch.setattr(notify, "command", lambda message: None)

    _, out, _ = _cli(capsys, "doctor")

    line = next(line for line in out.splitlines() if line.startswith("notifications"))
    assert "no notification tool" in line


def test_a_missing_source_says_where_it_came_from(tmp_path, capsys):
    config = tmp_path / "c.toml"
    config.write_text(f'[settings]\nsource = "{tmp_path / "Downlaods"}"\n', encoding="utf-8")

    _, _, from_config = _cli(capsys, "plan", "--config", str(config))
    _, _, from_flag = _cli(capsys, "plan", "--source", str(tmp_path / "nope"))

    assert f"source in {config}" in from_config
    assert "--source" in from_flag


def test_log_with_an_unknown_run_says_so(capsys):
    from cubby.adapters.logging import file_logger

    file_logger()("agent started")

    code, _, err = _cli(capsys, "log", "--run", "nope")

    assert code == 1
    assert "no log line for run 'nope'" in err


def test_a_bad_delay_names_the_flag(tmp_path, capsys):
    with pytest.raises(SystemExit) as caught:
        main(["plan", "--source", str(tmp_path), "--delay", "abc"])

    assert caught.value.code == 2
    assert "argument --delay: invalid duration: 'abc'" in capsys.readouterr().err


def test_two_categories_with_one_name_are_refused(tmp_path, capsys):
    config = tmp_path / "c.toml"
    config.write_text(
        '[[category]]\nname = "Images"\nextensions = ["foo"]\n'
        '[[category]]\nname = "Images"\nextensions = ["bar"]\n',
        encoding="utf-8",
    )

    code, _, err = _cli(capsys, "plan", "--config", str(config), "--source", str(tmp_path))

    assert code == 2
    assert "category 'Images' is defined twice" in err


def test_the_config_under_xdg_config_home_is_read(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    (tmp_path / "xdg" / "cubby").mkdir(parents=True)
    (tmp_path / "xdg" / "cubby" / "config.toml").write_text("", encoding="utf-8")

    # find_user_config is isolated by conftest; the candidates it walks are not.
    candidates = config_module.user_config_candidates()
    assert candidates[0] == tmp_path / "xdg" / "cubby" / "config.toml"
    assert config_module.default_user_config_path() == candidates[0]


def test_another_folder_inside_the_watched_one_can_be_sorted(tmp_path, capsys, monkeypatch):
    aged_file(tmp_path / "Downloads" / "inbox", "q.txt")
    monkeypatch.setenv("HOME", str(tmp_path))

    code, _, _ = _cli(capsys, "run", "--source", str(tmp_path / "Downloads" / "inbox"))

    assert code == 0
    assert (tmp_path / "Downloads" / "inbox" / "Documents" / "q.txt").exists()
