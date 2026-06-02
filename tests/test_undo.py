from cubby.adapters.journal import Journal
from cubby.app.sorter import Sorter
from cubby.app.undo import undo_last_run
from cubby.domain.category import Category, Config, Settings


def _config(tmp_path):
    return Config(
        settings=Settings(source=tmp_path, delay=0, content_scan=False),
        categories=(
            Category(name="Invoices", name_patterns=("invoice",)),
            Category(name="Documents", extensions=frozenset({"txt"})),
        ),
    )


def test_journal_round_trip(tmp_path):
    journal = Journal(tmp_path / "journal.jsonl")
    journal.record_run([(tmp_path / "a.txt", tmp_path / "Documents" / "a.txt")])
    last = journal.last_run()
    assert last == [(tmp_path / "a.txt", tmp_path / "Documents" / "a.txt")]


def test_run_then_undo_restores_files(tmp_path):
    (tmp_path / "Invoice-1.pdf").write_text("x")
    (tmp_path / "note.txt").write_text("y")
    journal = Journal(tmp_path / "journal.jsonl")

    Sorter(_config(tmp_path), journal=journal).sort_once(apply=True)
    assert (tmp_path / "Invoices" / "Invoice-1.pdf").exists()

    restored = undo_last_run(journal)
    assert restored == 2
    assert (tmp_path / "Invoice-1.pdf").exists()
    assert (tmp_path / "note.txt").exists()
    assert not (tmp_path / "Invoices" / "Invoice-1.pdf").exists()
    # The run is consumed, so a second undo does nothing.
    assert undo_last_run(journal) == 0


def test_undo_with_empty_journal(tmp_path):
    assert undo_last_run(Journal(tmp_path / "none.jsonl")) == 0
