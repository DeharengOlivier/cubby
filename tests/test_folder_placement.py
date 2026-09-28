"""A folder matched by its name is filed whole, under its own name."""

from __future__ import annotations

import os
import time

from cubby.cli import EXIT_OK, main


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
