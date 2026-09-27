"""A move never replaces a file, even one that appears after the name was chosen.

os.rename silently replaces an existing destination on POSIX. Cubby used to
pick a free name, then move with shutil.move: a file landing at that name in
between (another download, another cubby) was overwritten.
"""

from __future__ import annotations

import errno

import pytest

from cubby.adapters import filesystem
from cubby.adapters.filesystem import move_into, move_no_clobber, unique_destination


def test_moving_onto_an_existing_file_fails_and_keeps_both(tmp_path):
    source = tmp_path / "a.txt"
    source.write_text("new")
    taken = tmp_path / "b.txt"
    taken.write_text("old")

    with pytest.raises(FileExistsError):
        move_no_clobber(source, taken)

    assert source.read_text() == "new"
    assert taken.read_text() == "old"


def test_a_name_taken_between_choice_and_move_gets_the_next_name(tmp_path, monkeypatch):
    source = tmp_path / "report.txt"
    source.write_text("mine")
    category = tmp_path / "Documents"
    category.mkdir()
    real_link = filesystem.os.link
    raced = {"done": False}

    def racing_link(src, dst):
        if not raced["done"]:
            raced["done"] = True
            (category / "report.txt").write_text("someone else's")
        return real_link(src, dst)

    monkeypatch.setattr(filesystem.os, "link", racing_link)

    moved = move_into(source, category, root=tmp_path)

    assert moved.destination == category / "report (1).txt"
    assert (category / "report.txt").read_text() == "someone else's"
    assert moved.destination.read_text() == "mine"


def test_without_hard_links_the_move_still_refuses_to_clobber(tmp_path, monkeypatch):
    def no_links(src, dst):
        raise OSError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(filesystem.os, "link", no_links)
    source = tmp_path / "a.txt"
    source.write_text("new")
    taken = tmp_path / "b.txt"
    taken.write_text("old")

    with pytest.raises(FileExistsError):
        move_no_clobber(source, taken)

    move_no_clobber(source, tmp_path / "c.txt")
    assert (tmp_path / "c.txt").read_text() == "new"
    assert not source.exists()


def test_an_unexpected_link_error_is_not_swallowed(tmp_path, monkeypatch):
    def broken(src, dst):
        raise OSError(errno.EIO, "I/O error")

    monkeypatch.setattr(filesystem.os, "link", broken)
    source = tmp_path / "a.txt"
    source.write_text("x")

    with pytest.raises(OSError, match="I/O error"):
        move_no_clobber(source, tmp_path / "b.txt")
    assert source.exists()


def test_folders_are_moved_too(tmp_path):
    folder = tmp_path / "album"
    folder.mkdir()
    (folder / "song.mp3").write_text("x")

    move_no_clobber(folder, tmp_path / "Music-album")

    assert (tmp_path / "Music-album" / "song.mp3").exists()


def test_a_dangling_symlink_counts_as_taken(tmp_path):
    (tmp_path / "a.txt").symlink_to(tmp_path / "nowhere")
    assert unique_destination(tmp_path, "a.txt") == tmp_path / "a (1).txt"


def test_a_folder_where_every_name_keeps_being_taken_gives_up(tmp_path, monkeypatch):
    source = tmp_path / "a.txt"
    source.write_text("x")
    category = tmp_path / "Documents"

    def always_taken(src, dst):
        raise FileExistsError(errno.EEXIST, "exists", str(dst))

    monkeypatch.setattr(filesystem, "move_no_clobber", always_taken)

    with pytest.raises(FileExistsError, match="every candidate name"):
        move_into(source, category, root=tmp_path)
    assert source.exists()
