"""Where an invoice goes and what it is called, as pure functions.

The engine decides *which category* a file belongs to. This module decides,
for the finance categories, the *month/year subfolder* it should live in and,
for invoices, a *clean filename* built from the vendor and the invoice date.

Everything here is IO-free and works on plain strings, so date parsing, vendor
detection and name building are all unit-testable in isolation. Invoices arrive
in French and English, so both languages are handled throughout.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import PurePath

# --- Month names ----------------------------------------------------------

_FR_MONTHS: dict[int, str] = {
    1: "janvier",
    2: "février",
    3: "mars",
    4: "avril",
    5: "mai",
    6: "juin",
    7: "juillet",
    8: "août",
    9: "septembre",
    10: "octobre",
    11: "novembre",
    12: "décembre",
}

_EN_MONTHS: dict[int, str] = {
    1: "January",
    2: "February",
    3: "March",
    4: "April",
    5: "May",
    6: "June",
    7: "July",
    8: "August",
    9: "September",
    10: "October",
    11: "November",
    12: "December",
}


def _build_month_index() -> dict[str, int]:
    """Map every month spelling we accept (FR/EN, full/abbrev/unaccented) to its
    number, so a date written in either language parses."""
    index: dict[str, int] = {}
    fr_abbr = {
        1: ["janv"],
        2: ["févr", "fevr"],
        3: ["mars"],
        4: ["avr"],
        5: ["mai"],
        6: ["juin"],
        7: ["juil", "juill"],
        8: ["août", "aout"],
        9: ["sept", "sep"],
        10: ["oct"],
        11: ["nov"],
        12: ["déc", "dec"],
    }
    en_abbr = {
        1: ["jan"],
        2: ["feb"],
        3: ["mar"],
        4: ["apr"],
        5: ["may"],
        6: ["jun"],
        7: ["jul"],
        8: ["aug"],
        9: ["sep", "sept"],
        10: ["oct"],
        11: ["nov"],
        12: ["dec"],
    }

    def add(name: str, num: int) -> None:
        index[name.lower()] = num
        index[_strip_accents(name.lower())] = num

    for num, name in _FR_MONTHS.items():
        add(name, num)
    for num, name in _EN_MONTHS.items():
        add(name, num)
    for table in (fr_abbr, en_abbr):
        for num, names in table.items():
            for name in names:
                add(name, num)
    return index


def _strip_accents(text: str) -> str:
    table = str.maketrans("àâäéèêëîïôöùûüç", "aaaeeeeiioouuuc")
    return text.translate(table)


_MONTH_INDEX = _build_month_index()

# --- Date parsing ---------------------------------------------------------

_MONTHS_ALT = "|".join(sorted(map(re.escape, _MONTH_INDEX), key=len, reverse=True))

# 2026-07-07 (ISO, unambiguous, tried first).
_ISO = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
# 07/07/2026, 07.07.2026, 07-07-2026 (day/month/year, European default).
_NUMERIC = re.compile(r"\b(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})\b")
# 7 juillet 2026, 1er août 2026, 7 July 2026.
_DAY_MONTH = re.compile(
    rf"\b(\d{{1,2}})(?:er|st|nd|rd|th)?\s+({_MONTHS_ALT})\.?\s+(\d{{4}})\b",
    re.IGNORECASE,
)
# July 7, 2026 / July 7 2026.
_MONTH_DAY = re.compile(
    rf"\b({_MONTHS_ALT})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b",
    re.IGNORECASE,
)
# Words that tend to precede the invoice date; a date near one is preferred.
_LABEL = re.compile(
    r"(date d['’]?[ée]mission|facture du|invoice date|date de facture|"  # noqa: RUF001 - typographic apostrophe used in French documents
    r"\bdated\b|\bissued\b|\b[ée]mis\b|\bdate\b)",
    re.IGNORECASE,
)


def _valid(year: int, month: int, day: int) -> date | None:
    if not (2000 <= year <= 2100):
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _month_num(word: str) -> int | None:
    return _MONTH_INDEX.get(word.lower()) or _MONTH_INDEX.get(_strip_accents(word.lower()))


def _day_month(a: int, b: int) -> tuple[int, int]:
    """Order the two numeric fields of a date as ``(day, month)``.

    A field above 12 can only be a day. When both fit either role, France writes
    the day first, so that is the reading kept.
    """
    if b > 12 and a <= 12:  # second field can only be a day (US M/D/Y)
        return b, a
    return a, b


def _find_dates(text: str) -> list[tuple[int, date]]:
    """Every plausible date in ``text`` as ``(start_offset, date)`` pairs."""
    found: list[tuple[int, date]] = []

    for m in _ISO.finditer(text):
        d = _valid(int(m[1]), int(m[2]), int(m[3]))
        if d:
            found.append((m.start(), d))

    for m in _NUMERIC.finditer(text):
        year = int(m[3])
        year += 2000 if year < 100 else 0
        day, month = _day_month(int(m[1]), int(m[2]))
        d = _valid(year, month, day)
        if d:
            found.append((m.start(), d))

    for m in _DAY_MONTH.finditer(text):
        num = _month_num(m[2])
        if num:
            d = _valid(int(m[3]), num, int(m[1]))
            if d:
                found.append((m.start(), d))

    for m in _MONTH_DAY.finditer(text):
        num = _month_num(m[1])
        if num:
            d = _valid(int(m[3]), num, int(m[2]))
            if d:
                found.append((m.start(), d))

    return found


def parse_invoice_date(text: str, fallback: date) -> date:
    """The invoice's issue date, read from ``text``; ``fallback`` if unreadable.

    When several dates appear, one sitting just after a label (``Date``,
    ``Facture du``, ``Invoice date`` ...) wins; otherwise the earliest date in
    the document is used.
    """
    if not text:
        return fallback
    candidates = _find_dates(text)
    if not candidates:
        return fallback

    label_ends = [m.end() for m in _LABEL.finditer(text)]

    def labelled(pos: int) -> bool:
        return any(0 <= pos - end <= 40 for end in label_ends)

    preferred = [pair for pair in candidates if labelled(pair[0])]
    pool = preferred or candidates
    pool.sort(key=lambda pair: pair[0])
    return pool[0][1]


# --- Vendor detection -----------------------------------------------------

_SEP = re.compile(r"[\s_\-.]+")
# Noise tokens that are never a vendor name (FR + EN).
_STOPWORDS = (
    frozenset(
        {
            "invoice",
            "facture",
            "factures",
            "receipt",
            "recu",
            "reçu",
            "order",
            "commande",
            "payment",
            "paiement",
            "confirmation",
            "bill",
            "devis",
            "no",
            "num",
            "ref",
            "reference",
            "référence",
            "de",
            "du",
            "the",
            "pour",
            "for",
        }
    )
    | {name.lower() for name in _FR_MONTHS.values()}
    | {name.lower() for name in _EN_MONTHS.values()}
)


def detect_vendor(name: str, text: str, known: Sequence[str]) -> str | None:
    """A lowercase vendor slug for ``name``/``text``, or ``None`` when unsure.

    Known vendors (from config) are matched first, so ``Spotify`` is recognised
    even inside a cryptic filename. Otherwise the filename stem is mined: noise
    words, numbers and ids are dropped and the first real word is taken. When
    nothing convincing remains we return ``None`` and the caller keeps the
    original filename rather than guessing.
    """
    haystack = f"{name}\n{text}"
    for vendor in known:
        if re.search(re.escape(vendor), haystack, re.IGNORECASE):
            return vendor.lower()

    stem = name.rsplit(".", 1)[0] if "." in name else name
    for token in _SEP.split(stem):
        low = token.lower()
        if low in _STOPWORDS:
            continue
        if any(ch.isdigit() for ch in token):
            continue
        if len(low) < 2 or not low.isalpha():
            continue
        return low
    return None


# --- Folder and file naming -----------------------------------------------


def month_folder(d: date, style: str = "numeric", lang: str = "fr") -> str:
    """The subfolder name for ``d``: ``2026-07`` (numeric) or ``juillet 2026`` /
    ``July 2026`` (letters)."""
    if style == "letters":
        months = _EN_MONTHS if lang == "en" else _FR_MONTHS
        return f"{months[d.month]} {d.year}"
    return f"{d.year:04d}-{d.month:02d}"


def invoice_filename(vendor: str, d: date, ext: str) -> str:
    """``spotify facture 2026-07-07.pdf`` from its parts."""
    ext = ext.lstrip(".").lower()
    base = f"{vendor} facture {d.isoformat()}"
    return f"{base}.{ext}" if ext else base


# --- Orchestration --------------------------------------------------------


@dataclass(frozen=True)
class Placement:
    """Where a finance file is filed, relative to its category folder.

    ``subdir`` is the month/year folder (possibly empty). ``new_name`` is the
    renamed filename, or ``None`` to keep the original name.
    """

    subdir: str
    new_name: str | None = None


def plan_placement(
    *,
    name: str,
    text: str,
    fallback_date: date,
    vendor_rename: bool,
    month_style: str = "numeric",
    month_lang: str = "fr",
    vendors: Sequence[str] = (),
) -> Placement:
    """Decide the month subfolder and (for invoices) the renamed filename.

    ``text`` is the extracted document text (empty when content scanning is
    off). ``fallback_date`` is used when no date can be read (typically the
    file's modification time). Renaming only happens when ``vendor_rename`` is
    set *and* a vendor is confidently found.
    """
    d = parse_invoice_date(text, fallback_date)
    subdir = month_folder(d, month_style, month_lang)
    new_name: str | None = None
    if vendor_rename:
        vendor = detect_vendor(name, text, vendors)
        if vendor:
            ext = PurePath(name).suffix
            new_name = invoice_filename(vendor, d, ext)
    return Placement(subdir=subdir, new_name=new_name)
