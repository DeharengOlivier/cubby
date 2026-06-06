from cubby.adapters.filesystem import files_identical, move_into


def test_files_identical(tmp_path):
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("same")
    b.write_text("same")
    assert files_identical(a, b) is True
    b.write_text("different")
    assert files_identical(a, b) is False


def test_move_into_dedupe_drops_identical_duplicate(tmp_path):
    dest_dir = tmp_path / "Documents"
    dest_dir.mkdir()
    (dest_dir / "note.txt").write_text("hello")
    src = tmp_path / "note.txt"
    src.write_text("hello")

    result = move_into(src, dest_dir, dedupe=True)

    assert result == dest_dir / "note.txt"
    assert not src.exists()  # the redundant copy was removed
    assert not (dest_dir / "note (1).txt").exists()


def test_move_into_keeps_different_file_with_suffix(tmp_path):
    dest_dir = tmp_path / "Documents"
    dest_dir.mkdir()
    (dest_dir / "note.txt").write_text("original")
    src = tmp_path / "note.txt"
    src.write_text("changed")

    result = move_into(src, dest_dir, dedupe=True)

    assert result == dest_dir / "note (1).txt"
    assert (dest_dir / "note.txt").read_text() == "original"
