"""Edges of the code that moves, deletes and restores files.

Written from the survivors of the first mutation run on the journal, the
filesystem adapter and undo (see docs/audits/2026-09-28-mutation.md): each test
pins a behavior a plausible bug could break without any other test noticing.
The most important are the dedupe ones: a wrong answer there deletes a file
that was not a duplicate.
"""

from __future__ import annotations

import errno
import json
import os
import re
from pathlib import Path

import pytest

from cubby.adapters import journal as journal_module
from cubby.adapters import state
from cubby.adapters.filesystem import (
    build_ref,
    candidate_skip_reason,
    files_identical,
    move_into,
    move_no_clobber,
    not_yet_reason,
)
from cubby.adapters.journal import Entry, Journal, default_journal_path, new_run_id
from cubby.app.undo import undo_last_run, undo_run
from cubby.domain.category import Settings
from cubby.domain.naming import safe_component

# --- dedupe never deletes a file that is not a byte-identical copy -----------


def test_files_of_different_sizes_are_not_identical(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_bytes(b"short")
    b.write_bytes(b"longer content")
    assert files_identical(a, b) is False


def test_files_of_the_same_size_but_different_bytes_are_not_identical(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_bytes(b"x" * 70_000 + b"A")  # past one read chunk
    b.write_bytes(b"x" * 70_000 + b"B")
    assert files_identical(a, b) is False


def test_identical_bytes_are_identical(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_bytes(b"same" * 20_000)
    b.write_bytes(b"same" * 20_000)
    assert files_identical(a, b) is True


def test_a_file_and_a_folder_are_not_identical(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_bytes(b"")
    b.mkdir()
    assert files_identical(a, b) is False
    assert files_identical(b, a) is False


def test_a_file_that_cannot_be_examined_is_not_identical(tmp_path, monkeypatch):
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_bytes(b"same")
    b.write_bytes(b"same")
    real_stat = Path.stat

    def stat(self, *args, **kwargs):
        if self == b:  # b vanished or became unreadable after is_file()
            raise PermissionError(13, "Permission denied", str(self))
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "is_file", lambda self: True)
    monkeypatch.setattr(Path, "stat", stat)
    assert files_identical(a, b) is False


@pytest.mark.parametrize("existing", [b"other", b"diff"])  # other size, same size
def test_dedupe_keeps_a_same_named_file_with_other_bytes(tmp_path, existing):
    root = tmp_path
    (root / "Docs").mkdir()
    (root / "Docs" / "r.txt").write_bytes(existing)
    incoming = root / "r.txt"
    incoming.write_bytes(b"mine")

    moved = move_into(incoming, root / "Docs", root=root, dedupe=True)

    assert moved.op == "move"
    assert moved.destination.name == "r (1).txt"
    assert moved.destination.read_bytes() == b"mine"
    assert (root / "Docs" / "r.txt").read_bytes() == existing


def test_move_into_does_not_dedupe_unless_asked(tmp_path):
    (tmp_path / "Docs").mkdir()
    (tmp_path / "Docs" / "r.txt").write_bytes(b"same")
    incoming = tmp_path / "r.txt"
    incoming.write_bytes(b"same")

    moved = move_into(incoming, tmp_path / "Docs", root=tmp_path)

    assert (moved.op, moved.destination.name) == ("move", "r (1).txt")


# --- moving -------------------------------------------------------------------


def test_a_symlink_is_moved_as_a_symlink(tmp_path):
    target = tmp_path / "target.txt"
    target.write_text("t")
    link = tmp_path / "link.txt"
    link.symlink_to(target)
    destination = tmp_path / "moved.txt"

    move_no_clobber(link, destination)

    assert destination.is_symlink()
    assert destination.readlink() == target
    assert not os.path.lexists(link)


def test_a_taken_destination_is_reported_with_its_name(tmp_path):
    source = tmp_path / "d"
    source.mkdir()
    destination = tmp_path / "taken"
    destination.mkdir()

    with pytest.raises(FileExistsError) as info:
        move_no_clobber(source, destination)

    assert info.value.errno == errno.EEXIST
    assert info.value.strerror == "destination exists"
    assert info.value.filename == str(destination)
    assert source.is_dir()


def test_endless_name_races_end_in_a_clear_error(tmp_path, monkeypatch):
    incoming = tmp_path / "a.txt"
    incoming.write_text("x")

    def always_taken(source, destination):
        raise FileExistsError(errno.EEXIST, "destination exists", str(destination))

    monkeypatch.setattr("cubby.adapters.filesystem.move_no_clobber", always_taken)

    with pytest.raises(FileExistsError) as info:
        move_into(incoming, tmp_path / "Docs", root=tmp_path)

    assert info.value.errno == errno.EEXIST
    assert info.value.strerror == "every candidate name was taken while moving"
    assert info.value.filename == str(tmp_path / "Docs")
    assert incoming.exists()


def test_a_rename_to_a_path_is_refused_naming_the_setting(tmp_path):
    incoming = tmp_path / "a.txt"
    incoming.write_text("x")
    with pytest.raises(ValueError, match=r"^rename_to must be a single folder name"):
        move_into(incoming, tmp_path / "Docs", root=tmp_path, rename_to="../escape.txt")
    assert incoming.exists()


# --- what a run looks at --------------------------------------------------------


def test_a_folder_offers_no_text(tmp_path):
    ref = build_ref(tmp_path)
    assert ref.is_file is False
    assert ref.read_text() == ""


def test_text_is_read_up_to_the_default_window(tmp_path):
    path = tmp_path / "long.txt"
    path.write_text("a" * 10_000)
    assert len(build_ref(path).read_text()) == 4000


def test_skip_reasons_are_exact(tmp_path):
    settings = Settings(source=tmp_path)
    assert candidate_skip_reason(tmp_path / ".x", settings, frozenset()) == "hidden file"
    assert (
        candidate_skip_reason(tmp_path / "Docs", settings, frozenset({"Docs"}))
        == "a folder cubby files into"
    )


def test_a_file_exactly_as_old_as_the_delay_is_settled(tmp_path):
    path = tmp_path / "a.txt"
    path.write_text("x")
    mtime = path.stat().st_mtime
    assert not_yet_reason(path, Settings(source=tmp_path, delay=0), now=mtime) is None
    assert not_yet_reason(path, Settings(source=tmp_path, delay=10), now=mtime + 10) is None


def test_an_unreadable_age_names_the_reason(tmp_path):
    reason = not_yet_reason(tmp_path / "gone.txt", Settings(source=tmp_path))
    assert reason == "cannot be read (No such file or directory)"


def test_configured_name_errors_say_what_is_wrong():
    with pytest.raises(ValueError, match=r"^category must be text, got int"):
        safe_component(3, field="category")  # type: ignore[arg-type]
    with pytest.raises(
        ValueError, match=re.escape("got '..'. Empty names, '.' and '..' are not folders")
    ):
        safe_component("..", field="category")
    with pytest.raises(ValueError, match=r"outside the folder it watches\.$"):
        safe_component("a/b", field="category")


# --- journal ------------------------------------------------------------------


def test_a_bad_sequence_number_says_what_it_got():
    with pytest.raises(ValueError, match=r"^not a sequence number: -1$"):
        journal_module._seq(-1)


def test_the_journal_lives_in_the_state_folder():
    assert default_journal_path() == state.state_dir() / "journal.jsonl"


def test_run_ids_sort_by_time_and_carry_sixteen_hex_digits():
    assert re.fullmatch(r"\d{8}T\d{6}-[0-9a-f]{16}", new_run_id())


def test_a_version_1_run_reads_as_numbered_plain_moves(tmp_path):
    journal = Journal(tmp_path / "j.jsonl")
    line = json.dumps(
        {
            "ts": "2026-01-01T00:00:00",
            "moves": [
                {"from": "/d/a", "to": "/d/X/a"},
                {"from": "/d/b", "to": "/d/X/b"},
            ],
        }
    )
    journal.path.write_text(line + "\n", encoding="utf-8")

    (run,) = journal.runs()

    assert re.fullmatch(r"v1-[0-9a-f]{12}", run.run_id)
    assert [(e.seq, e.op) for e in run.entries] == [(0, "move"), (1, "move")]


def test_a_versioned_line_carrying_moves_is_not_read_as_version_1(tmp_path):
    journal = Journal(tmp_path / "j.jsonl")
    journal.path.write_text(
        json.dumps({"v": 3, "moves": [{"from": "/d/a", "to": "/d/X/a"}]}) + "\n",
        encoding="utf-8",
    )
    assert journal.runs() == []


def test_settlements_are_read_back_with_their_verdict(tmp_path):
    journal = Journal(tmp_path / "j.jsonl")
    first = Entry("r", 0, "move", Path("/d/a"), Path("/d/X/a"))
    second = Entry("r", 1, "move", Path("/d/b"), Path("/d/X/b"))
    journal.record(first)
    journal.record(second)
    journal.settle(first, "restored")
    journal.settle(second, "gone")

    (run,) = journal.runs()

    assert run.settled == {0: "restored", 1: "gone"}


def test_a_journal_exactly_at_the_limit_is_not_compacted(tmp_path, monkeypatch):
    journal = Journal(tmp_path / "j.jsonl")
    old = Entry("old", 0, "move", Path("/d/a"), Path("/d/X/a"))
    journal.record(old)
    journal.settle(old, "restored")  # nothing pending: compaction would drop it
    journal.record(Entry("new", 0, "move", Path("/d/b"), Path("/d/X/b")))
    monkeypatch.setattr(journal_module, "KEEP_RUNS", 1)
    monkeypatch.setattr(journal_module, "MAX_BYTES", journal.path.stat().st_size)
    before = journal.path.read_bytes()

    journal.compact()

    assert journal.path.read_bytes() == before


def test_compaction_drops_lines_that_belong_to_no_run(tmp_path, monkeypatch):
    journal = Journal(tmp_path / "j.jsonl")
    journal.record(Entry("r", 0, "move", Path("/d/a"), Path("/d/X/a")))
    with journal.path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"v": 2, "run": 7, "seq": 0, "op": "move"}) + "\n")
    monkeypatch.setattr(journal_module, "MAX_BYTES", 1)

    journal.compact()

    assert '"run": 7' not in journal.path.read_text(encoding="utf-8")
    assert [r.run_id for r in journal.runs()] == ["r"]


