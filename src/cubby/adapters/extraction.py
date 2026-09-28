"""Best-effort text extraction for the engine's content stage.

Each backend is optional and degrades gracefully: when neither a system tool
nor a Python library can read a format, extraction gives no text and the engine
simply falls back to filename / type rules. Nothing here raises to the caller,
so a corrupt or password-protected file never breaks a sort run.

Degrading is not the same as hiding. A converter that was there and broke (it
timed out, was killed, exited non-zero, or could not be started) is returned
as a :class:`ConverterFailure` next to the text, for the caller to log and
count. A converter that is not installed is not a failure: ``cubby doctor``
reports it, and the agent does not repeat it on every pass.

Reading is bounded three ways. A file larger than :data:`MAX_SOURCE_BYTES` is
not opened at all: cubby only needs a few thousand characters to decide where a
file belongs, and a document worth classifying is never that large. Below that
ceiling, each backend reads a window rather than the whole file. And every
parser of a structured format (PDF, docx, xlsx) runs in a child process with a
timeout, a system tool or the Python libraries alike (see ``parsers.py``), so a
hostile file costs one timeout rather than the agent. All of it matters because
cubby runs unattended: extracting 4000 bytes from a 315 MB page used to cost
896 MB of memory and fifteen seconds.
"""

from __future__ import annotations

import html
import importlib.util
import re
import resource
import shutil
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

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

#: The optional library behind each child-process parser.
_LIBRARIES = {"pdf": "pypdf", "docx": "docx", "xlsx": "openpyxl"}

#: The formats that need a converter, and the tools or libraries (any one of
#: them) that read each, as the functions below try them. The other parsable
#: formats are read directly.
_READERS = {
    "pdf": ("pdftotext", "pypdf"),
    "docx": ("docx", "textutil"),
    "doc": ("textutil", "antiword", "catdoc"),
    "rtf": ("textutil", "antiword", "catdoc"),
    "xlsx": ("openpyxl",),
}
_TOOLS = ("pdftotext", "textutil", "antiword", "catdoc")
#: Every converter cubby can use: system tools first, then Python libraries.
CONVERTERS = (*_TOOLS, *_LIBRARIES.values())


def converters_present() -> dict[str, bool]:
    """Which of :data:`CONVERTERS` this machine has (``cubby doctor`` and ``status``)."""
    present = {name: bool(shutil.which(name)) for name in _TOOLS}
    for lib in _LIBRARIES.values():
        try:
            __import__(lib)
            present[lib] = True
        except (ImportError, OSError):  # absent, or a broken native wheel
            present[lib] = False
    return present


def formats_without_converter(present: dict[str, bool]) -> list[str]:
    """The formats none of the ``present`` converters reads: sorted by name and type only."""
    return sorted(ext for ext, readers in _READERS.items() if not any(present[r] for r in readers))


#: Address-space ceiling for every converter child, where the platform enforces it.
_CHILD_MEMORY_BYTES = 1_000_000_000

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


#: Where converters run: never the Downloads folder, whose files are untrusted.
_NEUTRAL_CWD = "/"


def _limit_child_memory() -> None:
    """Cap a converter's address space where the platform enforces it (Linux)."""
    try:
        resource.setrlimit(resource.RLIMIT_AS, (_CHILD_MEMORY_BYTES, _CHILD_MEMORY_BYTES))
    except (ValueError, OSError):
        return  # macOS does not enforce RLIMIT_AS; the timeout still bounds the child


#: How a converter broke: it ran past its timeout, exited with a non-zero
#: status, was killed by a signal (a crash, or the kernel's out-of-memory
#: killer), or could not be run or read at all.
FailureKind = Literal["timeout", "exit", "crash", "error"]

#: The longest detail kept from what a converter said: one line, not its dump.
_MAX_DETAIL = 200


@dataclass(frozen=True)
class ConverterFailure:
    """A converter that was there and did not do its job."""

    converter: str  # "pdftotext", "pdf parser", ...
    kind: FailureKind
    detail: str

    def describe(self) -> str:
        """``converter: kind: detail``, the kind as a word a log filter can match."""
        return f"{self.converter}: {self.kind}: {self.detail}"


@dataclass(frozen=True)
class Extraction:
    """The text read from a file, and the converters that broke on the way."""

    text: str
    failures: tuple[ConverterFailure, ...] = ()


