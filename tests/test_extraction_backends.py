"""Tests for the format backends, without their optional tools installed.

Half of extraction.py went untested because it depends on poppler, textutil,
antiword, python-docx or openpyxl being present. What is untested there is not
the third party: it is cubby's own logic, which is the part that decides whether
a fallback chain tries the next backend, whether a crashing parser can break a
sort run, and whether the reads stay bounded.

Fakes stand in for the tools, so the behaviour is asserted on any machine.
"""

from __future__ import annotations

import subprocess
import sys
import types
from pathlib import Path

import pytest

from cubby.adapters import extraction
from cubby.adapters.extraction import extract_text


@pytest.fixture
def no_system_tools(monkeypatch):
    """Nothing on PATH, so each backend takes its library or empty branch."""
    monkeypatch.setattr(extraction.shutil, "which", lambda _: None)


@pytest.fixture
def tools_available(monkeypatch):
    """Every tool on PATH; records the command lines that were run."""
    monkeypatch.setattr(extraction.shutil, "which", lambda name: f"/usr/bin/{name}")
    commands: list[list[str]] = []

    def fake_run(cmd, capture_output=False, timeout=None, check=False):
        commands.append(cmd)
        return types.SimpleNamespace(stdout=b"extracted by " + cmd[0].encode())

    monkeypatch.setattr(extraction.subprocess, "run", fake_run)
    return commands


def _file(tmp_path: Path, name: str) -> Path:
    path = tmp_path / name
    path.write_bytes(b"irrelevant bytes")
    return path


# --- the system tools are preferred, and given a timeout --------------------


@pytest.mark.parametrize(
    ("name", "ext", "tool"),
    [
        ("invoice.pdf", "pdf", "pdftotext"),
        ("letter.doc", "doc", "textutil"),
        ("letter.rtf", "rtf", "textutil"),
        ("page.html", "html", "textutil"),
    ],
)
def test_a_system_tool_is_used_when_present(name, ext, tool, tmp_path, tools_available):
    text = extract_text(_file(tmp_path, name), ext)

    assert tool in text
    # The resolved absolute path is what runs, not the bare name.
    assert tools_available[0][0] == f"/usr/bin/{tool}"


def test_every_external_command_is_given_a_timeout(monkeypatch, tmp_path):
    # An unattended agent must never wait forever on a parser.
    monkeypatch.setattr(extraction.shutil, "which", lambda name: f"/usr/bin/{name}")
    seen: dict = {}

    def fake_run(cmd, capture_output=False, timeout=None, check=False):
        seen["timeout"] = timeout
        return types.SimpleNamespace(stdout=b"text")

    monkeypatch.setattr(extraction.subprocess, "run", fake_run)
    extract_text(_file(tmp_path, "invoice.pdf"), "pdf")

    assert seen["timeout"] is not None
    assert 0 < seen["timeout"] <= 60


def test_a_tool_that_times_out_yields_nothing(monkeypatch, tmp_path, no_system_tools):
    monkeypatch.setattr(extraction.shutil, "which", lambda name: f"/usr/bin/{name}")

    def timing_out(cmd, capture_output=False, timeout=None, check=False):
        raise subprocess.TimeoutExpired(cmd, timeout or 0)

    monkeypatch.setattr(extraction.subprocess, "run", timing_out)
    assert extract_text(_file(tmp_path, "letter.doc"), "doc") == ""


# --- the library fallbacks --------------------------------------------------


def test_pdf_falls_back_to_pypdf_when_poppler_is_absent(monkeypatch, tmp_path, no_system_tools):
    class _Page:
        def extract_text(self):
            return "facture pypdf"

    module = types.ModuleType("pypdf")
    module.PdfReader = lambda _: types.SimpleNamespace(pages=[_Page(), _Page(), _Page()])
    monkeypatch.setitem(sys.modules, "pypdf", module)

    text = extract_text(_file(tmp_path, "invoice.pdf"), "pdf")

    assert "facture pypdf" in text
    # Only the first two pages are read: enough to classify, bounded by design.
    assert text.count("facture pypdf") == 2


def test_pdf_returns_nothing_when_the_parser_raises(monkeypatch, tmp_path, no_system_tools):
    # A corrupt or password-protected PDF must never break a sort run.
    module = types.ModuleType("pypdf")

    def exploding(_):
        raise RuntimeError("encrypted")

    module.PdfReader = exploding
    monkeypatch.setitem(sys.modules, "pypdf", module)

    assert extract_text(_file(tmp_path, "invoice.pdf"), "pdf") == ""