# --- undo -----------------------------------------------------------------------


def _moved(tmp_path: Path, journal: Journal, name: str, seq: int = 0, run: str = "r") -> Entry:
    source = tmp_path / "Downloads" / name
    destination = tmp_path / "Downloads" / "Docs" / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(name)
    entry = Entry(run, seq, "move", source, destination)
    journal.record(entry)
    return entry


def test_undo_reports_the_run_it_undid(tmp_path):
    journal = Journal(tmp_path / "j.jsonl")
    _moved(tmp_path, journal, "a.txt", run="the-run")

    result = undo_run(journal)

    assert (result.run_id, result.restored) == ("the-run", 1)


def test_undo_of_an_unknown_run_names_it(tmp_path):
    with pytest.raises(KeyError, match="nope"):
        undo_run(Journal(tmp_path / "j.jsonl"), "nope")


def test_nothing_to_undo_is_said(tmp_path):
    lines: list[str] = []
    result = undo_run(Journal(tmp_path / "j.jsonl"), log=lines.append)
    assert (result.run_id, result.restored) == (None, 0)
    assert lines == ["nothing to undo"]


def test_vanished_files_are_counted_and_settled_as_gone(tmp_path):
    journal = Journal(tmp_path / "j.jsonl")
    for seq, name in enumerate(("a.txt", "b.txt")):
        _moved(tmp_path, journal, name, seq).destination.unlink()
    lines: list[str] = []

    result = undo_run(journal, log=lines.append)

    assert (result.gone, result.restored) == (2, 0)
    assert journal.runs()[0].settled == {0: "gone", 1: "gone"}
    assert all(line.startswith("skip (no longer at ") for line in lines)


