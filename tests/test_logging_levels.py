"""Tests for the log file's shape.

Every line looked the same, so the one line that mattered ("your files moved and
the undo journal could not be written") was indistinguishable from the hundreds
saying a file had been filed. A log is read after something has gone wrong, and
that is exactly when it has to be greppable.
"""

from __future__ import annotations

import json

from cubby.adapters import logging as logging_module
from cubby.adapters.logging import file_logger, human_line, read_tail


def test_an_ordinary_line_is_marked_as_information(tmp_path):
    log = file_logger(tmp_path / "cubby.log")
    log("[Invoices] (name) Invoice-1.pdf")
    assert "INFO" in (tmp_path / "cubby.log").read_text("utf-8")


def test_a_warning_is_marked_as_one(tmp_path):
    log = file_logger(tmp_path / "cubby.log")
    log("could not write the undo journal", level="WARNING")
    line = (tmp_path / "cubby.log").read_text("utf-8")
    assert "WARNING" in line
    assert "undo journal" in line


def test_warnings_can_be_found_without_reading_the_whole_file(tmp_path):
    log = file_logger(tmp_path / "cubby.log")
    for index in range(50):
        log(f"[Documents] (type) file{index}.pdf")
    log("could not write the undo journal", level="WARNING")

    lines = (tmp_path / "cubby.log").read_text("utf-8").splitlines()
    warnings = [line for line in lines if "WARNING" in line]
    assert len(warnings) == 1


def test_every_line_is_json_with_a_timestamp_a_level_and_a_message(tmp_path):
    log = file_logger(tmp_path / "cubby.log")
    log("a line", level="ERROR")
    record = json.loads((tmp_path / "cubby.log").read_text("utf-8"))
    assert record["ts"][:4].isdigit()
    assert record["level"] == "ERROR"
    assert record["msg"] == "a line"


def test_the_log_is_rotated_past_its_size_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(logging_module, "MAX_BYTES", 200)
    log = file_logger(tmp_path / "cubby.log")
    for index in range(20):
        log(f"line {index}")

    assert (tmp_path / "cubby.log.1").exists()
    assert (tmp_path / "cubby.log").stat().st_size <= 400


def test_the_log_is_readable_by_its_owner_only(tmp_path):
    file_logger(tmp_path / "cubby.log")("a line")
    assert (tmp_path / "cubby.log").stat().st_mode & 0o777 == 0o600


def test_the_tail_reads_json_and_tolerates_plain_text(tmp_path):
    path = tmp_path / "cubby.log"
    file_logger(path)("filed a.pdf")
    with path.open("a", encoding="utf-8") as handle:
        handle.write("Traceback (most recent call last):\n")

    tail = read_tail(path, limit=5)

    assert tail[0]["msg"] == "filed a.pdf"
    assert tail[1] == {"msg": "Traceback (most recent call last):"}
    assert "filed a.pdf" in human_line(tail[0])


def test_echo_prints_a_human_line(tmp_path, capsys):
    file_logger(tmp_path / "cubby.log", echo=True)("filed a.pdf", level="WARNING")
    out = capsys.readouterr().out
    assert "WARNING" in out
    assert "filed a.pdf" in out


def test_a_logger_that_cannot_write_never_breaks_a_sort(tmp_path):
    unwritable = tmp_path / "locked"
    unwritable.mkdir()
    unwritable.chmod(0o500)
    log = file_logger(unwritable / "cubby.log")
    try:
        log("this must not raise")
        log("nor this", level="WARNING")
    finally:
        unwritable.chmod(0o700)


def test_a_log_that_cannot_be_written_says_so_once(tmp_path, capsys):
    unwritable = tmp_path / "locked"
    unwritable.mkdir()
    unwritable.chmod(0o500)
    log = file_logger(unwritable / "cubby.log")
    try:
        log("one")
        log("two")
    finally:
        unwritable.chmod(0o700)

    err = capsys.readouterr().err
    assert err.count("cannot write the log") == 1
