"""Invoice names and months that the exploratory session found wrong.

- A vendor was made up from generic words: ``Invoices.pdf`` became
  ``invoices facture ...`` and ``Your bill.pdf`` became ``your facture ...``,
  while the README says cubby never guesses.
- A date in the file name was ignored: ``Invoice-2026-08-spotify.pdf`` went to
  ``2026-09/`` (the download month) and was renamed with the download date, as
  if it were the invoice's.
- The download date was written into the new name as the invoice date.
- A French invoice with a cryptic name (``Facture Free Mobile du 05/08/2026
  montant 19,99 EUR``) was not recognised by its content.
"""

from __future__ import annotations

from datetime import date

import pytest

from cubby.domain.invoices import detect_vendor, plan_placement

DOWNLOADED = date(2026, 9, 21)


def _place(name: str, text: str = "", vendors=()):
    return plan_placement(
        name=name, text=text, fallback_date=DOWNLOADED, vendor_rename=True, vendors=vendors
    )


@pytest.mark.parametrize(
    "name",
    ["Invoices.pdf", "Your bill.pdf", "My receipts.pdf", "Votre facture.pdf", "bills.pdf",
     "Invoice copy.pdf", "Facture scan.pdf", "aout facture.pdf"],
)  # fmt: skip
def test_generic_words_are_not_a_vendor(name):
    assert detect_vendor(name, "", []) is None
    assert _place(name).new_name is None


@pytest.mark.parametrize(
    ("name", "month", "renamed"),
    [
        ("Invoice-2026-08-spotify.pdf", "2026-08", "spotify facture 2026-08.pdf"),
        ("Facture EDF août 2026.pdf", "2026-08", "edf facture 2026-08.pdf"),
        ("Facture_OVH_2026-07-14.pdf", "2026-07", "ovh facture 2026-07-14.pdf"),
        ("netflix 03.06.2026.pdf", "2026-06", "netflix facture 2026-06-03.pdf"),
    ],
)
def test_a_date_in_the_name_comes_before_the_download_date(name, month, renamed):
    placement = _place(name)

    assert (placement.subdir, placement.new_name) == (month, renamed)


def test_a_date_in_the_content_comes_before_the_name():
    placement = _place("Invoice-2026-08-spotify.pdf", "Facture du 30/07/2026")

    assert (placement.subdir, placement.new_name) == ("2026-07", "spotify facture 2026-07-30.pdf")


def test_the_download_date_is_never_written_as_the_invoice_date():
    placement = _place("spotify-invoice.pdf")

    assert placement.subdir == "2026-09"  # filed by when it arrived, for want of better
    assert placement.new_name == "spotify facture.pdf"


def test_an_invoice_is_recognised_by_its_content(tmp_path):
    from cubby.adapters.config import load_config
    from cubby.domain.engine import Engine
    from cubby.domain.file_ref import FileRef, Stage

    config = load_config(user_path=None, overrides={"settings": {"source": str(tmp_path)}})
    engine = Engine(config)
    text = "Facture Free Mobile du 05/08/2026 montant 19,99 EUR"
    ref = FileRef(name="3c0fe3ad.pdf", stem="3c0fe3ad", ext="pdf", read_text=lambda: text)

    decision = engine.classify(ref)

    assert (decision.category, decision.stage) == ("Invoices", Stage.CONTENT)


def test_a_word_invoice_alone_in_a_text_is_not_enough(tmp_path):
    from cubby.adapters.config import load_config
    from cubby.domain.engine import Engine
    from cubby.domain.file_ref import FileRef

    config = load_config(user_path=None, overrides={"settings": {"source": str(tmp_path)}})
    text = "Notes: remember to send the invoice to the client next week."
    ref = FileRef(name="notes.pdf", stem="notes", ext="pdf", read_text=lambda: text)

    assert Engine(config).classify(ref).category != "Invoices"


def test_explain_says_the_content_was_read_and_matched_nothing(tmp_path, capsys):
    import json

    from cubby.cli import main

    (tmp_path / "notes.txt").write_text("nothing to see here", encoding="utf-8")
    (tmp_path / "invoice.txt").write_text("x", encoding="utf-8")

    main(["explain", "--source", str(tmp_path), str(tmp_path / "notes.txt")])
    text = capsys.readouterr().out
    main(["explain", "--source", str(tmp_path), "--json",
          str(tmp_path / "notes.txt"), str(tmp_path / "invoice.txt")])  # fmt: skip
    items = json.loads(capsys.readouterr().out)["items"]

    assert "read (19 characters), no content pattern matched" in text
    assert [item["content_chars"] for item in items] == [19, None]  # the name decided
