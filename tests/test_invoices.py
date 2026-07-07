"""Unit tests for the invoice placement domain: date parsing (FR/EN/ISO),
vendor detection, folder and file naming, and the safety fallbacks."""

from datetime import date

import pytest

from cubby.domain.invoices import (
    detect_vendor,
    invoice_filename,
    month_folder,
    parse_invoice_date,
    plan_placement,
)

FALLBACK = date(2000, 1, 1)


# --- Date parsing ---------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Invoice date: 2026-07-07", date(2026, 7, 7)),
        ("Facture du 07/07/2026", date(2026, 7, 7)),
        ("Émise le 07.07.2026", date(2026, 7, 7)),
        ("Date de facture 7 juillet 2026", date(2026, 7, 7)),
        ("Facture émise le 1er août 2026", date(2026, 8, 1)),
        ("Invoice date: July 7, 2026", date(2026, 7, 7)),
        ("Issued 7 July 2026", date(2026, 7, 7)),
        ("Facture du 3 décembre 2025", date(2025, 12, 3)),
        ("Dated Dec 31, 2026", date(2026, 12, 31)),
    ],
)
def test_parse_dates_fr_and_en(text, expected):
    assert parse_invoice_date(text, FALLBACK) == expected


def test_us_month_day_order_detected_when_unambiguous():
    # 13 cannot be a month, so 12/13/2026 must be M/D/Y.
    assert parse_invoice_date("Date 12/13/2026", FALLBACK) == date(2026, 12, 13)


def test_ambiguous_numeric_date_defaults_to_day_first():
    # 07/08 is ambiguous; the European reading (day first) wins.
    assert parse_invoice_date("Le 07/08/2026", FALLBACK) == date(2026, 8, 7)


def test_labelled_date_wins_over_earlier_unlabelled_date():
    text = "Reçu le 01/01/2020 pour service. Date de facture: 07/07/2026."
    assert parse_invoice_date(text, FALLBACK) == date(2026, 7, 7)


def test_no_date_returns_fallback():
    assert parse_invoice_date("no date here", FALLBACK) == FALLBACK
    assert parse_invoice_date("", FALLBACK) == FALLBACK


def test_implausible_year_is_ignored():
    assert parse_invoice_date("version 1999-01-01 build", FALLBACK) == FALLBACK


# --- Vendor detection -----------------------------------------------------


def test_known_vendor_matched_in_cryptic_name_via_text():
    vendor = detect_vendor("3c0fe1a2.pdf", "Merci pour votre abonnement Spotify", ["spotify"])
    assert vendor == "spotify"


def test_vendor_from_filename_heuristic():
    assert detect_vendor("Spotify-Invoice-3c0fe.pdf", "", []) == "spotify"
    assert detect_vendor("Facture_OVH_2026.pdf", "", []) == "ovh"
    assert detect_vendor("invoice-netflix-juillet.pdf", "", []) == "netflix"


def test_unknown_vendor_returns_none():
    assert detect_vendor("3c0fe1a2b3c4.pdf", "random text", []) is None
    assert detect_vendor("Invoice-2026-001.pdf", "", []) is None


# --- Folder and file naming -----------------------------------------------


def test_month_folder_styles():
    d = date(2026, 7, 7)
    assert month_folder(d, "numeric") == "2026-07"
    assert month_folder(d, "letters", "fr") == "juillet 2026"
    assert month_folder(d, "letters", "en") == "July 2026"
    assert month_folder(date(2026, 1, 5), "numeric") == "2026-01"


def test_invoice_filename():
    assert invoice_filename("spotify", date(2026, 7, 7), "pdf") == "spotify facture 2026-07-07.pdf"
    assert invoice_filename("ovh", date(2026, 6, 30), ".PDF") == "ovh facture 2026-06-30.pdf"


# --- Orchestration --------------------------------------------------------


def test_plan_placement_renames_known_invoice():
    placement = plan_placement(
        name="3c0fe.pdf",
        ext="pdf",
        text="Facture du 07/07/2026. Abonnement Spotify Premium.",
        fallback_date=FALLBACK,
        vendor_rename=True,
        vendors=["spotify"],
    )
    assert placement.subdir == "2026-07"
    assert placement.new_name == "spotify facture 2026-07-07.pdf"


def test_plan_placement_keeps_name_when_vendor_unknown():
    placement = plan_placement(
        name="3c0fe1a2b3c4.pdf",  # cryptic stem, no vendor anywhere
        ext="pdf",
        text="Facture du 07/07/2026",
        fallback_date=FALLBACK,
        vendor_rename=True,
        vendors=[],
    )
    assert placement.subdir == "2026-07"
    assert placement.new_name is None


def test_plan_placement_no_rename_for_statements():
    placement = plan_placement(
        name="releve-2026.pdf",
        ext="pdf",
        text="Relevé de compte 07/07/2026",
        fallback_date=FALLBACK,
        vendor_rename=False,
        vendors=["spotify"],
    )
    assert placement.subdir == "2026-07"
    assert placement.new_name is None


def test_plan_placement_letters_style():
    placement = plan_placement(
        name="Facture_OVH.pdf",
        ext="pdf",
        text="Facture du 7 juillet 2026",
        fallback_date=FALLBACK,
        vendor_rename=True,
        month_style="letters",
        month_lang="fr",
        vendors=[],
    )
    assert placement.subdir == "juillet 2026"
    assert placement.new_name == "ovh facture 2026-07-07.pdf"
