"""End-to-end: a realistic folder is sorted through the default config, with the
content stage rescuing a cryptically named file."""

from cubby.adapters.config import load_config
from cubby.app.sorter import Sorter


def _category_of(tmp_path, filename):
    # Invoices land in a month/year subfolder, so search recursively and return
    # the top-level category folder the file ended up under.
    for path in tmp_path.rglob(filename):
        return path.relative_to(tmp_path).parts[0]
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


def test_invoice_is_filed_by_month_and_renamed(tmp_path):
    # Cryptic name; content reveals the vendor (Spotify) and the invoice date.
    (tmp_path / "3c0fe.txt").write_text(
        "Facture du 07/07/2026. Merci pour votre abonnement Spotify. Invoice number 42."
    )
    config = load_config(
        user_path=None, overrides={"settings": {"source": str(tmp_path), "delay": 0}}
    )
    Sorter(config).sort_once(apply=True)

    assert (tmp_path / "Invoices" / "2026-07" / "spotify facture 2026-07-07.txt").exists()
