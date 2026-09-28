"""Every move cubby makes can be undone, whichever command made it.

Reproducers for the defects found by audit 1 (docs/audits/2026-09-28-audit-1.md):
the agent's moves were never journaled, a failure part way through a run lost
the journal of the files already moved, and undoing a deduplicated file moved
the copy that had been kept.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cubby import cli
from cubby.adapters.journal import Journal
from cubby.app import sorter as sorter_mod
from cubby.app.sorter import Sorter
from cubby.app.undo import undo_last_run
from cubby.app.watcher import Watcher
from cubby.cli import EXIT_OK, main
from cubby.domain.category import Category, Config, Settings


def _config(source: Path, **settings) -> Config:
    return Config(
        settings=Settings(source=source, delay=0, content_scan=False, **settings),
        categories=(
            Category(name="Invoices", name_patterns=("invoice",)),
            Category(name="Images", extensions=frozenset({"png"})),
            Category(name="Documents", extensions=frozenset({"txt"})),
        ),
    )


def _files(root: Path) -> list[str]:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


@pytest.fixture
def downloads(tmp_path) -> Path:
    source = tmp_path / "Downloads"
    source.mkdir()
    return source


def test_files_sorted_by_the_agent_can_be_undone(downloads, tmp_path, monkeypatch):
    (downloads / "invoice-2026.txt").write_text("a")
    (downloads / "photo.png").write_text("b")
    config = tmp_path / "config.toml"
    config.write_text(
        '[[category]]\nname = "Invoices"\nname_patterns = ["invoice"]\n'
        '[[category]]\nname = "Images"\nextensions = ["png"]\n',
        encoding="utf-8",
    )

    class OneCycle(Watcher):
        def run(self, **_):
            return super().run(max_cycles=1)

    monkeypatch.setattr(cli, "Watcher", OneCycle)
    argv = ["watch", "--config", str(config), "--source", str(downloads), "--delay", "0"]
    assert main(argv) == EXIT_OK
    assert _files(downloads) == ["Images/photo.png", "Invoices/invoice-2026.txt"]

    assert main(["undo"]) == EXIT_OK

    assert _files(downloads) == ["invoice-2026.txt", "photo.png"]


def test_a_failing_file_does_not_stop_the_run_or_lose_the_journal(downloads, monkeypatch):
    for name in ("a-invoice.txt", "b.png", "c.txt"):
        (downloads / name).write_text(name)
    real_move = sorter_mod.move_into

    def refuse_b(path, *args, **kwargs):
        if path.name == "b.png":
            raise PermissionError(13, "Permission denied", str(path))
        return real_move(path, *args, **kwargs)

    monkeypatch.setattr(sorter_mod, "move_into", refuse_b)
    journal = Journal(downloads.parent / "journal.jsonl")

    outcomes = Sorter(_config(downloads), journal=journal).sort_once(apply=True)

    by_name = {o.name: o for o in outcomes}
    assert by_name["b.png"].error is not None
    assert "Permission denied" in by_name["b.png"].error
    assert by_name["a-invoice.txt"].error is None
    assert by_name["c.txt"].error is None
    # The files after the failure were sorted too, and b.png stayed put.
    assert _files(downloads) == ["Documents/c.txt", "Invoices/a-invoice.txt", "b.png"]

    assert undo_last_run(journal) == 2
    assert _files(downloads) == ["a-invoice.txt", "b.png", "c.txt"]


def test_undoing_a_deduplicated_file_brings_the_duplicate_back(downloads):
    (downloads / "Images").mkdir()
    (downloads / "Images" / "p.png").write_text("same")
    (downloads / "p.png").write_text("same")
    journal = Journal(downloads.parent / "journal.jsonl")

    Sorter(_config(downloads, dedupe=True), journal=journal).sort_once(apply=True)
    assert _files(downloads) == ["Images/p.png"]

    assert undo_last_run(journal) == 1

    # Both copies exist again, and the one that was already filed stayed filed.
    assert _files(downloads) == ["Images/p.png", "p.png"]
    assert (downloads / "p.png").read_text() == "same"


@pytest.mark.parametrize("odd", ["\u2028", "\u2029", "\x85", "\x0b", "\x0c", "\x1c"])
def test_a_name_with_a_unicode_line_break_can_be_undone(downloads, odd):
    # Found by the journal property test: str.splitlines() also breaks on these,
    # so the journal line of such a file was torn in two and its move was lost.
    name = f"odd{odd}name.txt"
    (downloads / name).write_text("x")
    journal = Journal(downloads.parent / "journal.jsonl")

    Sorter(_config(downloads), journal=journal).sort_once(apply=True)
    assert _files(downloads) == [f"Documents/{name}"]

    assert undo_last_run(journal) == 1
    assert _files(downloads) == [name]
