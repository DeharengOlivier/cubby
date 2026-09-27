"""Best-effort text extraction for the engine's content stage.

Each backend is optional and degrades gracefully: when neither a system tool
nor a Python library can read a format, extraction returns ``""`` and the engine
simply falls back to filename / type rules. Nothing here raises to the caller,
so a corrupt or password-protected file never breaks a sort run.

Reading is bounded, twice. A file larger than :data:`MAX_SOURCE_BYTES` is not
opened at all: cubby only needs a few thousand characters to decide where a file
belongs, and a document worth classifying is never that large. Below that
ceiling, each backend reads a window rather than the whole file. Both matter
because cubby runs unattended: extracting 4000 bytes from a 315 MB page used to
cost 896 MB of memory and fifteen seconds.
"""

from __future__ import annotations

import html
import re
import shutil
import subprocess
from pathlib import Path

# Formats we know how to read as text. Anything else skips the content stage.
PARSABLE: frozenset[str] = frozenset(
    {
        "pdf",
        "docx",
        "doc",
        "rtf",
        "html",
        "htm",
        "txt",
        "md",
        "csv",
        "tsv",
        "log",
        "xlsx",
    }
)

_TAG_RE = re.compile(r"<[^>]+>")
_TIMEOUT = 15

#: Files larger than this are not read at all. Well past any invoice, contract
#: or statement; small enough that reading one cannot exhaust memory.
MAX_SOURCE_BYTES = 20_000_000

#: How much raw input may be read to yield ``max_bytes`` of text. Markup and
#: encoding overhead mean the raw window has to be larger than the answer.
_RAW_WINDOW_FACTOR = 64
_MAX_RAW_WINDOW = 4_000_000


def _raw_window(max_bytes: int) -> int:
    """Bytes of raw input worth reading to produce ``max_bytes`` of text."""
    return min(max(max_bytes, 1) * _RAW_WINDOW_FACTOR, _MAX_RAW_WINDOW)


def _is_too_large(path: Path) -> bool:
    """True if the file is past the ceiling, or cannot be measured."""
    try:
        return path.stat().st_size > MAX_SOURCE_BYTES
    except OSError:
        return True


def _run(cmd: list[str]) -> str:
    """Run ``cmd`` and return its stdout, or "" if anything goes wrong.

    The command is a list, never a shell string, and its first element is an
    absolute path resolved by :func:`_tool`, so neither the file name nor PATH
    can decide what gets executed.
    """
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=_TIMEOUT, check=False)
        return result.stdout.decode("utf-8", "ignore")
    except Exception:  # noqa: BLE001 - a converter failing in any way means "no text"
        return ""


def _tool(name: str) -> str | None:
    """The absolute path of ``name`` on PATH, or None.

    Callers run the resolved path rather than the bare name: cubby runs
    unattended, and a name leaves the choice of binary to whatever PATH holds at
    the moment the agent happens to fire.
    """
    return shutil.which(name)


def _from_pdf(path: Path) -> str:
    if pdftotext := _tool("pdftotext"):  # poppler, common on macOS and Linux
        text = _run([pdftotext, "-l", "2", "-q", str(path), "-"])
        if text.strip():
            return text
    try:
        from pypdf import PdfReader  # noqa: PLC0415 - optional backend

        reader = PdfReader(str(path))
        pages = reader.pages[:2]
        return "\n".join((page.extract_text() or "") for page in pages)
    except Exception:  # noqa: BLE001 - a corrupt document yields no text, never a crash
        return ""


def _from_docx(path: Path, max_bytes: int) -> str:
    try:
        import docx  # noqa: PLC0415 - optional backend

        collected: list[str] = []
        length = 0
        for paragraph in docx.Document(str(path)).paragraphs:
            collected.append(paragraph.text)
            length += len(paragraph.text) + 1
            if length >= max_bytes:  # enough to classify; stop walking the document
                break
        return "\n".join(collected)
    except Exception:  # noqa: BLE001 - see below
        # python-docx missing or the document unreadable by it: fall through to
        # the system converter, which is the whole point of a cascade.
        text = ""
    if textutil := _tool("textutil"):  # macOS native
        return _run([textutil, "-convert", "txt", "-stdout", str(path)])
    return text


def _from_legacy_office(path: Path) -> str:
    if textutil := _tool("textutil"):  # macOS reads .doc/.rtf natively
        return _run([textutil, "-convert", "txt", "-stdout", str(path)])
    for name in ("antiword", "catdoc"):  # common on Linux
        if tool := _tool(name):
            return _run([tool, str(path)])
    return ""


def _from_html(path: Path, max_bytes: int) -> str:
    if textutil := _tool("textutil"):
        text = _run([textutil, "-convert", "txt", "-stdout", str(path)])
        if text.strip():
            return text
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            raw = handle.read(_raw_window(max_bytes))
        return html.unescape(_TAG_RE.sub(" ", raw))
    except Exception:  # noqa: BLE001 - a corrupt document yields no text, never a crash
        return ""


def _from_text(path: Path, max_bytes: int) -> str:
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            return handle.read(max_bytes)
    except Exception:  # noqa: BLE001 - a corrupt document yields no text, never a crash
        return ""


def _from_xlsx(path: Path) -> str:
    try:
        import openpyxl  # noqa: PLC0415 - optional backend

        workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
        sheet = workbook.active
        cells: list[str] = []
        for row in sheet.iter_rows(max_row=20, values_only=True):
            cells += [str(c) for c in row if c is not None]
        return " ".join(cells)
    except Exception:  # noqa: BLE001 - a corrupt document yields no text, never a crash
        return ""


def extract_text(path: Path, ext: str, max_bytes: int = 4000) -> str:
    """Return up to ``max_bytes`` of extracted text, or ``""``.

    Returns the empty string when the format is unsupported, the file cannot be
    read, or it is larger than :data:`MAX_SOURCE_BYTES`. The engine then falls
    back to its filename and file-type stages, which is the designed behaviour
    for anything the content stage cannot speak for.
    """
    if not path.is_file() or _is_too_large(path):
        return ""
    ext = ext.lower()
    if ext == "pdf":
        text = _from_pdf(path)
    elif ext == "docx":
        text = _from_docx(path, max_bytes)
    elif ext in {"doc", "rtf"}:
        text = _from_legacy_office(path)
    elif ext in {"html", "htm"}:
        text = _from_html(path, max_bytes)
    elif ext in {"txt", "md", "csv", "tsv", "log"}:
        text = _from_text(path, max_bytes)
    elif ext == "xlsx":
        text = _from_xlsx(path)
    else:
        text = ""
    return text[:max_bytes]