def _summary(raw: str, file: Path | None = None) -> str:
    """The line of ``raw`` that says why, printable and bounded.

    That is its first non-blank line, except in a Python traceback, whose
    first line says nothing and whose last names the exception. ``file`` is
    written as its name alone: the log line already says which file it is, and
    an absolute path would use up the detail.
    """
    lines = [part.strip() for part in raw.splitlines() if part.strip()]
    if not lines:
        return ""
    line = lines[-1] if lines[0].startswith("Traceback (most recent call last)") else lines[0]
    if file is not None:
        line = line.replace(str(file), file.name)
    return "".join(c if c.isprintable() else "?" for c in line)[:_MAX_DETAIL]


def _signal_name(number: int) -> str:
    try:
        return signal.Signals(number).name
    except ValueError:
        return f"signal {number}"


def _run(
    cmd: list[str],
    failures: list[ConverterFailure],
    name: str | None = None,
    *,
    file: Path | None = None,
) -> str:
    """Run ``cmd`` and return its stdout; a converter that broke is added to ``failures``.

    The command is a list, never a shell string, and its first element is an
    absolute path resolved by :func:`_tool` (or this interpreter), so neither
    the file name nor PATH can decide what gets executed. It runs from the
    filesystem root with a timeout and a memory ceiling, so a hostile document
    costs one timeout and cannot plant code in the working directory. ``name``
    is how a failure names the converter (the executable's name by default),
    and ``file`` the file it read, shortened to its name in what a failure says.
    """
    converter = name or Path(cmd[0]).name
    try:
        # preexec_fn is safe here: cubby starts no threads.
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=_TIMEOUT,
            check=False,
            cwd=_NEUTRAL_CWD,
            preexec_fn=_limit_child_memory,
        )
    except subprocess.TimeoutExpired:
        failures.append(ConverterFailure(converter, "timeout", f"no answer after {_TIMEOUT} s"))
        return ""
    except (OSError, subprocess.SubprocessError) as exc:
        # Found on PATH a moment ago, and yet it could not be run.
        detail = _summary(f"{type(exc).__name__}: {exc}", file)
        failures.append(ConverterFailure(converter, "error", detail))
        return ""
    if result.returncode < 0:
        detail = f"killed by {_signal_name(-result.returncode)}"
        failures.append(ConverterFailure(converter, "crash", detail))
    elif result.returncode > 0:
        said = _summary(result.stderr.decode("utf-8", "replace"), file)
        detail = f"status {result.returncode}" + (f", {said}" if said else "")
        failures.append(ConverterFailure(converter, "exit", detail))
    # Whatever a converter printed before failing is still text to classify on.
    return result.stdout.decode("utf-8", "ignore")


def _in_child(kind: str, path: Path, max_bytes: int, failures: list[ConverterFailure]) -> str:
    """Run the ``kind`` Python parser in a child process (see ``parsers.py``).

    The interpreter is the one running cubby, by absolute path. When the
    library is not installed, no process is started at all, and that is not a
    failure: the parser is optional.
    """
    if importlib.util.find_spec(_LIBRARIES[kind]) is None:
        return ""
    # -P: do not put the working directory on sys.path. Run from the Downloads
    # folder, `python -m` would otherwise import a downloaded docx.py or pypdf.py
    # in place of the real library.
    return _run(
        [sys.executable, "-P", "-m", "cubby.adapters.parsers", kind, str(path), str(max_bytes)],
        failures,
        f"{kind} parser",
        file=path,
    )


def _tool(name: str) -> str | None:
    """The absolute path of ``name`` on PATH, or None.

    Callers run the resolved path rather than the bare name: cubby runs
    unattended, and a name leaves the choice of binary to whatever PATH holds at
    the moment the agent happens to fire.
    """
    return shutil.which(name)


def _from_pdf(path: Path, max_bytes: int, failures: list[ConverterFailure]) -> str:
    if pdftotext := _tool("pdftotext"):  # poppler, common on macOS and Linux
        # -q stays although it leaves a failure without its reason: without it,
        # poppler prints one line per bad byte, and a 5 MB crafted PDF made it
        # write 300 MB to stderr, which capture_output would hold in memory.
        text = _run([pdftotext, "-l", "2", "-q", str(path), "-"], failures, file=path)
        if text.strip():
            return text
    return _in_child("pdf", path, max_bytes, failures)


