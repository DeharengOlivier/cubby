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


# --- from the review of this change ---------------------------------------------


def _category_of(tmp_path, text: str, name: str = "3c0fe3ad.pdf") -> str:
    from cubby.adapters.config import load_config
    from cubby.domain.engine import Engine
    from cubby.domain.file_ref import FileRef

    config = load_config(user_path=None, overrides={"settings": {"source": str(tmp_path)}})
    stem = name.rsplit(".", 1)[0]
    ref = FileRef(name=name, stem=stem, ext="pdf", read_text=lambda: text)
    return Engine(config).classify(ref).category


#: Found by review: statements, contracts and other documents that mention an
#: invoice, which the first version of the pattern filed as invoices.
NOT_INVOICES = {
    "statement, invoice in line 3": "Relevé de compte courant\nIBAN FR76 3000 4000\n"
    "05/08 PRLV SEPA FREE MOBILE FACTURE 05/08/2026 19,99 EUR\n",
    "statement, invoice in line 2": "Releve de compte\n05/08 PRLV SEPA FREE MOBILE FACTURE "
    "05/08/2026 19,99 EUR\nsolde",
    "statement in English": "Account statement\nDirect debit invoice Spotify 05/08/2026 10.99 EUR",
    "statement, a payment": "Releve\nPaiement facture EDF 05/08/2026 64,20 EUR",
    "one-line statement": "Votre releve  Solde 1 204,55 EUR  PRLV FREE MOBILE FACTURE "
    "05/08/2026 19,99 EUR",
    "bank export": "Date;Libelle;Montant\n05/08/2026;PRLV SEPA FREE MOBILE FACTURE 202608;-19,99\n",
    "sheet, cells joined": "Releve de compte Date Libelle Montant 05/08/2026 PRLV FREE FACTURE "
    "-19,99 EUR Solde 1 204,55 EUR",
    "contract": "Contrat de prestation de services, entre les soussignés\nArticle 4. "
    "Paiement\nLa facture est payable sous 30 jours, soit avant le 31/01/2026.\n",
    "terms": "Conditions generales\nToute facture emise le 01/01/2026 est payable a 30 jours",
    "agreement titled invoice": "Invoice terms and payment schedule\nThis agreement sets "
    "the terms. Payment within 30 days of 01/01/2026.",
    "quote": "DEVIS N 42\nConverti en facture le 05/09/2026\nTotal 1 200,00 EUR",
    "payslip": "BULLETIN DE PAIE\nPeriode du 01/08/2026 au 31/08/2026\nNet a payer 2 100,00 EUR",
    "release notes": "Invoice generator 2.1.10 released",
    "email": "Hello,\ncould you send me the invoice for August? Thanks, 05/08/2026",
}

INVOICES = {
    "title with a date": "Facture Free Mobile du 05/08/2026",
    "title with an amount": "Facture Free Mobile, montant 19,99 EUR",
    "the session's invoice": "Facture Free Mobile du 05/08/2026 montant 19,99 EUR",
    "heading, then a date": "Invoice\nDate: 2026-07-14\nTotal due 42.00 EUR",
    "company, then a numbered heading": "ACME SARL\nFacture n. 1042\n\nMontant : 19,99 €",
    "a heading, the currency first": "Invoice #A-1042\nTotal: $19.99",
    "a heading with n°": "Facture N° 2026-118\nÉmise le 03/06/2026",
}


@pytest.mark.parametrize("case", sorted(NOT_INVOICES))
def test_a_document_that_mentions_an_invoice_is_not_one(tmp_path, case):
    assert _category_of(tmp_path, NOT_INVOICES[case]) != "Invoices"


@pytest.mark.parametrize("case", sorted(INVOICES))
def test_an_invoice_titled_as_one_is_recognised_by_its_content(tmp_path, case):
    assert _category_of(tmp_path, INVOICES[case]) == "Invoices"


def test_statements_and_contracts_keep_their_own_category(tmp_path):
    assert _category_of(tmp_path, NOT_INVOICES["statement, invoice in line 3"]) == (
        "Bank-Statements"
    )
    assert _category_of(tmp_path, NOT_INVOICES["contract"]) == "Legal"


@pytest.mark.parametrize(
    "name",
    ["Receipt 2-3-45.pdf", "Invoice 06.12.34.56.78.pdf", "Setup 2024.3 invoice.pdf",
     "facture 2026-08-1234.pdf"],
)  # fmt: skip
def test_numbers_that_are_not_a_date_give_none(name):
    from cubby.domain.invoices import name_date

    assert name_date(name) is None


