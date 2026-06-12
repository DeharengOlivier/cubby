"""Extraction over real (tiny) files. PDF/office backends are environment
dependent, so we assert on formats we can always read and only check that the
others never raise."""

from cubby.adapters.extraction import PARSABLE, extract_text


def test_extract_plain_text(tmp_path):
    f = tmp_path / "note.txt"
    f.write_text("invoice total due 42")
    assert "invoice" in extract_text(f, "txt").lower()


def test_extract_csv(tmp_path):
    f = tmp_path / "data.csv"
    f.write_text("iban,amount\nBE00,100\n")
    assert "iban" in extract_text(f, "csv").lower()


def test_extract_html_strips_tags(tmp_path):
    f = tmp_path / "page.html"
    f.write_text("<html><body><p>Hello &amp; bonjour</p></body></html>")
    text = extract_text(f, "html")
    assert "Hello" in text and "bonjour" in text
    assert "<p>" not in text


def test_extract_unsupported_returns_empty(tmp_path):
    f = tmp_path / "thing.bin"
    f.write_bytes(b"\x00\x01\x02")
    assert extract_text(f, "bin") == ""


def test_extract_missing_file_is_safe(tmp_path):
    assert extract_text(tmp_path / "nope.pdf", "pdf") == ""


def test_extract_respects_max_bytes(tmp_path):
    f = tmp_path / "big.txt"
    f.write_text("x" * 10000)
    assert len(extract_text(f, "txt", max_bytes=100)) == 100


def test_parsable_set_is_frozen():
    assert "pdf" in PARSABLE and isinstance(PARSABLE, frozenset)
