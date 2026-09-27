"""Regression battery: the undo journal has to survive the crash it exists for.

Three defects, each measured before this battery was written:

1. A run interrupted mid-append leaves a partial last line, and ``cubby undo``
   died on it with a raw JSONDecodeError. That is precisely the situation undo
   is for.
2. Recording a run into an unwritable directory returned normally and wrote
   nothing, so files moved with no way back and nobody was told.
3. Undo moved files with Path.rename while the sort moved them with
   shutil.move, so any move that crossed a filesystem could not be undone.

A mechanism that is supposed to save you has to be tested in the conditions
where it is supposed to save you.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cubby.adapters.journal import Journal
from cubby.app.undo import undo_last_run


def _journal(tmp_path: Path) -> Journal:
    return Journal(tmp_path / "state" / "journal.jsonl")


def _recorded_move(tmp_path: Path, name: str = "a.txt") -> tuple[Path, Path]:
    source = tmp_path / name
    destination = tmp_path / "Documents" / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("content", encoding="utf-8")
    return source, destination


# --- 1. a journal truncated by a crash --------------------------------------


def test_a_truncated_last_line_does_not_crash_undo(tmp_path):
    journal = _journal(tmp_path)
    journal.record_run([_recorded_move(tmp_path)])
    with journal.path.open("a", encoding="utf-8") as handle:
        handle.write('{"ts": "2026-08-28T10:00:00", "mov')  # the process died here

    # The complete run before it is still recoverable.
    last = journal.last_run()
    assert last is not None
    assert last[0][1].name == "a.txt"


def test_undo_recovers_the_last_complete_run(tmp_path):
    journal = _journal(tmp_path)
    source, destination = _recorded_move(tmp_path)
    journal.record_run([(source, destination)])
    with journal.path.open("a", encoding="utf-8") as handle:
        handle.write("{not json at all")

    assert undo_last_run(journal) == 1
    assert source.is_file()
    assert not destination.exists()


def test_a_journal_of_nothing_but_damage_reports_nothing_to_undo(tmp_path):
    journal = _journal(tmp_path)
    journal.path.parent.mkdir(parents=True, exist_ok=True)
    journal.path.write_text("{broken\n{also broken\n", encoding="utf-8")

    assert journal.last_run() is None
    assert undo_last_run(journal) == 0


# --- 2. a journal that could not be written ---------------------------------


def test_a_journal_that_cannot_be_written_is_reported(tmp_path):
    unwritable = tmp_path / "state"
    unwritable.mkdir()
    unwritable.chmod(0o500)
    journal = Journal(unwritable / "journal.jsonl")
    warnings: list[str] = []

    try:
        journal.record_run([_recorded_move(tmp_path)], warn=warnings.append)
    finally:
        unwritable.chmod(0o700)

    assert warnings, "moving files with no way back must not happen silently"
    assert "undo" in warnings[0].lower()


def test_a_failed_recording_still_does_not_abort_the_run(tmp_path):
    # Losing the journal is bad; losing the sort as well is worse. The files
    # have already moved by the time this is called.
    unwritable = tmp_path / "state"
    unwritable.mkdir()
    unwritable.chmod(0o500)
    journal = Journal(unwritable / "journal.jsonl")
    try:
        journal.record_run([_recorded_move(tmp_path)])  # must not raise
    finally:
        unwritable.chmod(0o700)


# --- 3. undo across a filesystem boundary -----------------------------------


def test_undo_survives_a_move_across_filesystems(tmp_path, monkeypatch):
    # os.rename raises EXDEV across devices; the forward move used shutil.move
    # and could therefore produce a move that undo was unable to reverse.
    journal = _journal(tmp_path)
    source, destination = _recorded_move(tmp_path)
    journal.record_run([(source, destination)])

    real_rename = Path.rename

    def cross_device_rename(self, target):
        raise OSError(18, "Invalid cross-device link")

    monkeypatch.setattr(Path, "rename", cross_device_rename)
    restored = undo_last_run(journal)
    monkeypatch.setattr(Path, "rename", real_rename)

    assert restored == 1
    assert source.is_file()
    assert source.read_text(encoding="utf-8") == "content"


# --- partial failure has defined semantics ---------------------------------


def test_a_run_that_fails_halfway_keeps_what_it_could_not_restore(tmp_path):
    journal = _journal(tmp_path)
    first = _recorded_move(tmp_path, "first.txt")
    second = _recorded_move(tmp_path, "second.txt")
    journal.record_run([first, second])

    # The second file is gone from under us, so it cannot be restored.
    second[1].unlink()

    restored = undo_last_run(journal)

    assert restored == 1
    assert first[0].is_file()
    # The run is consumed either way: a second undo must not restore twice.
    assert undo_last_run(journal) == 0


def test_dropping_the_last_run_never_truncates_the_rest(tmp_path):
    journal = _journal(tmp_path)
    journal.record_run([_recorded_move(tmp_path, "one.txt")])
    journal.record_run([_recorded_move(tmp_path, "two.txt")])

    journal.drop_last_run()

    remaining = [
        json.loads(line)
        for line in journal.path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(remaining) == 1
    assert remaining[0]["moves"][0]["to"].endswith("one.txt")


def test_the_journal_survives_a_crash_while_dropping_a_run(tmp_path, monkeypatch):
    journal = _journal(tmp_path)
    journal.record_run([_recorded_move(tmp_path, "one.txt")])
    journal.record_run([_recorded_move(tmp_path, "two.txt")])

    def failing_replace(self, target):
        raise OSError("disk full")

    monkeypatch.setattr("pathlib.Path.replace", failing_replace)
    with pytest.raises(OSError, match="disk full"):
        journal.drop_last_run()
    monkeypatch.undo()

    # Both runs are still there: a failed drop is a no-op, not a lost journal.
    assert len(journal.path.read_text(encoding="utf-8").strip().splitlines()) == 2