def _from_docx(path: Path, max_bytes: int, failures: list[ConverterFailure]) -> str:
    text = _in_child("docx", path, max_bytes, failures)
    if text.strip():
        return text
    # python-docx missing or the document unreadable by it: fall through to
    # the system converter, which is the whole point of a cascade.
    if textutil := _tool("textutil"):  # macOS native
        return _run([textutil, "-convert", "txt", "-stdout", str(path)], failures, file=path)
    return text


def _from_legacy_office(path: Path, failures: list[ConverterFailure]) -> str:
    if textutil := _tool("textutil"):  # macOS reads .doc/.rtf natively
        return _run([textutil, "-convert", "txt", "-stdout", str(path)], failures, file=path)
    for name in ("antiword", "catdoc"):  # common on Linux
        if tool := _tool(name):
            return _run([tool, str(path)], failures, file=path)
    return ""


def _read_failed(exc: Exception, path: Path) -> ConverterFailure:
    return ConverterFailure("direct read", "error", _summary(f"{type(exc).__name__}: {exc}", path))


def _from_html(path: Path, max_bytes: int, failures: list[ConverterFailure]) -> str:
    if textutil := _tool("textutil"):
        text = _run([textutil, "-convert", "txt", "-stdout", str(path)], failures, file=path)
        if text.strip():
            return text
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            raw = handle.read(_raw_window(max_bytes))
        return html.unescape(_TAG_RE.sub(" ", raw))
    except Exception as exc:  # noqa: BLE001 - reported, and the file still sorts by name and type
        failures.append(_read_failed(exc, path))
        return ""


def _from_text(path: Path, max_bytes: int, failures: list[ConverterFailure]) -> str:
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            return handle.read(max_bytes)
    except Exception as exc:  # noqa: BLE001 - reported, and the file still sorts by name and type
        failures.append(_read_failed(exc, path))
        return ""


#: The extensions :func:`extract_text` can read; any other gives "".
READABLE = frozenset(
    {"pdf", "docx", "doc", "rtf", "html", "htm", "txt", "md", "csv", "tsv", "log", "xlsx"}
)


def can_read(ext: str) -> bool:
    """Whether cubby tries to read the content of a file with extension ``ext``."""
    return ext.lower() in READABLE


def extract(path: Path, ext: str, max_bytes: int = 4000) -> Extraction:
    """Up to ``max_bytes`` of extracted text, and the converters that broke.

    The text is empty when the format is unsupported, no converter is
    installed, every converter failed, or the file is larger than
    :data:`MAX_SOURCE_BYTES`. The engine then falls back to its filename and
    file-type stages, which is the designed behaviour for anything the content
    stage cannot speak for.

    ``failures`` is empty unless the text is: a file is reported only when its
    content was lost. A converter that is missing, or a large file left
    unread, is never a failure.
    """
    # Converters run from the filesystem root: a relative path would name
    # another file there, or none.
    path = path.absolute()
    if not path.is_file() or _is_too_large(path):
        return Extraction("")
    failures: list[ConverterFailure] = []
    ext = ext.lower()
    if ext == "pdf":
        text = _from_pdf(path, max_bytes, failures)
    elif ext == "docx":
        text = _from_docx(path, max_bytes, failures)
    elif ext in {"doc", "rtf"}:
        text = _from_legacy_office(path, failures)
    elif ext in {"html", "htm"}:
        text = _from_html(path, max_bytes, failures)
    elif ext in {"txt", "md", "csv", "tsv", "log"}:
        text = _from_text(path, max_bytes, failures)
    elif ext == "xlsx":
        text = _in_child("xlsx", path, max_bytes, failures)
    else:
        text = ""
    text = text[:max_bytes]
    # The rule lives here, where "usable text" is decided, and not with the
    # callers: a converter that broke but was rescued (pypdf after pdftotext,
    # textutil after the docx parser, the direct html read after textutil, or
    # text printed before a non-zero exit) lost nothing. The file is sorted by
    # its content, so reporting it would say something false.
    if text.strip():
        return Extraction(text)
    return Extraction(text, tuple(failures))


def extract_text(path: Path, ext: str, max_bytes: int = 4000) -> str:
    """The text alone of :func:`extract`, for callers with no one to report failures to."""
    return extract(path, ext, max_bytes).text
