"""``cubby status``: is the agent live, is it ready, and how long do its passes take.

Liveness is the heartbeat: a process that still passes. Readiness is whether
that process can do its job now: the watched folder is there and can be read
and written, the state folder can be written, no file stands where a category
folder goes, and nothing paused it. A missing content converter only degrades
it. Latency and saturation come from the duration and backlog each run now
records in the ledger.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from cubby.adapters import extraction
from cubby.adapters.ledger import Ledger, RunRecord
from cubby.adapters.pause import set_pause
from cubby.app.activity import summarize
from cubby.app.readiness import check_readiness
from cubby.app.sorter import Sorter
from cubby.cli import agent as cli_agent
from cubby.cli import main
from cubby.cli.common import EXIT_FAILED, EXIT_OK
from tests.helpers import aged_file, config_for, process_named_cubby_watch

needs_permissions = pytest.mark.skipif(os.geteuid() == 0, reason="root ignores folder permissions")

ALL_CONVERTERS = dict.fromkeys(extraction.CONVERTERS, True)
NOW = datetime(2026, 9, 28, 12, 0, 0)


def _ready(source: Path, state: Path, *, paused=False, converters=None, **settings):
    config = config_for(source, **settings)
    return check_readiness(
        config.settings,
        config.managed_dirs,
        state_folder=state,
        paused=paused,
        converters=ALL_CONVERTERS if converters is None else converters,
    )


# --- readiness, checked on its own -----------------------------------------------


def test_a_usable_folder_is_ready(tmp_path):
    (tmp_path / "Downloads").mkdir()

    readiness = _ready(tmp_path / "Downloads", tmp_path / "state" / "not-yet")

    assert readiness.ready
    assert readiness.problems == ()
    assert readiness.degraded == ()


def test_a_missing_folder_is_not_ready(tmp_path):
    readiness = _ready(tmp_path / "gone", tmp_path)

    assert not readiness.ready
    assert readiness.problems == (f"source folder does not exist: {tmp_path / 'gone'}",)


def test_a_file_as_the_source_is_not_ready(tmp_path):
    aged_file(tmp_path, "Downloads")

    assert _ready(tmp_path / "Downloads", tmp_path).problems == (
        f"source is not a folder: {tmp_path / 'Downloads'}",
    )


@needs_permissions
@pytest.mark.parametrize(("mode", "what"), [(0o500, "writable"), (0o300, "readable")])
def test_a_folder_cubby_cannot_read_or_write_is_not_ready(tmp_path, mode, what):
    source = tmp_path / "Downloads"
    source.mkdir()
    source.chmod(mode)
    try:
        readiness = _ready(source, tmp_path)
    finally:
        source.chmod(0o700)

    assert readiness.problems == (f"source folder is not {what}: {source}",)


@needs_permissions
def test_a_folder_that_cannot_even_be_looked_at_is_not_ready(tmp_path):
    closed = tmp_path / "closed"
    (closed / "Downloads").mkdir(parents=True)
    closed.chmod(0o000)
    try:
        readiness = _ready(closed / "Downloads", tmp_path)
    finally:
        closed.chmod(0o700)

    assert len(readiness.problems) == 1
    assert readiness.problems[0].startswith(f"source folder cannot be checked: {closed}")


@needs_permissions
def test_a_state_folder_cubby_cannot_write_is_not_ready(tmp_path):
    (tmp_path / "Downloads").mkdir()
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        # The state folder does not exist yet: the folder it would be made in counts.
        readiness = _ready(tmp_path / "Downloads", locked / "cubby")
    finally:
        locked.chmod(0o700)

    assert readiness.problems == (f"state folder is not writable: {locked / 'cubby'}",)


def test_a_file_where_a_category_folder_goes_is_not_ready(tmp_path):
    aged_file(tmp_path / "Downloads", "Documents")

    readiness = _ready(tmp_path / "Downloads", tmp_path)

    assert readiness.problems == (
        "a file named Documents is in the way of the Documents/ folder cubby sorts into",
    )


def test_a_pause_is_not_ready_without_being_a_problem(tmp_path):
    (tmp_path / "Downloads").mkdir()

    readiness = _ready(tmp_path / "Downloads", tmp_path, paused=True)

    assert not readiness.ready
    assert readiness.paused
    assert readiness.problems == ()


def test_a_missing_converter_degrades_but_stays_ready(tmp_path):
    (tmp_path / "Downloads").mkdir()
    only_pdf = {name: name == "pdftotext" for name in extraction.CONVERTERS}

    readiness = _ready(tmp_path / "Downloads", tmp_path, converters=only_pdf, content_scan=True)

    assert readiness.ready
    assert readiness.degraded == ("doc", "docx", "rtf", "xlsx")


def test_without_content_scan_no_converter_is_missed(tmp_path):
    (tmp_path / "Downloads").mkdir()
    none = dict.fromkeys(extraction.CONVERTERS, False)

    assert _ready(tmp_path / "Downloads", tmp_path, converters=none).degraded == ()


def test_doctor_and_status_read_the_same_converters(monkeypatch):
    monkeypatch.setattr(extraction.shutil, "which", lambda name: None)

    present = extraction.converters_present()

    assert set(present) == set(extraction.CONVERTERS)
    assert not any(present[tool] for tool in ("pdftotext", "textutil", "antiword", "catdoc"))


# --- readiness in cubby status -------------------------------------------------------


class Running:
    name = "fake"

    def is_installed(self, label: str = "com.cubby.agent") -> bool:
        return True

    def is_running(self, label: str = "com.cubby.agent") -> bool:
        return True

    def unit_path(self, label: str) -> Path:
        return Path("/tmp/fake.agent")


def _status(monkeypatch, capsys, source: Path, *, installed: bool, extra=()) -> tuple[int, str]:
    monkeypatch.setattr(cli_agent, "detect_service", lambda: Running() if installed else None)
    Ledger().beat(source, 30)
    code = main(["status", *extra])
    return code, capsys.readouterr().out


def test_status_of_a_ready_agent(tmp_path, monkeypatch, capsys):
    (tmp_path / "Downloads").mkdir()

    code, out = _status(monkeypatch, capsys, tmp_path / "Downloads", installed=True)

    assert code == EXIT_OK
    assert "live            yes" in out
    assert "ready           yes" in out


def test_an_agent_watching_a_missing_folder_is_live_but_not_ready(tmp_path, monkeypatch, capsys):
    code, out = _status(monkeypatch, capsys, tmp_path / "gone", installed=True)

    assert code == EXIT_FAILED
    assert "live            yes" in out
    assert f"ready           no (source folder does not exist: {tmp_path / 'gone'})" in out


def test_not_ready_without_an_agent_keeps_exit_0(tmp_path, monkeypatch, capsys):
    code, out = _status(monkeypatch, capsys, tmp_path / "gone", installed=False)

    assert code == EXIT_OK
    assert "ready           no (source folder does not exist" in out


def test_a_paused_agent_is_not_ready_and_keeps_exit_0(tmp_path, monkeypatch, capsys):
    (tmp_path / "Downloads").mkdir()
    set_pause(3600)

    code, out = _status(monkeypatch, capsys, tmp_path / "Downloads", installed=True)

    assert code == EXIT_OK
    assert "ready           no (paused" in out


def test_a_broken_config_is_reported_as_not_ready(tmp_path, monkeypatch, capsys):
    (tmp_path / "Downloads").mkdir()
    bad = tmp_path / "config.toml"
    bad.write_text("[settings\n", "utf-8")

    code, out = _status(
        monkeypatch, capsys, tmp_path / "Downloads", installed=True, extra=["--config", str(bad)]
    )

    assert code == EXIT_FAILED
    assert "ready           no (config error:" in out


def test_the_not_ready_reason_is_escaped(tmp_path, monkeypatch, capsys):
    code, out = _status(monkeypatch, capsys, tmp_path / "gone\x1b[31m", installed=True)

    assert code == EXIT_FAILED
    assert "\x1b[31m" not in out
    assert "gone\\x1b[31m" in out


def test_a_live_process_without_a_unit_is_held_to_readiness_too(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli_agent, "detect_service", lambda: None)
    with process_named_cubby_watch() as pid:
        monkeypatch.setattr("os.getpid", lambda: pid)
        Ledger().beat(tmp_path / "gone", 30)
        code = main(["status", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert code == EXIT_FAILED
    assert payload["live"] is True
    assert payload["readiness"]["ready"] is False
    assert payload["healthy"] is False


# --- latency and saturation ------------------------------------------------------------


def _timed(run: str, minutes_ago: int, duration_ms: int | None, waiting: int | None = None):
    at = (NOW - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")
    return RunRecord(
        run=run,
        mode="watch",
        source="/d",
        started=at,
        finished=at,
        moved=1,
        failed=0,
        duration_ms=duration_ms,
        waiting=waiting,
    )


def test_pass_durations_of_the_day_give_p50_p95_and_max():
    records = [_timed(f"r{i}", i, duration_ms=(i + 1) * 10) for i in range(20)]
    records.append(_timed("old", 60 * 25, duration_ms=99_999))  # outside the window
    records.append(_timed("pre", 5, duration_ms=None))  # written by an older cubby

    day = summarize(records, since=NOW - timedelta(hours=24), now=NOW)

    assert day.pass_ms is not None
    assert (day.pass_ms.count, day.pass_ms.p50, day.pass_ms.p95, day.pass_ms.max) == (
        20,
        100,
        190,
        200,
    )


def test_the_backlog_trend_runs_from_the_oldest_to_the_newest_run():
    records = [
        _timed("a", 30, 5, waiting=1),
        _timed("b", 20, 5, waiting=9),
        _timed("c", 10, 5, waiting=None),
        _timed("d", 1, 5, waiting=4),
    ]

    day = summarize(records, since=NOW - timedelta(hours=24), now=NOW)

    assert day.backlog is not None
    assert (day.backlog.first, day.backlog.last, day.backlog.peak) == (1, 4, 9)


def test_no_timed_run_gives_no_latency():
    day = summarize([_timed("a", 1, None)], since=NOW - timedelta(hours=24), now=NOW)

    assert day.pass_ms is None
    assert day.backlog is None


def test_each_run_records_its_duration_and_backlog(tmp_path):
    source = tmp_path / "Downloads"
    aged_file(source, "notes.txt")
    aged_file(source, "fresh.txt", age=0)
    ledger = Ledger(tmp_path / "state")

    Sorter(config_for(source, delay=3600), ledger=ledger).sort_once(apply=True)

    (record,) = ledger.runs()
    assert record.duration_ms is not None
    assert record.duration_ms >= 0
    assert record.waiting == 1
    line = json.loads(ledger.runs_path.read_text("utf-8"))
    assert type(line["duration_ms"]) is int


def test_a_record_without_the_new_fields_still_reads(tmp_path):
    ledger = Ledger(tmp_path)
    old = _timed("r0", 1, None).to_json()
    del old["duration_ms"], old["waiting"]
    old["a_field_from_a_later_cubby"] = [1]
    ledger.runs_path.write_text(json.dumps(old) + "\n", "utf-8")

    (record,) = ledger.runs()

    assert (record.duration_ms, record.waiting) == (None, None)


@pytest.mark.parametrize("field", ["duration_ms", "waiting"])
@pytest.mark.parametrize("value", [-1, 1.5, "3", True])
def test_a_damaged_new_field_skips_the_record(tmp_path, field, value):
    ledger = Ledger(tmp_path)
    line = _timed("r0", 1, 5, waiting=0).to_json()
    line[field] = value
    ledger.runs_path.write_text(json.dumps(line) + "\n", "utf-8")

    assert ledger.runs() == []


def test_status_shows_pass_times_and_saturation(tmp_path, monkeypatch, capsys):
    (tmp_path / "Downloads").mkdir()
    now = datetime.now()
    for i, ms in enumerate((200, 400, 3_000)):
        at = (now - timedelta(minutes=10 - i)).isoformat(timespec="seconds")
        Ledger().record(
            RunRecord(f"r{i}", "watch", "/d", at, at, 1, 0, duration_ms=ms, waiting=i * 2)
        )

    code, out = _status(monkeypatch, capsys, tmp_path / "Downloads", installed=True)

    assert code == EXIT_OK
    assert "p50 0.4 s, p95 3 s, max 3 s over 3 runs" in out
    assert "p95 is 10% of the 30s interval" in out
    assert "0 -> 4 waiting to settle (rising), peak 4" in out
