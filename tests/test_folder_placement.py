"""A folder matched by its name is filed whole, under its own name."""

from __future__ import annotations

import os
import time

import pytest

from cubby.cli import EXIT_OK, main
from cubby.domain.invoices import is_month_folder


def _aged_folder(parent, name):
    folder = parent / name
    folder.mkdir(parents=True)
    (folder / "a.txt").write_text("x", encoding="utf-8")
    old = time.time() - 7200
    for path in (folder / "a.txt", folder):
        os.utime(path, (old, old))
    return folder


def _run(source, *extra):
    return main(["run", "--source", str(source), "--delay", "0", *extra])


def test_a_folder_named_like_invoices_keeps_its_name_at_the_category_root(tmp_path):
    source = tmp_path / "dl"
    _aged_folder(source, "invoice-archive")

    assert _run(source) == EXIT_OK

    moved = source / "Invoices" / "invoice-archive"
    assert moved.is_dir()
    assert (moved / "a.txt").read_text(encoding="utf-8") == "x"
    assert sorted(p.name for p in (source / "Invoices").iterdir()) == ["invoice-archive"]


def test_the_plan_shows_the_folder_under_its_own_name(tmp_path, capsys):
    source = tmp_path / "dl"
    _aged_folder(source, "invoice-archive")

    assert main(["plan", "--source", str(source), "--delay", "0"]) == EXIT_OK

    out = capsys.readouterr().out
    assert "Invoices/  (1)" in out
    assert "invoice-archive" in out
    assert "facture" not in out


def test_an_unmatched_folder_still_goes_whole_to_unsorted(tmp_path):
    source = tmp_path / "dl"
    _aged_folder(source, "random-folder")

    assert _run(source) == EXIT_OK

    assert (source / "_Unsorted" / "random-folder" / "a.txt").exists()


def test_a_folder_named_like_a_month_folder_does_not_become_one(tmp_path):
    source = tmp_path / "dl"
    config = tmp_path / "config.toml"
    config.write_text(
        "[[category]]\n"
        'name = "Invoices"\n'
        'name_patterns = ["invoice", "^20\\\\d\\\\d-\\\\d\\\\d$"]\n'
        "date_folders = true\n",
        encoding="utf-8",
    )
    _aged_folder(source, "2026-09")

    assert main(["run", "--config", str(config), "--source", str(source), "--delay", "0"]) == 0

    assert (source / "Invoices" / "2026-09 (folder)" / "a.txt").exists()
    assert not (source / "Invoices" / "2026-09").exists()


@pytest.mark.parametrize(
    ("name", "style", "lang", "expected"),
    [
        ("2026-09", "numeric", "fr", True),
        ("2026-13", "numeric", "fr", False),
        ("2026-09-01", "numeric", "fr", False),
        ("septembre 2026", "letters", "fr", True),
        ("Septembre 2026", "letters", "fr", True),
        ("September 2026", "letters", "en", True),
        ("September 2026", "letters", "fr", False),
        ("septembre 26", "letters", "fr", False),
        ("2026-09", "letters", "fr", False),
        ("invoice-archive", "numeric", "fr", False),
    ],
)
def test_month_folder_names_are_recognised(name, style, lang, expected):
    assert is_month_folder(name, style, lang) is expected


def test_a_matched_folder_comes_back_under_its_name_on_undo(tmp_path):
    source = tmp_path / "dl"
    _aged_folder(source, "invoice-archive")
    assert _run(source) == EXIT_OK

    assert main(["undo"]) == EXIT_OK

    assert (source / "invoice-archive" / "a.txt").exists()
