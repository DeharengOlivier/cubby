"""``cubby log``: read what the agent did without knowing where the log is or jq."""

from __future__ import annotations

import json

import pytest

from cubby.adapters import logging as log_module
from cubby.adapters import state
from cubby.adapters.logging import file_logger, run_context
from cubby.cli import EXIT_OK, main


def _write_log() -> None:
    log = file_logger()
    log("agent started")
    with run_context("r1"):
        log("[Documents] a.txt")
        log("could not sort b.txt: Permission denied", level="WARNING")
    with run_context("r2"):
        log("[Documents] c.txt")


def test_no_log_yet_is_said_plainly(capsys):
    assert main(["log"]) == EXIT_OK
    assert "No log yet" in capsys.readouterr().out


def test_the_last_lines_are_shown_oldest_first(capsys):
    _write_log()

    assert main(["log", "-n", "2"]) == EXIT_OK

    out = capsys.readouterr().out.splitlines()
    assert len(out) == 2
    assert "b.txt" in out[0]
    assert "c.txt" in out[1]


def test_one_run_can_be_followed(capsys):
    _write_log()

    assert main(["log", "--run", "r1"]) == EXIT_OK

    out = capsys.readouterr().out
    assert "a.txt" in out
    assert "b.txt" in out
    assert "c.txt" not in out
    assert "agent started" not in out


def test_only_what_went_wrong(capsys):
    _write_log()

    assert main(["log", "--warnings"]) == EXIT_OK

    out = capsys.readouterr().out.splitlines()
    assert len(out) == 1
    assert "Permission denied" in out[0]


def test_json_gives_the_records_one_per_line(capsys):
    _write_log()

    assert main(["log", "--json", "--run", "r2"]) == EXIT_OK

    (line,) = capsys.readouterr().out.splitlines()
    record = json.loads(line)
    assert (record["run"], record["msg"]) == ("r2", "[Documents] c.txt")


def test_the_rotated_file_is_read_first(capsys, monkeypatch):
    monkeypatch.setattr(log_module, "MAX_BYTES", 1)
    log = file_logger()
    log("older")
    log("newer")  # the first line is rotated to cubby.log.1 before this one is written

    assert state.log_path().with_name("cubby.log.1").exists()
    assert main(["log"]) == EXIT_OK
    out = capsys.readouterr().out.splitlines()
    assert ["older" in out[0], "newer" in out[1]] == [True, True]


def test_a_line_that_is_not_json_is_still_shown(capsys):
    state.log_path().parent.mkdir(parents=True, exist_ok=True)
    state.log_path().write_text("plain text from an old cubby\n", encoding="utf-8")

    assert main(["log"]) == EXIT_OK
    assert "plain text from an old cubby" in capsys.readouterr().out


@pytest.mark.parametrize("value", ["0", "-3", "x"])
def test_the_line_count_must_be_positive(value):
    with pytest.raises(SystemExit) as info:
        main(["log", "-n", value])
    assert info.value.code == 2