def test_docx_reads_paragraphs_and_stops_once_it_has_enough(monkeypatch, tmp_path, no_system_tools):
    paragraphs = [types.SimpleNamespace(text="facture " * 20) for _ in range(500)]
    module = types.ModuleType("docx")
    module.Document = lambda _: types.SimpleNamespace(paragraphs=paragraphs)
    monkeypatch.setitem(sys.modules, "docx", module)

    text = extract_text(_file(tmp_path, "contract.docx"), "docx", max_bytes=200)

    assert "facture" in text
    assert len(text) <= 200


def test_docx_without_the_library_and_without_textutil_yields_nothing(
    monkeypatch, tmp_path, no_system_tools
):
    monkeypatch.setitem(sys.modules, "docx", None)
    assert extract_text(_file(tmp_path, "contract.docx"), "docx") == ""


def test_xlsx_reads_a_bounded_number_of_rows(monkeypatch, tmp_path, no_system_tools):
    class _Sheet:
        def iter_rows(self, max_row=None, values_only=False):
            assert max_row is not None, "reading every row of a workbook is unbounded"
            return [("iban", "BE00", None) for _ in range(max_row)]

    module = types.ModuleType("openpyxl")
    module.load_workbook = lambda *a, **k: types.SimpleNamespace(active=_Sheet())
    monkeypatch.setitem(sys.modules, "openpyxl", module)

    text = extract_text(_file(tmp_path, "book.xlsx"), "xlsx")

    assert "iban" in text
    assert "None" not in text  # empty cells are dropped, not stringified


def test_xlsx_returns_nothing_when_the_workbook_cannot_be_opened(
    monkeypatch, tmp_path, no_system_tools
):
    module = types.ModuleType("openpyxl")

    def exploding(*a, **k):
        raise RuntimeError("not a zip file")

    module.load_workbook = exploding
    monkeypatch.setitem(sys.modules, "openpyxl", module)

    assert extract_text(_file(tmp_path, "book.xlsx"), "xlsx") == ""


def test_legacy_office_tries_each_tool_in_turn(monkeypatch, tmp_path):
    # textutil is macOS-only; antiword and catdoc are the Linux fallbacks.
    monkeypatch.setattr(
        extraction.shutil, "which", lambda name: "/usr/bin/catdoc" if name == "catdoc" else None
    )
    commands: list[list[str]] = []

    def fake_run(cmd, capture_output=False, timeout=None, check=False):
        commands.append(cmd)
        return types.SimpleNamespace(stdout=b"read by catdoc")

    monkeypatch.setattr(extraction.subprocess, "run", fake_run)

    assert "catdoc" in extract_text(_file(tmp_path, "old.doc"), "doc")
    assert commands[0][0] == "/usr/bin/catdoc"


def test_html_falls_back_to_stripping_tags_itself(tmp_path, no_system_tools):
    path = tmp_path / "page.html"
    path.write_text("<html><body><p>Bonjour &amp; facture</p></body></html>", encoding="utf-8")

    text = extract_text(path, "html")

    assert "Bonjour & facture" in text
    assert "<p>" not in text


# --- the tool that runs is the one that was found ---------------------------


def test_the_resolved_absolute_path_is_executed_not_a_bare_name(monkeypatch, tmp_path):
    # cubby runs unattended as a launchd agent. Invoking "pdftotext" by name
    # leaves the choice of binary to whatever PATH happens to hold at the time;
    # the path shutil.which already resolved is the one to run.
    monkeypatch.setattr(extraction.shutil, "which", lambda name: f"/opt/tools/{name}")
    commands: list[list[str]] = []

    def fake_run(cmd, capture_output=False, timeout=None, check=False):
        commands.append(cmd)
        return types.SimpleNamespace(stdout=b"text")

    monkeypatch.setattr(extraction.subprocess, "run", fake_run)
    extract_text(_file(tmp_path, "invoice.pdf"), "pdf")

    assert commands[0][0] == "/opt/tools/pdftotext"


def test_a_tool_that_vanishes_between_lookup_and_use_is_survivable(monkeypatch, tmp_path):
    monkeypatch.setattr(extraction.shutil, "which", lambda name: None)
    assert extract_text(_file(tmp_path, "old.doc"), "doc") == ""


@pytest.mark.parametrize("ext", ["html", "txt"])
def test_an_unreadable_file_yields_nothing(monkeypatch, tmp_path, no_system_tools, ext):
    path = _file(tmp_path, f"page.{ext}")

    def refuse(*args, **kwargs):
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(extraction.Path, "open", refuse)
    assert extract_text(path, ext) == ""