def test_a_failed_entry_does_not_stop_the_older_ones(tmp_path, monkeypatch):
    journal = Journal(tmp_path / "j.jsonl")
    older = _moved(tmp_path, journal, "a.txt", 0)
    _moved(tmp_path, journal, "b.txt", 1)
    real = move_no_clobber

    def refuse_b(source, destination):
        if source.name == "b.txt":
            raise PermissionError(13, "Permission denied")
        real(source, destination)

    monkeypatch.setattr("cubby.app.undo.move_no_clobber", refuse_b)
    lines: list[str] = []

    result = undo_run(journal, log=lines.append)

    assert result.restored == 1
    assert older.source.exists()
    assert len(result.failed) == 1
    assert any(line.startswith("pending (cannot restore b.txt)") for line in lines)
    assert "restored a.txt" in lines


def test_undo_recreates_a_source_folder_that_was_removed(tmp_path):
    journal = Journal(tmp_path / "j.jsonl")
    source = tmp_path / "drive" / "user" / "Downloads" / "a.txt"
    destination = tmp_path / "kept" / "a.txt"
    destination.parent.mkdir()
    destination.write_text("a")
    journal.record(Entry("r", 0, "move", source, destination))

    assert undo_run(journal).restored == 1

    assert source.read_text() == "a"