@pytest.mark.parametrize(
    ("name", "label"),
    [
        ("invoice_2026_07_15 ovh.pdf", "2026-07-15"),
        ("ovh invoice 20260715.pdf", "2026-07-15"),
        ("ovh facture 07-2026.pdf", "2026-07"),
        ("ovh facture 2026-07-01 and 2026-08-01.pdf", "2026-07-01"),  # the first one
    ],
)
def test_common_name_date_formats_are_read(name, label):
    from cubby.domain.invoices import name_date

    found = name_date(name)
    assert found is not None
    assert found.label() == label


def test_content_without_a_date_leaves_the_name_date(tmp_path):
    placement = _place("Invoice-2026-08-spotify.pdf", "Thank you for your order")

    assert placement.subdir == "2026-08"


def test_explain_reads_a_file_given_by_a_relative_path(tmp_path, monkeypatch, capsys):
    # Found by review: converters run from /, so a relative path found nothing.
    from cubby.adapters import extraction

    seen: list[str] = []
    monkeypatch.setattr(extraction, "_from_pdf", lambda path, *_: seen.append(str(path)) or "")
    (tmp_path / "a.pdf").write_bytes(b"%PDF-1.4")
    monkeypatch.chdir(tmp_path)
    from cubby.cli import main

    main(["explain", "--source", str(tmp_path), "a.pdf"])

    assert seen == [str(tmp_path / "a.pdf")]


def test_explain_says_nothing_about_content_it_never_tried_to_read(tmp_path, capsys):
    from cubby.cli import main

    (tmp_path / "blob.xyz").write_bytes(b"\x00\x01")

    main(["explain", "--source", str(tmp_path), "--json", str(tmp_path / "blob.xyz")])

    import json

    assert json.loads(capsys.readouterr().out)["items"][0]["content_chars"] is None


def test_explain_says_when_a_readable_type_gave_no_text(tmp_path, capsys):
    import json

    from cubby.cli import main

    (tmp_path / "empty.txt").write_text("", encoding="utf-8")

    main(["explain", "--source", str(tmp_path), str(tmp_path / "empty.txt")])
    text = capsys.readouterr().out
    main(["explain", "--source", str(tmp_path), "--json", str(tmp_path / "empty.txt")])

    assert "no text could be read from it" in text
    assert json.loads(capsys.readouterr().out)["items"][0]["content_chars"] == 0


def test_no_stopword_is_ever_a_vendor():
    from cubby.domain.invoices import _STOPWORDS

    named = [word for word in sorted(_STOPWORDS) if detect_vendor(f"{word} 3c0f.pdf", "", [])]

    assert named == []


@pytest.mark.parametrize("name", ["Facture-2045-07 ovh.pdf", "ovh invoice 2099-12.pdf"])
def test_a_name_date_after_the_download_is_not_the_invoice_date(name):
    # Re-review: an invoice cannot be dated after it arrived.
    placement = _place(name)

    assert placement.subdir == "2026-09"
    assert placement.new_name == "ovh facture.pdf"


def test_a_month_and_year_joined_by_hyphens_are_read():
    from cubby.domain.invoices import name_date

    found = name_date("Invoice-08-2026-spotify.pdf")

    assert found is not None
    assert found.label() == "2026-08"


# --- from the second re-review --------------------------------------------------


@pytest.mark.parametrize("blank", [" ", "\t", "\n", " \n"])
def test_a_text_opening_with_blanks_is_classified_in_linear_time(tmp_path, blank):
    # Re-review 2: the title patterns backtracked quadratically on a long run
    # of blanks (2 s for 4000 spaces, 20 s with a larger content_max_bytes).
    import time

    text = blank * (20_000 // len(blank)) + "hello"
    started = time.perf_counter()
    _category_of(tmp_path, text)

    assert time.perf_counter() - started < 0.5


@pytest.mark.parametrize(
    ("text", "category"),
    [
        ("Releve de compte\nFacture carte du 05/08/2026 CARREFOUR 42,10 EUR\nsolde",
         "Bank-Statements"),
        ("FACTURE CARTE 05/08/2026 42,10 EUR  Relevé de compte  solde 1 204,55 EUR",
         "Bank-Statements"),
        ("Invoice\nThis agreement between the parties, dated 01/01/2026, sets the terms.", "Legal"),
    ],
)  # fmt: skip
def test_another_category_named_by_the_content_comes_first(tmp_path, text, category):
    # Re-review 2: a title-like "facture" line took these from their category.
    assert _category_of(tmp_path, text) == category


@pytest.mark.parametrize("name", ["INV-2026-0815.pdf", "FA-102938.pdf", "FACT_2026_118.pdf"])
def test_an_invoice_number_prefix_is_not_a_vendor(name):
    assert detect_vendor(name, "", []) is None


def test_an_invoice_number_is_not_a_month():
    from cubby.domain.invoices import name_date

    assert name_date("INV-12-2025-0042.pdf") is None
