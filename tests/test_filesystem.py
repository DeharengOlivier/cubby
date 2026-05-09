import os
import time

from cubby.adapters.filesystem import (
    build_ref,
    is_eligible,
    iter_candidates,
    move_into,
    unique_destination,
)
from cubby.domain.category import Settings


def test_move_into_creates_category_dir(tmp_path):
    src = tmp_path / "note.txt"
    src.write_text("hello")
    dest = move_into(src, tmp_path / "Documents")
    assert dest == tmp_path / "Documents" / "note.txt"
    assert dest.read_text() == "hello"
    assert not src.exists()


def test_move_into_never_overwrites(tmp_path):
    (tmp_path / "Documents").mkdir()
    (tmp_path / "Documents" / "note.txt").write_text("original")
    src = tmp_path / "note.txt"
    src.write_text("new")
    dest = move_into(src, tmp_path / "Documents")
    assert dest.name == "note (1).txt"
    assert (tmp_path / "Documents" / "note.txt").read_text() == "original"
    assert dest.read_text() == "new"


def test_unique_destination_counts_up(tmp_path):
    (tmp_path / "a.pdf").write_text("x")
    (tmp_path / "a (1).pdf").write_text("x")
    assert unique_destination(tmp_path, "a.pdf").name == "a (2).pdf"


def test_eligibility_respects_delay(tmp_path):
    f = tmp_path / "fresh.txt"
    f.write_text("x")
    settings = Settings(delay=60)
    assert is_eligible(f, settings, now=time.time()) is False
    old = time.time() + 120  # pretend "now" is well past the delay
    assert is_eligible(f, settings, now=old) is True


def test_eligibility_skips_in_progress_downloads(tmp_path):
    f = tmp_path / "movie.mp4.crdownload"
    f.write_text("x")
    os.utime(f, (0, 0))  # very old, so only the extension can disqualify it
    assert is_eligible(f, Settings(delay=0), now=time.time()) is False


def test_iter_candidates_skips_hidden_and_managed(tmp_path):
    (tmp_path / "keep.txt").write_text("x")
    (tmp_path / ".hidden").write_text("x")
    (tmp_path / "_Unsorted").mkdir()
    settings = Settings(source=tmp_path)
    names = {p.name for p in iter_candidates(settings)}
    assert names == {"keep.txt"}


def test_build_ref_exposes_extension_and_text(tmp_path):
    f = tmp_path / "Hello.TXT"
    f.write_text("invoice total due")
    ref = build_ref(f)
    assert ref.ext == "txt"
    assert "invoice" in ref.text()
