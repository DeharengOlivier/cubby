"""A file named like a folder cubby sorts into.

Found by the property test of sorting then undoing: a regular file called
``_Unsorted`` (or ``Documents``, or ``Invoices`` when invoices are filed by
month) stood where cubby creates a folder, and every file bound for it failed
with ``[Errno 17] File exists``, a message that names neither the cause nor the
remedy. The failure stays (moving the user's file aside is not cubby's call),
but it now says which file is in the way and what to do.
"""

from __future__ import annotations

import errno

import pytest

from cubby.adapters import filesystem
from cubby.adapters.filesystem import move_into
from cubby.app.sorter import Sorter
from tests.helpers import aged_file, config_for


@pytest.mark.parametrize("blocker", ["Documents", "_Unsorted"])
def test_the_file_in_the_way_is_named_with_the_remedy(tmp_path, blocker):
    aged_file(tmp_path, blocker)
    aged_file(tmp_path, "notes.txt")
    aged_file(tmp_path, "0")

    outcomes = Sorter(config_for(tmp_path)).sort_once(apply=True)

    failed = [o for o in outcomes if o.error]
    assert failed
    for outcome in failed:
        assert f"a file named {blocker} is in the way" in outcome.error
        assert "rename or move it" in outcome.error
    assert (tmp_path / blocker).is_file()  # the user's file is left alone
    assert {o.name for o in outcomes if not o.error} == {"notes.txt", "0"} - {
        o.name for o in failed
    }


@pytest.mark.parametrize("blocker", ["Invoices", "Invoices/2026-09"])
def test_a_file_in_the_way_of_a_month_folder_is_named_too(tmp_path, blocker):
    (tmp_path / blocker).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / blocker).write_text("mine", encoding="utf-8")
    invoice = aged_file(tmp_path, "bill.pdf")

    with pytest.raises(OSError, match=f"a file named {blocker} is in the way"):
        move_into(invoice, tmp_path / "Invoices" / "2026-09", root=tmp_path)

    assert invoice.exists()
    assert (tmp_path / blocker).read_text("utf-8") == "mine"


def test_the_error_keeps_its_type_errno_and_cause(tmp_path):
    aged_file(tmp_path, "Documents")
    notes = aged_file(tmp_path, "notes.txt")

    with pytest.raises(FileExistsError) as caught:
        move_into(notes, tmp_path / "Documents", root=tmp_path)

    assert caught.value.errno == errno.EEXIST
    assert caught.value.filename == str(tmp_path / "Documents")
    assert isinstance(caught.value.__cause__, FileExistsError)


def test_a_dangling_symlink_in_the_way_is_named(tmp_path):
    # Found by review: exists() follows the link, so it was not named.
    (tmp_path / "Documents").symlink_to(tmp_path / "gone")
    notes = aged_file(tmp_path, "notes.txt")

    with pytest.raises(OSError, match="a file named Documents is in the way"):
        move_into(notes, tmp_path / "Documents", root=tmp_path)


def test_a_blocker_gone_before_the_search_leaves_the_original_error(tmp_path, monkeypatch):
    notes = aged_file(tmp_path, "notes.txt")
    monkeypatch.setattr(filesystem, "_file_in_the_way", lambda folder, root: None)
    aged_file(tmp_path, "Documents")

    with pytest.raises(FileExistsError) as caught:
        move_into(notes, tmp_path / "Documents", root=tmp_path)

    assert "in the way" not in str(caught.value)


def test_a_search_that_fails_leaves_the_original_error(tmp_path, monkeypatch):
    # Found by review: a stat refused during the search replaced the real error.
    notes = aged_file(tmp_path, "notes.txt")
    aged_file(tmp_path, "Documents")

    def refused(folder, root):
        raise PermissionError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(filesystem, "_file_in_the_way", refused)

    with pytest.raises(FileExistsError):
        move_into(notes, tmp_path / "Documents", root=tmp_path)


def test_a_blocker_outside_the_root_spelling_is_shown_in_full(tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    aged_file(elsewhere, "Documents")

    with pytest.raises(OSError, match=f"a file named {elsewhere / 'Documents'} is in the way"):
        filesystem._make_folder(elsewhere / "Documents", tmp_path / "root")
