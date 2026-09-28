"""A file named like a folder cubby sorts into.

Found by the property test of sorting then undoing: a regular file called
``_Unsorted`` (or ``Documents``, or ``Invoices`` when invoices are filed by
month) stood where cubby creates a folder, and every file bound for it failed
with ``[Errno 17] File exists``, a message that names neither the cause nor the
remedy. The failure stays (moving the user's file aside is not cubby's call),
but it now says which file is in the way and what to do.
"""

from __future__ import annotations

import pytest

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
