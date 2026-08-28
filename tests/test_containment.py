"""Regression battery: cubby never writes outside the folder it manages.

The defect: a category name is a plain string from the config file, joined
straight onto the source path. A name of "../../escaped" moved a file two levels
above the watched folder, which measurably happened. Every promise this tool
makes ("your root stays clean", "nothing is ever lost") depends on that not
being possible.

Two independent barriers are tested here, because one of them will eventually be
bypassed by a path nobody thought of: the config refuses a name that is not a
single safe folder component, and the move itself refuses a destination outside
the tree it was given.
"""

from __future__ import annotations

import os
import textwrap
import time
from pathlib import Path

import pytest

from cubby.adapters.config import load_config
from cubby.adapters.filesystem import move_into
from cubby.app.sorter import Sorter
from cubby.domain.category import Category, Config, Settings
from cubby.domain.engine import Engine

ESCAPING_NAMES = [
    "../escaped",
    "../../escaped",
    "/tmp/escaped",
    "nested/deeper",
    "..",
    ".",
    "",
    "   ",
]


def _aged_file(folder: Path, name: str = "report.pdf") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text("content", encoding="utf-8")
    old = time.time() - 10_000
    os.utime(path, (old, old))
    return path


# --- barrier 1: the configuration refuses the name -------------------------


@pytest.mark.parametrize("name", ESCAPING_NAMES)
def test_a_category_name_must_be_one_safe_folder_component(name, tmp_path):
    user = tmp_path / "config.toml"
    user.write_text(
        textwrap.dedent(f"""
            [[category]]
            name = "{name}"
            extensions = ["pdf"]
        """),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="name"):
        load_config(user_path=user)


@pytest.mark.parametrize("name", ["../escaped", "/tmp/escaped", "nested/deeper", ""])
def test_the_unsorted_folder_name_is_checked_too(name, tmp_path):
    user = tmp_path / "config.toml"
    user.write_text(
        textwrap.dedent(f"""
            [settings]
            unsorted_dir = "{name}"

            [[category]]
            name = "Documents"
            extensions = ["pdf"]
        """),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unsorted_dir"):
        load_config(user_path=user)


def test_ordinary_names_are_still_accepted(tmp_path):
    user = tmp_path / "config.toml"
    user.write_text(
        textwrap.dedent("""
            [settings]
            unsorted_dir = "_Unsorted"

            [[category]]
            name = "Facturés 2026"
            extensions = ["pdf"]
        """),
        encoding="utf-8",
    )
    config = load_config(user_path=user)
    assert [c.name for c in config.categories] == ["Facturés 2026"]


# --- barrier 2: the move refuses the destination ----------------------------


def test_move_into_refuses_a_destination_outside_its_root(tmp_path):
    source = tmp_path / "downloads"
    path = _aged_file(source)
    outside = tmp_path / "escaped"

    with pytest.raises(ValueError, match="outside"):
        move_into(path, outside, root=source)

    assert path.exists(), "the file must not be moved when the destination is refused"
    assert not outside.exists()


@pytest.mark.parametrize("rename_to", ["../escaped.pdf", "sub/dir.pdf", "/tmp/x.pdf"])
def test_move_into_refuses_a_rename_that_escapes(rename_to, tmp_path):
    source = tmp_path / "downloads"
    path = _aged_file(source)
    category = source / "Documents"

    with pytest.raises(ValueError, match="outside|name"):
        move_into(path, category, root=source, rename_to=rename_to)

    assert path.exists()


def test_move_into_still_accepts_a_destination_inside_the_root(tmp_path):
    source = tmp_path / "downloads"
    path = _aged_file(source)

    destination = move_into(path, source / "Documents" / "2026-08", root=source)

    assert destination == source / "Documents" / "2026-08" / "report.pdf"
    assert destination.is_file()
    assert not path.exists()


# --- the end-to-end promise -------------------------------------------------


def test_the_escaping_name_cannot_even_be_constructed(tmp_path):
    # The measured defect, at its source: this exact configuration moved a file
    # two levels above the watched folder. The domain object now refuses to
    # exist, so no code path can carry the name as far as a write.
    with pytest.raises(ValueError, match="name"):
        Category(name="../../escaped", extensions=frozenset({"pdf"}), strong_ext=True)


def test_a_sort_keeps_every_file_inside_the_watched_folder(tmp_path):
    source = tmp_path / "downloads"
    _aged_file(source)
    config = Config(
        settings=Settings(source=source, delay=0, content_scan=False),
        categories=(Category(name="Documents", extensions=frozenset({"pdf"}), strong_ext=True),),
    )

    Sorter(config, Engine(config)).sort_once(apply=True)

    assert (source / "Documents" / "report.pdf").is_file()
    written_outside = [p for p in tmp_path.rglob("report.pdf") if source not in p.parents]
    assert written_outside == []
