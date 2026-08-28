"""Regression battery: reading a file's content is bounded.

The defect, measured: extracting text from a 315 MB HTML file to decide where it
belongs asked for 4000 bytes, and cost 896 MB of resident memory and 15 seconds.
`_from_text` already read only what it needed; every other backend read the whole
file, or every paragraph, and truncated afterwards. cubby runs unattended as a
background agent, where that is a hang and an out-of-memory away from taking the
machine down with it.

Nothing here is about correctness of the extracted text; it is about the cost of
getting it.
"""

from __future__ import annotations

import resource
import sys
from pathlib import Path

import pytest

from cubby.adapters.extraction import MAX_SOURCE_BYTES, extract_text


def peak_rss_mb() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024


def _file_of(tmp_path: Path, name: str, size_bytes: int, unit: str = "x") -> Path:
    path = tmp_path / name
    with path.open("w", encoding="utf-8") as handle:
        written = 0
        block = unit * 65536
        while written < size_bytes:
            handle.write(block)
            written += len(block)
    return path


# --- a file too large to be worth reading is not read -----------------------


@pytest.mark.parametrize("ext", ["html", "txt", "md", "csv", "log"])
def test_a_file_past_the_ceiling_is_skipped(ext, tmp_path):
    path = _file_of(tmp_path, f"huge.{ext}", MAX_SOURCE_BYTES + 65536)
    assert extract_text(path, ext, max_bytes=4000) == ""


def test_a_file_just_under_the_ceiling_is_still_read(tmp_path):
    path = tmp_path / "ordinary.txt"
    path.write_text("facture " * 100, encoding="utf-8")
    assert "facture" in extract_text(path, "txt", max_bytes=4000)


def test_the_ceiling_is_a_documented_number_not_a_surprise():
    # Big enough for any document worth classifying, small enough that reading
    # one cannot take the machine down.
    assert 1_000_000 <= MAX_SOURCE_BYTES <= 64_000_000


# --- what is read stays proportionate to what was asked for -----------------


def test_extracting_from_a_large_html_file_stays_cheap(tmp_path):
    # 40 MB of markup, under the ceiling so it is genuinely parsed.
    path = tmp_path / "page.html"
    with path.open("w", encoding="utf-8") as handle:
        for _ in range(40):
            handle.write("<p>" + "facture " * 131_000 + "</p>\n")

    before = peak_rss_mb()
    text = extract_text(path, "html", max_bytes=4000)
    grew_mb = peak_rss_mb() - before

    assert len(text) <= 4000
    assert grew_mb < 200, f"reading 4000 bytes should not cost {grew_mb:.0f} MB"


def test_never_returns_more_than_asked_for(tmp_path):
    path = _file_of(tmp_path, "long.txt", 5_000_000, unit="a")
    assert len(extract_text(path, "txt", max_bytes=500)) == 500


def test_a_missing_file_still_returns_nothing(tmp_path):
    assert extract_text(tmp_path / "gone.txt", "txt") == ""


def test_an_unsupported_extension_still_returns_nothing(tmp_path):
    path = tmp_path / "archive.zip"
    path.write_bytes(b"PK\x03\x04")
    assert extract_text(path, "zip") == ""
