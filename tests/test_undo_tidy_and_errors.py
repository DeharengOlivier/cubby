"""Findings 10 and 13 of the exploratory session.

- After ``cubby undo``, every category folder the run had filled was left
  behind, empty: undo did not put the folder back as it was.
- A file that could not be moved was reported as a raw Python exception with
  two absolute paths: the message said nothing a person could act on.
"""

from __future__ import annotations

import os

import pytest

from cubby.adapters.journal import Journal
from cubby.app.sorter import Sorter, describe_error
from cubby.app.undo import undo_run
from tests.helpers import aged_file, config_for


def _run(tmp_path, *names):
    for name in names:
        aged_file(tmp_path, name)
    journal = Journal(tmp_path.parent / f"{tmp_path.name}.jsonl")
    Sorter(config_for(tmp_path), journal=journal).sort_once(apply=True)
    return journal


def test_undo_removes_the_folders_it_emptied(tmp_path):
    journal = _run(tmp_path, "a.txt", "b.txt")

    undo_run(journal)

    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.txt", "b.txt"]


def test_undo_keeps_a_folder_that_still_holds_something(tmp_path):
    journal = _run(tmp_path, "a.txt")
    (tmp_path / "Documents" / "mine.txt").write_text("the user's", encoding="utf-8")

    undo_run(journal)

    assert (tmp_path / "Documents" / "mine.txt").exists()


def test_undo_removes_an_emptied_month_folder_and_its_category(tmp_path):
    from cubby.domain.category import Category

    invoices = Category(name="Invoices", name_patterns=("invoice",), date_folders=True)
    aged_file(tmp_path, "invoice.txt")
    journal = Journal(tmp_path.parent / "m.jsonl")
    Sorter(config_for(tmp_path, invoices), journal=journal).sort_once(apply=True)
    assert (tmp_path / "Invoices").is_dir()

    undo_run(journal)

    assert not (tmp_path / "Invoices").exists()


def test_undo_never_removes_the_watched_folder(tmp_path):
    source = tmp_path / "Downloads"
    journal = _run(source, "a.txt")

    undo_run(journal)

    assert (source / "a.txt").exists()


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes through any permission")
def test_a_refused_move_is_described_in_words_with_short_paths(tmp_path):
    aged_file(tmp_path, "fresh.txt")
    (tmp_path / "Documents").mkdir()
    (tmp_path / "Documents").chmod(0o555)
    warnings: list[str] = []
    try:
        (outcome,) = Sorter(config_for(tmp_path), warn=warnings.append).sort_once(apply=True)
    finally:
        (tmp_path / "Documents").chmod(0o755)

    assert outcome.error is not None
    assert str(tmp_path) not in outcome.error
    assert outcome.error.startswith("PermissionError: Permission denied: ")
    assert "fresh.txt -> Documents/" in outcome.error
    assert "check that cubby may write there" in outcome.error


def test_an_error_without_a_file_keeps_its_own_words():
    assert describe_error(ValueError("bad value")) == "ValueError: bad value"
    assert describe_error(OSError()) == "OSError: no detail"
