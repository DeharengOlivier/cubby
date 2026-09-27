"""Regression battery: the undo journal has to survive the crash it exists for.

Defects, each measured before the test that pins it was written:

1. A run interrupted mid-append left a partial last line, and ``cubby undo``
   died on it with a raw JSONDecodeError. That is precisely the situation undo
   is for.
2. Recording a run into an unwritable directory returned normally and wrote
   nothing, so files moved with no way back and nobody was told.
3. Undo moved files with Path.rename while the sort moved them with
   shutil.move, so any move that crossed a filesystem could not be undone.
4. (audit 1) A run was written to the journal only once it had finished, so a
   run that failed part way left the files it had moved with no way back.

A mechanism that is supposed to save you has to be tested in the conditions
where it is supposed to save you.
"""

from __future__ import annotations

import errno
import json
from pathlib import Path

import pytest

from cubby.adapters import journal as journal_module
from cubby.adapters.journal import Entry, Journal
from cubby.app.sorter import Sorter
from cubby.app.undo import undo_last_run, undo_run
from cubby.domain.category import Category, Config, Settings


def _journal(tmp_path: Path) -> Journal:
    return Journal(tmp_path / "state" / "journal.jsonl")


def _recorded_move(journal: Journal, tmp_path: Path, name: str, run: str = "r1", seq: int = 0):
    source = tmp_path / name
    destination = tmp_path / "Documents" / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("content", encoding="utf-8")
    journal.record(Entry(run, seq, "move", source, destination))
    return source, destination


# --- 1. a journal truncated by a crash --------------------------------------


def test_a_truncated_last_line_does_not_crash_undo(tmp_path):
    journal = _journal(tmp_path)
    source, _ = _recorded_move(journal, tmp_path, "a.txt")
    with journal.path.open("a", encoding="utf-8") as handle:
        handle.write('{"v": 2, "run": "r2", "seq": 0, "op": "mo')  # the process died here

    # The complete move before it is still recoverable.
    assert undo_last_run(journal) == 1
    assert source.is_file()


def test_a_journal_of_nothing_but_damage_reports_nothing_to_undo(tmp_path):
    journal = _journal(tmp_path)
    journal.path.parent.mkdir(parents=True, exist_ok=True)
    journal.path.write_text('{broken\n{also broken\n[1, 2]\n"text"\n', encoding="utf-8")

    assert journal.last_pending_run() is None
    assert undo_last_run(journal) == 0


def test_lines_from_an_unknown_future_version_are_ignored(tmp_path):
    journal = _journal(tmp_path)
    journal.path.parent.mkdir(parents=True, exist_ok=True)
    journal.path.write_text(
        '{"v": 99, "run": "x", "seq": 0, "op": "teleport", "from": "/a", "to": "/b"}\n',
        encoding="utf-8",
    )
    assert journal.runs() == []


# --- 2. a journal that could not be written ---------------------------------


def _config(source: Path) -> Config:
    return Config(
        settings=Settings(source=source, delay=0, content_scan=False),
        categories=(Category(name="Documents", extensions=frozenset({"txt"})),),
    )


def test_a_journal_that_cannot_be_written_is_reported_and_the_sort_goes_on(tmp_path):
    source = tmp_path / "Downloads"
    source.mkdir()
    (source / "a.txt").write_text("a")
    (source / "b.txt").write_text("b")
    unwritable = tmp_path / "state"
    unwritable.mkdir()
    unwritable.chmod(0o500)
    warnings: list[str] = []

    try:
        outcomes = Sorter(
            _config(source), journal=Journal(unwritable / "journal.jsonl"), warn=warnings.append
        ).sort_once(apply=True)
    finally:
        unwritable.chmod(0o700)

    # Losing the journal is bad; losing the sort as well would be worse.
    assert all(o.moved_to is not None for o in outcomes)
    assert len(warnings) == 2, "moving files with no way back must not happen silently"
    assert all("undo" in w.lower() for w in warnings)


# --- 3. undo across a filesystem boundary -----------------------------------


def test_undo_survives_a_move_across_filesystems(tmp_path, monkeypatch):
    # Hard links and rename both fail with EXDEV across devices; the undo has
    # to fall back to copying, as the forward move did.
    journal = _journal(tmp_path)
    source, _ = _recorded_move(journal, tmp_path, "a.txt")

    def cross_device(*args, **kwargs):
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr("os.link", cross_device)
    monkeypatch.setattr("os.rename", cross_device)
    restored = undo_last_run(journal)
    monkeypatch.undo()

    assert restored == 1
    assert source.read_text(encoding="utf-8") == "content"


# --- 4. a run is journaled as it goes ---------------------------------------


def test_each_move_is_on_disk_before_the_next_one_starts(tmp_path, monkeypatch):
    source = tmp_path / "Downloads"
    source.mkdir()
    for name in ("a.txt", "b.txt", "c.txt"):
        (source / name).write_text(name)
    journal = Journal(tmp_path / "journal.jsonl")
    seen_before_each_move: list[int] = []
    from cubby.app import sorter as sorter_module

    real_move = sorter_module.move_into

    def spying_move(path, *args, **kwargs):
        seen_before_each_move.append(sum(len(r.entries) for r in journal.runs()))
        return real_move(path, *args, **kwargs)

    monkeypatch.setattr(sorter_module, "move_into", spying_move)
    Sorter(_config(source), journal=journal).sort_once(apply=True)

    assert seen_before_each_move == [0, 1, 2]


