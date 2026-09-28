"""Reproducers for the independent review of PR 33 (status readiness and run time)."""

from __future__ import annotations

import json
import os
import plistlib
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from cubby.adapters.ledger import Ledger, RunRecord, RunsWith
from cubby.adapters.service.launchd import LaunchdService
from cubby.adapters.service.systemd import SystemdService, quote_argument, unit_program_args
from cubby.app.activity import summarize
from cubby.app.sorter import Sorter
from cubby.app.watcher import Watcher
from cubby.cli import agent as cli_agent
from cubby.cli import main
from cubby.cli.common import EXIT_BAD_CONFIG, EXIT_FAILED, EXIT_OK
from tests.helpers import aged_file, config_for

NOW = datetime(2026, 9, 28, 12, 0, 0)
RECEIPTS = '[[category]]\nname = "Receipts"\nextensions = ["pdf"]\n'

# --- I1: status and doctor never run a module planted in the working folder ------


@pytest.mark.parametrize("command", ["status", "doctor"])
def test_a_module_planted_in_the_working_folder_is_not_run(tmp_path, command):
    folder = tmp_path / "Downloads"
    folder.mkdir()
    (tmp_path / "home").mkdir()
    marker = tmp_path / "planted-module-ran"
    for lib in ("docx", "pypdf", "openpyxl"):
        (folder / f"{lib}.py").write_text(f"open({str(marker)!r}, 'a').write({lib!r})\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("XDG_", "CUBBY_"))}
    env |= {"HOME": str(tmp_path / "home"), "CUBBY_STATE_DIR": str(tmp_path / "state")}

    # `python -m` puts the working folder first on sys.path: the Downloads
    # folder, when a person runs cubby from there.
    result = subprocess.run(
        [sys.executable, "-m", "cubby", command],
        cwd=folder,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert not marker.exists(), marker.read_text()
    assert result.returncode in (EXIT_OK, EXIT_FAILED), result.stderr


# --- I2: readiness is judged with the settings the agent runs with ------------------


class Installed:
    name = "fake"

    def __init__(self, args: list[str] | None = None) -> None:
        self.args = args

    def is_installed(self, label: str = "com.cubby.agent") -> bool:
        return True

    def is_running(self, label: str = "com.cubby.agent") -> bool:
        return True

    def unit_path(self, label: str) -> Path:
        return Path("/tmp/fake.agent")

    def program_args(self, label: str = "com.cubby.agent") -> list[str] | None:
        return self.args


def _status(monkeypatch, capsys, service, *extra: str) -> tuple[int, str]:
    monkeypatch.setattr(cli_agent, "detect_service", lambda: service)
    code = main(["status", *extra])
    return code, capsys.readouterr().out


def test_the_watch_command_records_what_it_runs_with(tmp_path, monkeypatch):
    source = tmp_path / "Downloads"
    source.mkdir()
    config = tmp_path / "agent.toml"
    config.write_text(RECEIPTS, "utf-8")
    real_run = Watcher.run
    monkeypatch.setattr(Watcher, "run", lambda self, stop: real_run(self, stop=stop, max_cycles=1))

    main(["watch", "--config", str(config), "--source", str(source), "--no-content"])

    beat = Ledger().heartbeat()
    assert beat is not None
    assert beat.runs_with == RunsWith(
        config=str(config.resolve()),
        folders=("Receipts", "_Unsorted"),
        unsorted_dir="_Unsorted",
        content_scan=False,
    )


def test_readiness_uses_the_agent_s_own_folders_and_content_flag(tmp_path, monkeypatch, capsys):
    source = tmp_path / "Downloads"
    aged_file(source, "Documents")  # a default category the agent does not use
    runs_with = RunsWith(
        config="/etc/agent.toml",
        folders=("Receipts", "_Unsorted"),
        unsorted_dir="_Unsorted",
        content_scan=False,
    )
    Ledger().beat(source, 30, runs_with=runs_with)

    code, out = _status(monkeypatch, capsys, Installed())

    assert code == EXIT_OK
    assert "ready           yes; checked with the settings the agent reported" in out
    assert "degraded" not in out

    aged_file(source, "Receipts")  # one the agent does use
    code, out = _status(monkeypatch, capsys, Installed())

    assert code == EXIT_FAILED
    assert "a file named Receipts is in the way" in out


def test_without_a_report_the_installed_unit_is_read(tmp_path, monkeypatch, capsys):
    source = tmp_path / "Downloads"
    aged_file(source, "Receipts")
    config = tmp_path / "agent.toml"
    config.write_text(RECEIPTS, "utf-8")
    unit = ["/bin/cubby", "watch", "--wait-for-source", "--config", str(config)]
    unit += ["--source", str(source), "--no-content"]

    code, out = _status(monkeypatch, capsys, Installed(unit))

    assert code == EXIT_FAILED
    assert "a file named Receipts is in the way" in out
    assert "checked with the installed agent's command line" in out
    payload = json.loads(_status(monkeypatch, capsys, Installed(unit), "--json")[1])
    assert payload["readiness"]["basis"] == "unit"
    assert payload["readiness"]["degraded"] == []


def test_without_a_report_or_a_unit_the_default_config_is_said(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "Downloads").mkdir()

    code, out = _status(monkeypatch, capsys, None)

    assert code == EXIT_OK
    assert "checked with the default config" in out


def test_a_report_from_an_older_agent_falls_back_to_the_unit(tmp_path, monkeypatch, capsys):
    source = tmp_path / "Downloads"
    aged_file(source, "Receipts")
    config = tmp_path / "agent.toml"
    config.write_text(RECEIPTS, "utf-8")
    Ledger().beat(source, 30)  # a 0.4 agent: no settings in its heartbeat

    code, out = _status(
        monkeypatch, capsys, Installed(["/bin/cubby", "watch", "--config", str(config)])
    )

    assert code == EXIT_FAILED
    assert "a file named Receipts is in the way" in out


@pytest.mark.parametrize(
    "arg", ["plain", "with space", 'quote"d', "back\\slash", "50%", "$HOME", "new\nline", ""]
)
def test_the_systemd_command_line_reads_back_unchanged(arg):
    line = " ".join(quote_argument(a) for a in ["/bin/cubby", "watch", "--source", arg])

    assert unit_program_args(f"[Service]\nExecStart={line}\n") == [
        "/bin/cubby",
        "watch",
        "--source",
        arg,
    ]


@pytest.mark.parametrize("text", ["", "[Service]\nType=simple\n", 'ExecStart="unclosed\n'])
def test_an_unreadable_systemd_unit_gives_no_command_line(text):
    assert unit_program_args(text) is None


def test_the_units_give_their_command_line(tmp_path, monkeypatch):
    plist = tmp_path / "agent.plist"
    plist.write_bytes(plistlib.dumps({"ProgramArguments": ["/bin/cubby", "watch"]}))
    service_file = tmp_path / "cubby.service"
    service_file.write_text('[Service]\nExecStart="/bin/cubby" "watch"\n', "utf-8")
    monkeypatch.setattr(LaunchdService, "unit_path", lambda self, label: plist)
    monkeypatch.setattr(SystemdService, "unit_path", lambda self, label: service_file)

    assert LaunchdService().program_args() == ["/bin/cubby", "watch"]
    assert SystemdService().program_args() == ["/bin/cubby", "watch"]
    plist.write_bytes(b"not a plist")
    assert LaunchdService().program_args() is None
    service_file.unlink()
    assert SystemdService().program_args() is None


# --- M1: only the agent's runs count toward its run time and backlog -----------------


def _run(run: str, mode: str, duration_ms: int, waiting: int) -> RunRecord:
    at = (NOW - timedelta(minutes=5)).isoformat(timespec="seconds")
    return RunRecord(run, mode, "/d", at, at, 1, 0, duration_ms=duration_ms, waiting=waiting)


def test_manual_runs_are_left_out_of_the_agent_s_figures():
    records = [_run("w", "watch", 10, 1), _run("m", "run", 90_000, 50)]

    day = summarize(records, since=NOW - timedelta(hours=24), now=NOW)

    assert day.pass_ms is not None
    assert (day.pass_ms.count, day.pass_ms.max) == (1, 10)
    assert day.backlog is not None
    assert day.backlog.peak == 1
    assert day.runs == 2  # the rate still counts every run


# --- M6: percentiles of the smallest samples ------------------------------------------


@pytest.mark.parametrize(
    ("durations", "expected"), [([7], (7, 7, 7)), ([9, 1], (1, 9, 9)), ([5, 5], (5, 5, 5))]
)
def test_percentiles_of_one_and_two_runs(durations, expected):
    records = [_run(f"r{i}", "watch", ms, 0) for i, ms in enumerate(durations)]

    latency = summarize(records, since=NOW - timedelta(hours=24), now=NOW).pass_ms

    assert latency is not None
    assert (latency.p50, latency.p95, latency.max) == expected


# --- M2: a damaged interval is no interval ------------------------------------------------


@pytest.mark.parametrize("interval", [-5, 0, "30", None, 1e400])
def test_a_heartbeat_with_a_damaged_interval_is_not_read(tmp_path, interval):
    ledger = Ledger(tmp_path)
    ledger.beat(tmp_path, 30)
    data = json.loads(ledger.heartbeat_path.read_text("utf-8"))
    data["interval"] = interval
    ledger.heartbeat_path.write_text(json.dumps(data), "utf-8")

    assert ledger.heartbeat() is None


# --- M3: a --config status cannot read is a config error ----------------------------------


@pytest.mark.parametrize("content", ["[settings\n", None])
def test_status_with_a_bad_or_missing_config_exits_2(tmp_path, monkeypatch, capsys, content):
    config = tmp_path / "config.toml"
    if content is not None:
        config.write_text(content, "utf-8")

    code, _ = _status(monkeypatch, capsys, Installed(), "--config", str(config))

    assert code == EXIT_BAD_CONFIG


def test_a_runs_with_report_survives_the_round_trip(tmp_path):
    runs_with = RunsWith(config=None, folders=("A",), unsorted_dir="_U", content_scan=True)
    ledger = Ledger(tmp_path)
    ledger.beat(tmp_path, 30, runs_with=runs_with)

    beat = ledger.heartbeat()

    assert beat is not None
    assert beat.runs_with == runs_with


def test_the_sorter_s_folders_are_what_the_watcher_reports(tmp_path):
    source = tmp_path / "Downloads"
    source.mkdir()
    ledger = Ledger(tmp_path / "state")
    runs_with = RunsWith.of(config_for(source), None)

    Watcher(Sorter(config_for(source)), 30, ledger=ledger, runs_with=runs_with).run(max_cycles=1)

    beat = ledger.heartbeat()
    assert beat is not None
    assert beat.runs_with == RunsWith(
        config=None,
        folders=("Documents", "_Unsorted"),
        unsorted_dir="_Unsorted",
        content_scan=False,
    )


def test_a_damaged_report_or_a_foreign_unit_falls_back_to_the_default(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("HOME", str(tmp_path))
    source = tmp_path / "Downloads"
    source.mkdir()
    bad = RunsWith(config=None, folders=("A",), unsorted_dir="../out", content_scan=True)
    Ledger().beat(source, 30, runs_with=bad)  # an unsorted folder cubby refuses

    code, out = _status(monkeypatch, capsys, Installed(["/bin/something-else", "--flag"]))

    assert code == EXIT_OK
    assert "checked with the default config" in out