def test_undo_last_run_passes_its_log_on(tmp_path):
    journal = Journal(tmp_path / "j.jsonl")
    _moved(tmp_path, journal, "a.txt")
    lines: list[str] = []
    assert undo_last_run(journal, log=lines.append) == 1
    assert lines == ["restored a.txt"]


def test_a_move_whose_cleanup_also_fails_raises_the_first_error_and_names_the_link(
    tmp_path, monkeypatch
):
    source = tmp_path / "a.txt"
    source.write_text("x")
    destination = tmp_path / "Documents" / "a.txt"
    destination.parent.mkdir()
    real_unlink = Path.unlink

    def refuse(self, missing_ok=False):
        if self in (source, destination):
            raise PermissionError(13, "Permission denied", str(self))
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", refuse)

    with pytest.raises(PermissionError) as info:
        move_no_clobber(source, destination)

    assert info.value.filename == str(source)
    assert any(str(destination) in note for note in info.value.__notes__)


def test_compaction_that_drops_nothing_does_not_rewrite_the_file(tmp_path, monkeypatch):
    journal = Journal(tmp_path / "j.jsonl")
    journal.record(Entry("r", 0, "move", Path("/d/a"), Path("/d/X/a")))  # pending: stays
    monkeypatch.setattr(journal_module, "MAX_BYTES", 1)
    inode = journal.path.stat().st_ino

    journal.compact()

    assert journal.path.stat().st_ino == inode


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads through any permission")
def test_two_unreadable_files_of_the_same_size_are_not_identical(tmp_path):
    # Found by review: both digests were None, and None == None.
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("AAAA", encoding="utf-8")
    b.write_text("BBBB", encoding="utf-8")
    a.chmod(0o000)
    b.chmod(0o000)
    try:
        assert files_identical(a, b) is False
    finally:
        a.chmod(0o644)
        b.chmod(0o644)


def test_a_filed_link_to_the_source_is_not_its_duplicate(tmp_path):
    # Found by review: dedupe deleted the only copy and kept a broken link.
    source = tmp_path / "r.txt"
    source.write_text("only copy", encoding="utf-8")
    (tmp_path / "Documents").mkdir()
    (tmp_path / "Documents" / "r.txt").symlink_to(source)

    moved = move_into(source, tmp_path / "Documents", root=tmp_path, dedupe=True)

    assert moved.op == "move"
    assert moved.destination.read_text("utf-8") == "only copy"


def test_a_link_to_a_file_is_not_its_copy_even_at_the_same_size(tmp_path):
    # The link's own size is the length of its target: make them equal.
    target = tmp_path / "t.txt"
    target.write_text("x" * len(str(target)), encoding="utf-8")
    link = tmp_path / "link.txt"
    link.symlink_to(target)

    assert files_identical(target, link) is False
    assert files_identical(link, target) is False
