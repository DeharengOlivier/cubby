"""Tests for the log file's shape.

Every line looked the same, so the one line that mattered ("your files moved and
the undo journal could not be written") was indistinguishable from the hundreds
saying a file had been filed. A log is read after something has gone wrong, and
that is exactly when it has to be greppable.
"""

from __future__ import annotations

from cubby.adapters.logging import file_logger


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


def test_every_line_still_carries_its_timestamp(tmp_path):
    log = file_logger(tmp_path / "cubby.log")
    log("a line")
    assert (tmp_path / "cubby.log").read_text("utf-8")[:4].isdigit()


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
