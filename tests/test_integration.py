"""End-to-end: a realistic folder is sorted through the default config, with the
content stage rescuing a cryptically named file."""

from cubby.adapters.config import load_config
from cubby.app.sorter import Sorter


def _category_of(tmp_path, filename):
    for child in tmp_path.iterdir():
        if child.is_dir() and (child / filename).exists():
            return child.name
    return None


def test_full_sort_with_default_config(tmp_path):
    # A spread of realistic downloads.
    (tmp_path / "Invoice-2026-001.pdf").write_text("x")
    (tmp_path / "holiday.png").write_text("x")
    (tmp_path / "Setup.dmg").write_text("x")
    (tmp_path / "song.mp3").write_text("x")
    (tmp_path / "archive.zip").write_text("x")
    (tmp_path / "boarding-pass-eurostar.pdf").write_text("x")
    # Cryptic name, but the content gives it away.
    (tmp_path / "8f2a1c.txt").write_text("Total amount due: 100 EUR. Invoice number 42.")

    config = load_config(
        user_path=None, overrides={"settings": {"source": str(tmp_path), "delay": 0}}
    )
    Sorter(config).sort_once(apply=True)

    assert _category_of(tmp_path, "Invoice-2026-001.pdf") == "Invoices"
    assert _category_of(tmp_path, "holiday.png") == "Images"
    assert _category_of(tmp_path, "Setup.dmg") == "Installers"
    assert _category_of(tmp_path, "song.mp3") == "Music"
    assert _category_of(tmp_path, "archive.zip") == "Archives"
    assert _category_of(tmp_path, "boarding-pass-eurostar.pdf") == "Travel"
    assert _category_of(tmp_path, "8f2a1c.txt") == "Invoices"  # rescued by content
    assert not list(tmp_path.glob("*.pdf"))  # nothing left at the root
