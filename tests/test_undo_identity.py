"""Undo puts back the file cubby moved, never another one found in its place.

Found by an exploratory session: after a run filed ``todo.txt`` into
``Documents/``, the user replaced it with a different file of the same name,
and ``cubby undo`` pulled that file (which cubby never moved) out of
``Documents/``. The journal now records the identity of each moved file (its
device and inode), and undo leaves a file alone when that identity changed.
The inode alone is not enough (ext4 reuses a freed one at once), so the
identity also holds the size and the modification time: a file edited since
the run is left in place too, and undo says so.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

import pytest

from cubby.adapters.filesystem import identity, same_file
from cubby.adapters.journal import Journal
from cubby.app.sorter import Sorter
from cubby.app.undo import undo_run
from cubby.cli import main
from tests.helpers import aged_file, config_for


def _sorted(tmp_path, *names: str, **settings) -> tuple[Journal, list[str]]:
    for name in names:
        aged_file(tmp_path, name, content=f"original {name}")
    journal = Journal(tmp_path.parent / f"{tmp_path.name}.jsonl")
    Sorter(config_for(tmp_path, **settings), journal=journal).sort_once(apply=True)
    return journal, []


def test_a_file_replaced_since_the_run_is_left_where_it_is(tmp_path):
    journal, logs = _sorted(tmp_path, "todo.txt")
    filed = tmp_path / "Documents" / "todo.txt"
    filed.unlink()
    filed.write_text("the user's own file", encoding="utf-8")

    result = undo_run(journal, log=logs.append)

    assert filed.read_text("utf-8") == "the user's own file"
    assert not (tmp_path / "todo.txt").exists()
    assert result.restored == 0
    assert result.replaced == 1
    assert any("changed or replaced since the run" in line and "todo.txt" in line for line in logs)
    assert journal.last_pending_run() is None  # settled: retrying cannot help


def test_a_file_edited_since_the_run_is_left_in_place_too(tmp_path):
    journal, logs = _sorted(tmp_path, "notes.txt")
    with (tmp_path / "Documents" / "notes.txt").open("a", encoding="utf-8") as handle:
        handle.write(" and an edit")

    result = undo_run(journal, log=logs.append)

    assert result.replaced == 1
    assert (tmp_path / "Documents" / "notes.txt").exists()
    assert any("move it back to" in line for line in logs)


def test_a_file_only_read_since_the_run_comes_back(tmp_path):
    journal, _ = _sorted(tmp_path, "notes.txt")
    (tmp_path / "Documents" / "notes.txt").read_text("utf-8")
    (tmp_path / "Documents" / "notes.txt").chmod(0o644)  # a metadata change only

    assert undo_run(journal).restored == 1


def test_the_journal_records_the_identity_of_each_move(tmp_path):
    journal, _ = _sorted(tmp_path, "a.txt")
    filed = (tmp_path / "Documents" / "a.txt").stat()

    (entry,) = journal.runs()[0].entries

    assert entry.ident == (filed.st_dev, filed.st_ino, filed.st_size, filed.st_mtime_ns)


def test_an_entry_without_identity_undoes_as_before(tmp_path):
    # Journals written before 0.3 have no identity: undo cannot tell, and
    # behaves as it always did.
    (tmp_path / "Documents").mkdir()
    (tmp_path / "Documents" / "old.txt").write_text("x", encoding="utf-8")
    journal = Journal(tmp_path.parent / "old.jsonl")
    journal.path.write_text(
        json.dumps(
            {"v": 2, "run": "r1", "seq": 0, "op": "move",
             "from": str(tmp_path / "old.txt"), "to": str(tmp_path / "Documents" / "old.txt")}
        ) + "\n",
        encoding="utf-8",
    )  # fmt: skip

    assert undo_run(journal).restored == 1
    assert (tmp_path / "old.txt").exists()


def test_a_damaged_identity_reads_as_unknown(tmp_path):
    journal = Journal(tmp_path / "j.jsonl")
    record = {"v": 2, "run": "r1", "seq": 0, "op": "move", "from": "/a", "to": "/b"}
    recorded = [
        [1, 2, 3, 4],
        [0, 2, 0, 0],
        [1, 2, 3, -5],
        [1, 2, -3, 4],
        [1, 2],
        "x",
        [1, 2, 3, "4"],
        [True, 2, 3, 4],
        [-1, 2, 3, 4],
        None,
    ]
    lines = [json.dumps({**record, "seq": i, "id": ident}) for i, ident in enumerate(recorded)]
    journal.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    idents = [entry.ident for entry in journal.runs()[0].entries]

    assert idents == [
        (1, 2, 3, 4),
        (0, 2, 0, 0),
        (1, 2, 3, -5),
        None,
        None,
        None,
        None,
        None,
        None,
        None,
    ]


def test_a_duplicate_is_not_recreated_from_a_replaced_copy(tmp_path):
    aged_file(tmp_path / "Documents", "report.txt", content="SAME")
    aged_file(tmp_path, "report.txt", content="SAME")
    journal = Journal(tmp_path.parent / "d.jsonl")
    Sorter(config_for(tmp_path, dedupe=True), journal=journal).sort_once(apply=True)
    kept = tmp_path / "Documents" / "report.txt"
    kept.unlink()
    kept.write_text("something else entirely", encoding="utf-8")

    result = undo_run(journal)

    assert result.replaced == 1
    assert not (tmp_path / "report.txt").exists()


# --- what the command says -------------------------------------------------------


def test_undo_says_what_it_could_not_put_back_and_exits_1(tmp_path, capsys):
    for name in ("a.txt", "b.txt", "c.txt"):
        aged_file(tmp_path, name)
    main(["run", "--source", str(tmp_path), "--delay", "0"])
    (tmp_path / "Documents" / "a.txt").unlink()  # deleted by the user
    (tmp_path / "Documents" / "b.txt").unlink()
    (tmp_path / "Documents" / "b.txt").write_text("other", encoding="utf-8")
    capsys.readouterr()

    code = main(["undo"])
    captured = capsys.readouterr()

    assert code == 1
    assert "Restored 1 file(s)." in captured.out
    assert "cubby: 1 no longer where the run put it" in captured.err
    assert "cubby: 1 changed or replaced since the run, left in place" in captured.err


def test_undo_says_when_a_file_comes_back_under_another_name(tmp_path, capsys):
    aged_file(tmp_path, "notes.txt")
    main(["run", "--source", str(tmp_path), "--delay", "0"])
    aged_file(tmp_path, "notes.txt", content="a new download with the same name")
    capsys.readouterr()

    main(["undo"])

    assert "restored notes.txt as notes (1).txt (notes.txt is taken)" in capsys.readouterr().out


# --- from the review of this change ---------------------------------------------


def test_a_sorted_folder_whose_content_changed_still_comes_back(tmp_path):
    # A folder's mtime moves whenever an entry inside changes (Finder writes
    # .DS_Store just by opening it): only the inode tells a folder apart.
    (tmp_path / "holiday").mkdir()
    (tmp_path / "holiday" / "a.jpg").write_bytes(b"x")
    past = 10_000
    os.utime(tmp_path / "holiday", (time.time() - past, time.time() - past))
    journal = Journal(tmp_path.parent / "f.jsonl")
    Sorter(config_for(tmp_path), journal=journal).sort_once(apply=True)
    moved = next((tmp_path / "_Unsorted").iterdir())
    (moved / ".DS_Store").write_bytes(b"finder")

    assert undo_run(journal).restored == 1
    assert (tmp_path / "holiday" / ".DS_Store").exists()


def test_a_changed_kept_copy_is_not_called_a_file_to_move_back(tmp_path):
    aged_file(tmp_path / "Documents", "report.txt", content="SAME")
    aged_file(tmp_path, "report.txt", content="SAME")
    journal = Journal(tmp_path.parent / "k.jsonl")
    Sorter(config_for(tmp_path, dedupe=True), journal=journal).sort_once(apply=True)
    with (tmp_path / "Documents" / "report.txt").open("a", encoding="utf-8") as handle:
        handle.write(" edited")
    logs: list[str] = []

    undo_run(journal, log=logs.append)

    (line,) = [line for line in logs if "report.txt" in line]
    assert "the duplicate is not recreated" in line
    assert "move it back" not in line


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads through any permission")
def test_an_unreadable_destination_stays_pending(tmp_path):
    # Found by review: lexists() says False on a permission error, and the
    # entry was settled as gone for good.
    journal, _ = _sorted(tmp_path, "a.txt")
    documents = tmp_path / "Documents"
    documents.chmod(0o000)
    try:
        result = undo_run(journal)
    finally:
        documents.chmod(0o755)

    assert (result.gone, len(result.failed)) == (0, 1)
    assert undo_run(journal).restored == 1


def test_an_empty_file_is_still_told_apart(tmp_path):
    aged_file(tmp_path, "empty.txt", content="")
    journal = Journal(tmp_path.parent / "e.jsonl")
    Sorter(config_for(tmp_path), journal=journal).sort_once(apply=True)
    filed = tmp_path / "Documents" / "empty.txt"
    filed.write_text("now it has content", encoding="utf-8")

    assert undo_run(journal).replaced == 1


def test_two_replaced_files_are_both_counted(tmp_path):
    journal, _ = _sorted(tmp_path, "a.txt", "b.txt")
    for name in ("a.txt", "b.txt"):
        (tmp_path / "Documents" / name).write_text("changed", encoding="utf-8")

    assert undo_run(journal).replaced == 2


def test_undo_exits_1_when_files_are_only_gone(tmp_path, capsys):
    aged_file(tmp_path, "a.txt")
    main(["run", "--source", str(tmp_path), "--delay", "0"])
    (tmp_path / "Documents" / "a.txt").unlink()
    capsys.readouterr()

    assert main(["undo"]) == 1
    assert "cubby: 1 no longer where the run put it" in capsys.readouterr().err


def test_a_kept_copy_that_is_a_symlink_is_not_deduplicated_against(tmp_path):
    # Found by review: the identity was the link's, while the copy followed it.
    outside = aged_file(tmp_path.parent / "elsewhere", "report.txt", content="SAME")
    (tmp_path / "Documents").mkdir()
    (tmp_path / "Documents" / "report.txt").symlink_to(outside)
    aged_file(tmp_path, "report.txt", content="SAME")
    journal = Journal(tmp_path.parent / "s.jsonl")

    (outcome,) = Sorter(config_for(tmp_path, dedupe=True), journal=journal).sort_once(apply=True)

    assert outcome.moved_to == tmp_path / "Documents" / "report (1).txt"


def test_a_move_whose_identity_cannot_be_read_says_undo_will_not_check_it(tmp_path, monkeypatch):
    monkeypatch.setattr("cubby.adapters.filesystem.identity", lambda _: None)
    aged_file(tmp_path, "a.txt")
    warnings: list[str] = []
    journal = Journal(tmp_path.parent / "w.jsonl")

    Sorter(config_for(tmp_path), journal=journal, warn=warnings.append).sort_once(apply=True)

    assert journal.runs()[0].entries[0].ident is None
    assert any("could not read" in w and "a.txt" in w for w in warnings)


# --- from the re-review ----------------------------------------------------------


def _sorted_folder(tmp_path, *files: str) -> tuple[Journal, Path]:
    (tmp_path / "project").mkdir()
    for name in files:
        (tmp_path / "project" / name).write_text(name, encoding="utf-8")
    past = time.time() - 10_000
    os.utime(tmp_path / "project", (past, past))
    journal = Journal(tmp_path.parent / f"{tmp_path.name}.jsonl")
    Sorter(config_for(tmp_path), journal=journal).sort_once(apply=True)
    return journal, tmp_path / "_Unsorted" / "project"


def test_a_folder_deleted_and_made_again_is_left_in_place(tmp_path):
    # Found by re-review: ext4 gave the new folder the old one's inode, and
    # undo moved the user's new folder out.
    journal, filed = _sorted_folder(tmp_path, "a.txt")
    shutil.rmtree(filed)
    filed.mkdir()
    (filed / "users-own.txt").write_text("mine", encoding="utf-8")
    os.utime(filed / "users-own.txt", ns=(1, 1))  # made later, not in the same clock tick

    result = undo_run(journal)

    assert (result.restored, result.replaced) == (0, 1)
    assert (filed / "users-own.txt").exists()


def test_a_folder_whose_files_were_renamed_or_added_still_comes_back(tmp_path):
    journal, filed = _sorted_folder(tmp_path, "a.txt", "b.txt")
    (filed / "a.txt").rename(filed / "z.txt")
    (filed / "new.txt").write_text("added", encoding="utf-8")

    assert undo_run(journal).restored == 1


def test_an_empty_folder_comes_back(tmp_path):
    journal, _ = _sorted_folder(tmp_path)

    assert undo_run(journal).restored == 1
    assert (tmp_path / "project").is_dir()


def test_an_empty_folder_made_again_is_left_in_place(tmp_path):
    journal, filed = _sorted_folder(tmp_path)
    filed.rmdir()
    filed.mkdir()
    os.utime(filed, ns=(1, 1))  # whatever its inode, its time tells it apart

    assert undo_run(journal).replaced == 1


def test_the_device_number_is_not_compared(tmp_path):
    # macOS numbers an external drive anew at each mount.
    path = tmp_path / "a.txt"
    path.write_text("x", encoding="utf-8")
    dev, ino, size, mtime = identity(path)

    assert same_file(path, (dev + 1, ino, size, mtime))
    assert not same_file(path, (dev, ino + 1, size, mtime))


def test_a_destination_under_a_file_now_is_gone(tmp_path):
    journal, _ = _sorted(tmp_path, "a.txt")
    shutil.rmtree(tmp_path / "Documents")
    (tmp_path / "Documents").write_text("a file now", encoding="utf-8")

    result = undo_run(journal)

    assert (result.gone, result.failed) == (1, [])


# --- from the second re-review ---------------------------------------------------


def _sorted_tree(tmp_path, *files: str) -> tuple[Journal, Path]:
    for name in files:
        path = tmp_path / "holiday" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name, encoding="utf-8")
    past = time.time() - 10_000
    os.utime(tmp_path / "holiday", (past, past))
    journal = Journal(tmp_path.parent / f"{tmp_path.name}.jsonl")
    Sorter(config_for(tmp_path), journal=journal).sort_once(apply=True)
    return journal, tmp_path / "_Unsorted" / "holiday"


def test_a_folder_of_folders_opened_in_finder_still_comes_back(tmp_path):
    # Found by re-review: the witness was a subfolder, whose time Finder changes.
    journal, filed = _sorted_tree(tmp_path, "2024/p.jpg", "2025/q.jpg")
    (filed / ".DS_Store").write_bytes(b"finder")  # the folder's own time changes too
    (filed / "2024" / ".DS_Store").write_bytes(b"finder")

    assert undo_run(journal).restored == 1


def test_a_folder_of_folders_made_again_is_left_in_place(tmp_path):
    journal, filed = _sorted_tree(tmp_path, "2024/p.jpg")
    shutil.rmtree(filed)
    (filed / "2024").mkdir(parents=True)
    (filed / "2024" / "p.jpg").write_text("another", encoding="utf-8")
    os.utime(filed / "2024" / "p.jpg", ns=(1, 1))  # made later, not in the same clock tick

    assert undo_run(journal).replaced == 1


def test_the_witness_is_a_file_before_a_folder(tmp_path):
    journal, filed = _sorted_tree(tmp_path, "a/x.txt", "z.txt")
    (filed / "a" / ".DS_Store").write_bytes(b"finder")

    assert undo_run(journal).restored == 1


def test_a_hidden_file_is_never_the_witness(tmp_path):
    journal, filed = _sorted_tree(tmp_path, ".DS_Store", "a.txt")
    (filed / ".DS_Store").write_bytes(b"rewritten by finder")

    assert undo_run(journal).restored == 1


@pytest.mark.parametrize("change", ["time", "size"])
def test_a_file_differing_in_time_or_size_alone_is_another_file(tmp_path, change):
    journal, _ = _sorted(tmp_path, "a.txt")
    filed = tmp_path / "Documents" / "a.txt"
    before = filed.stat()
    if change == "time":
        os.utime(filed, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))
    else:
        filed.write_text("xx", encoding="utf-8")
        os.utime(filed, ns=(before.st_atime_ns, before.st_mtime_ns))

    assert undo_run(journal).replaced == 1


def test_a_file_dated_before_1970_is_still_checked(tmp_path):
    # Found by re-review: a negative time made the journal drop the identity.
    aged_file(tmp_path, "old.txt")
    os.utime(tmp_path / "old.txt", (-86_400, -86_400))
    journal = Journal(tmp_path.parent / "o.jsonl")
    Sorter(config_for(tmp_path), journal=journal).sort_once(apply=True)
    (tmp_path / "Documents" / "old.txt").write_text("replaced", encoding="utf-8")

    assert journal.runs()[0].entries[0].ident is not None
    assert undo_run(journal).replaced == 1


@pytest.mark.parametrize(("depth", "found"), [(3, True), (4, False)])
def test_the_witness_is_looked_for_three_levels_down(tmp_path, depth, found):
    folder = tmp_path / "top"
    deep = folder.joinpath(*[f"l{i}" for i in range(1, depth)])
    deep.mkdir(parents=True)
    (deep / "f.txt").write_text("x", encoding="utf-8")

    recorded = identity(folder)

    assert (recorded[2] == (deep / "f.txt").stat().st_ino) is found


def test_a_symlinked_folder_never_supplies_the_witness(tmp_path):
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "f.txt").write_text("x", encoding="utf-8")
    (tmp_path / "top").mkdir()
    (tmp_path / "top" / "a-link").symlink_to(tmp_path / "outside")

    assert identity(tmp_path / "top")[2] == 0


def test_the_witness_is_the_first_file_by_name(tmp_path):
    (tmp_path / "top").mkdir()
    for name in ("b.txt", "a.txt", "c.txt"):
        (tmp_path / "top" / name).write_text(name, encoding="utf-8")

    assert identity(tmp_path / "top")[2] == (tmp_path / "top" / "a.txt").stat().st_ino


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads through any permission")
def test_an_unreadable_subfolder_does_not_hide_the_other_files(tmp_path):
    (tmp_path / "top" / "a-locked").mkdir(parents=True)
    (tmp_path / "top" / "b").mkdir()
    (tmp_path / "top" / "b" / "f.txt").write_text("x", encoding="utf-8")
    (tmp_path / "top" / "a-locked").chmod(0)
    try:
        recorded = identity(tmp_path / "top")
    finally:
        (tmp_path / "top" / "a-locked").chmod(0o755)

    assert recorded is not None
    assert recorded[2] == (tmp_path / "top" / "b" / "f.txt").stat().st_ino


@pytest.mark.parametrize(("limit", "found"), [(2, False), (3, True)])
def test_the_scan_stops_at_its_limit(tmp_path, monkeypatch, limit, found):
    monkeypatch.setattr("cubby.adapters.filesystem._WITNESS_SCAN", limit)
    (tmp_path / "top" / "a").mkdir(parents=True)
    (tmp_path / "top" / "b").mkdir()
    (tmp_path / "top" / "c.txt").write_text("x", encoding="utf-8")  # the third entry

    assert (identity(tmp_path / "top")[2] != 0) is found


def test_a_symlink_to_a_file_is_never_the_witness(tmp_path):
    # Mutation round 8: following links let a file outside the folder stand for it.
    (tmp_path / "outside.txt").write_text("x", encoding="utf-8")
    (tmp_path / "top").mkdir()
    (tmp_path / "top" / "a-link").symlink_to(tmp_path / "outside.txt")
    (tmp_path / "top" / "b.txt").write_text("b", encoding="utf-8")

    assert identity(tmp_path / "top")[2] == (tmp_path / "top" / "b.txt").stat().st_ino
