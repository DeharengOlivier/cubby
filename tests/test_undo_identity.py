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
    recorded = [[1, 2, 3, 4], [1, 2], "x", [1, 2, 3, "4"], [True, 2, 3, 4], [-1, 2, 3, 4], None]
    lines = [json.dumps({**record, "seq": i, "id": ident}) for i, ident in enumerate(recorded)]
    journal.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    idents = [entry.ident for entry in journal.runs()[0].entries]

    assert idents == [(1, 2, 3, 4), None, None, None, None, None, None]


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
    out = capsys.readouterr().out

    assert code == 1
    assert "Restored 1 file(s)." in out
    assert "1 no longer where the run put it" in out
    assert "1 changed or replaced since the run, left in place" in out


def test_undo_says_when_a_file_comes_back_under_another_name(tmp_path, capsys):
    aged_file(tmp_path, "notes.txt")
    main(["run", "--source", str(tmp_path), "--delay", "0"])
    aged_file(tmp_path, "notes.txt", content="a new download with the same name")
    capsys.readouterr()

    main(["undo"])

    assert "restored notes.txt as notes (1).txt (notes.txt is taken)" in capsys.readouterr().out