# --- partial failure has defined semantics ---------------------------------


def test_a_file_gone_since_the_run_is_settled_and_the_rest_restored(tmp_path):
    journal = _journal(tmp_path)
    first = _recorded_move(journal, tmp_path, "first.txt", seq=0)
    second = _recorded_move(journal, tmp_path, "second.txt", seq=1)
    second[1].unlink()  # removed by the user since the run

    result = undo_run(journal)

    assert result.restored == 1
    assert result.gone == 1
    assert first[0].is_file()
    # Everything is settled: a second undo has nothing left to do.
    assert undo_last_run(journal) == 0


def test_a_file_that_cannot_be_restored_stays_pending_for_a_retry(tmp_path, monkeypatch):
    journal = _journal(tmp_path)
    source, _ = _recorded_move(journal, tmp_path, "a.txt")
    from cubby.app import undo as undo_module

    def refuse(*args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(undo_module, "move_no_clobber", refuse)
    result = undo_run(journal)
    assert result.restored == 0
    assert result.failed == ["a.txt"]
    monkeypatch.undo()

    assert undo_last_run(journal) == 1
    assert source.is_file()


def test_undo_never_rewrites_the_journal(tmp_path):
    journal = _journal(tmp_path)
    _recorded_move(journal, tmp_path, "one.txt", run="r1")
    _recorded_move(journal, tmp_path, "two.txt", run="r2")
    before = journal.path.read_text(encoding="utf-8")

    undo_last_run(journal)

    after = journal.path.read_text(encoding="utf-8")
    assert after.startswith(before), "history is appended to, never edited"
    settled = json.loads(after.splitlines()[-1])
    assert settled == {"v": 2, "run": "r2", "seq": 0, "op": "restored"}


def test_undo_goes_back_one_run_at_a_time(tmp_path):
    journal = _journal(tmp_path)
    one, _ = _recorded_move(journal, tmp_path, "one.txt", run="r1")
    two, _ = _recorded_move(journal, tmp_path, "two.txt", run="r2")

    assert undo_last_run(journal) == 1
    assert two.is_file()
    assert not one.is_file()
    assert undo_last_run(journal) == 1
    assert one.is_file()


def test_a_named_run_can_be_undone_out_of_order(tmp_path):
    journal = _journal(tmp_path)
    one, _ = _recorded_move(journal, tmp_path, "one.txt", run="r1")
    two, _ = _recorded_move(journal, tmp_path, "two.txt", run="r2")

    assert undo_run(journal, "r1").restored == 1

    assert one.is_file()
    assert not two.is_file()
    with pytest.raises(KeyError):
        undo_run(journal, "no-such-run")


# --- version 1 journals, written by cubby 0.1 --------------------------------


def test_a_version_1_run_is_still_undone(tmp_path):
    journal = _journal(tmp_path)
    source = tmp_path / "old.txt"
    destination = tmp_path / "Documents" / "old.txt"
    destination.parent.mkdir(parents=True)
    destination.write_text("from 0.1")
    journal.path.parent.mkdir(parents=True, exist_ok=True)
    journal.path.write_text(
        json.dumps(
            {"ts": "2026-08-01T10:00:00", "moves": [{"from": str(source), "to": str(destination)}]}
        )
        + "\n",
        encoding="utf-8",
    )

    assert undo_last_run(journal) == 1
    assert source.read_text() == "from 0.1"
    assert undo_last_run(journal) == 0


# --- the journal is bounded, and bounding it cannot lose it ------------------


def test_compaction_keeps_the_most_recent_runs_whole(tmp_path, monkeypatch):
    journal = _journal(tmp_path)
    monkeypatch.setattr(journal_module, "MAX_BYTES", 1)
    monkeypatch.setattr(journal_module, "KEEP_RUNS", 2)
    for index in range(5):
        for seq in range(3):
            journal.record(
                Entry(f"r{index}", seq, "move", Path(f"/a/{index}-{seq}"), Path(f"/b/{index}"))
            )

    journal.compact()

    runs = journal.runs()
    assert [r.run_id for r in runs] == ["r3", "r4"]
    assert all(len(r.entries) == 3 for r in runs)


def test_a_crash_while_compacting_leaves_the_journal_intact(tmp_path, monkeypatch):
    journal = _journal(tmp_path)
    _recorded_move(journal, tmp_path, "one.txt", run="r1")
    _recorded_move(journal, tmp_path, "two.txt", run="r2")
    before = journal.path.read_text(encoding="utf-8")
    monkeypatch.setattr(journal_module, "MAX_BYTES", 1)

    def failing_replace(self, target):
        raise OSError("disk full")

    monkeypatch.setattr("pathlib.Path.replace", failing_replace)
    with pytest.raises(OSError, match="disk full"):
        journal.compact()
    monkeypatch.undo()

    assert journal.path.read_text(encoding="utf-8") == before
    assert not journal.path.with_name(journal.path.name + ".staging").exists()


def test_the_journal_is_readable_by_its_owner_only(tmp_path):
    journal = _journal(tmp_path)
    _recorded_move(journal, tmp_path, "a.txt")
    assert journal.path.stat().st_mode & 0o777 == 0o600
