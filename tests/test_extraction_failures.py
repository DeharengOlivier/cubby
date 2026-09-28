"""A converter that breaks is reported, not swallowed.

The defect (audit 3, REL-07 and OBS-01): a converter that timed out, crashed,
ran out of memory or exited non-zero gave ``""`` with no log line, exactly like
a document with no text in it. The file then fell through to the filename and
type stages, which is the designed fallback, but nobody could tell that
extraction had broken: a poppler upgrade that segfaults on every PDF would have
gone unnoticed for months.

What is asserted here: the fallback is unchanged (same destination, no crash),
and on top of it each failure is a WARNING log line carrying the run id, the
kind of failure and the file, and a per-run count in the ledger that ``cubby
status`` shows. A converter that is simply not installed is not a failure:
``cubby doctor`` reports it once, the agent does not repeat it every pass.
"""

from __future__ import annotations

import json
import signal
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from cubby.adapters import extraction, parsers, state
from cubby.adapters.extraction import ConverterFailure, extract
from cubby.adapters.filesystem import build_ref
from cubby.adapters.ledger import Ledger, RunRecord
from cubby.app.activity import summarize
from cubby.app.sorter import Sorter
from cubby.cli import agent as cli_agent
from cubby.cli import main
from cubby.domain.category import Category
from tests.helpers import aged_file, config_for

PDFTOTEXT = "/usr/bin/pdftotext"


def _pdf(folder: Path, name: str = "scan.pdf") -> Path:
    return aged_file(folder, name, "%PDF-1.4 not really")


@pytest.fixture
def only_pdftotext(monkeypatch):
    """pdftotext on PATH, no Python PDF library: one converter to break."""
    monkeypatch.setattr(
        extraction.shutil, "which", lambda name: PDFTOTEXT if name == "pdftotext" else None
    )
    monkeypatch.setattr(extraction.importlib.util, "find_spec", lambda name: None)


def _pdftotext_does(monkeypatch, behaviour):
    """Replace what running pdftotext does; any other command runs for real."""
    real_run = subprocess.run

    def fake_run(cmd, *args, **kwargs):
        if cmd and cmd[0] == PDFTOTEXT:
            return behaviour(cmd)
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(extraction.subprocess, "run", fake_run)


def _times_out(cmd):
    raise subprocess.TimeoutExpired(cmd, extraction._TIMEOUT)


def _exits(status: int, stderr: bytes = b""):
    return lambda cmd: subprocess.CompletedProcess(cmd, status, b"", stderr)


def _answers(stdout: bytes):
    return lambda cmd: subprocess.CompletedProcess(cmd, 0, stdout, b"")


# --- the adapter says what broke ------------------------------------------------


def test_a_converter_that_times_out_is_a_timeout_failure(monkeypatch, tmp_path, only_pdftotext):
    _pdftotext_does(monkeypatch, _times_out)

    result = extract(_pdf(tmp_path), "pdf")

    assert result.text == ""
    (failure,) = result.failures
    assert (failure.converter, failure.kind) == ("pdftotext", "timeout")
    assert f"{extraction._TIMEOUT} s" in failure.detail


def test_a_converter_that_exits_non_zero_is_an_exit_failure(monkeypatch, tmp_path, only_pdftotext):
    _pdftotext_does(monkeypatch, _exits(1, b"Syntax Error: Couldn't read xref table\n"))

    (failure,) = extract(_pdf(tmp_path), "pdf").failures

    assert (failure.converter, failure.kind) == ("pdftotext", "exit")
    assert "status 1" in failure.detail
    assert "Couldn't read xref table" in failure.detail  # what the tool said, first line


def test_a_converter_killed_by_a_signal_is_a_crash(monkeypatch, tmp_path, only_pdftotext):
    _pdftotext_does(monkeypatch, _exits(-signal.SIGSEGV))

    (failure,) = extract(_pdf(tmp_path), "pdf").failures

    assert (failure.converter, failure.kind) == ("pdftotext", "crash")
    assert "SIGSEGV" in failure.detail


def test_a_signal_with_no_name_is_still_a_crash(monkeypatch, tmp_path, only_pdftotext):
    _pdftotext_does(monkeypatch, _exits(-200))

    (failure,) = extract(_pdf(tmp_path), "pdf").failures

    assert (failure.kind, failure.detail) == ("crash", "killed by signal 200")


def test_every_converter_that_broke_in_a_cascade_is_reported(monkeypatch, tmp_path):
    # python-docx raises in its child, then textutil exits non-zero: both said.
    monkeypatch.setattr(extraction.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(extraction.importlib.util, "find_spec", lambda name: object())

    def both_fail(cmd, *args, **kwargs):
        if cmd[0] == "/usr/bin/textutil":
            return subprocess.CompletedProcess(cmd, 2, b"", b"textutil: cannot convert\n")
        return subprocess.CompletedProcess(cmd, parsers.EXIT_PARSE_FAILED, b"", b"KeyError: x\n")

    monkeypatch.setattr(extraction.subprocess, "run", both_fail)

    result = extract(aged_file(tmp_path, "letter.docx"), "docx")

    assert result.text == ""
    assert [f.describe() for f in result.failures] == [
        "docx parser: exit: status 3, KeyError: x",
        "textutil: exit: status 2, textutil: cannot convert",
    ]


def test_a_converter_that_cannot_be_started_is_an_error(monkeypatch, tmp_path, only_pdftotext):
    def refused(cmd):
        raise PermissionError(13, "Permission denied", cmd[0])

    _pdftotext_does(monkeypatch, refused)

    (failure,) = extract(_pdf(tmp_path), "pdf").failures

    assert (failure.converter, failure.kind) == ("pdftotext", "error")
    assert "PermissionError" in failure.detail


def test_what_a_converter_says_on_stderr_is_bounded_and_printable(
    monkeypatch, tmp_path, only_pdftotext
):
    _pdftotext_does(monkeypatch, _exits(1, b"\x1b[31m" + b"x" * 5000 + b"\nsecond line\n"))

    (failure,) = extract(_pdf(tmp_path), "pdf").failures

    assert len(failure.detail) <= 300
    assert "\x1b" not in failure.detail
    assert "second line" not in failure.detail


def test_a_scanned_pdf_with_no_text_is_not_a_failure(monkeypatch, tmp_path, only_pdftotext):
    _pdftotext_does(monkeypatch, _answers(b""))

    assert extract(_pdf(tmp_path), "pdf").failures == ()


def test_the_text_of_a_converter_that_worked_is_kept(monkeypatch, tmp_path, only_pdftotext):
    _pdftotext_does(monkeypatch, _answers(b"Facture 2026"))

    result = extract(_pdf(tmp_path), "pdf")

    assert result.text == "Facture 2026"
    assert result.failures == ()


def test_a_converter_that_is_not_installed_is_not_a_failure(monkeypatch, tmp_path):
    # `cubby doctor` reports a missing tool; the agent must not log it every pass.
    monkeypatch.setattr(extraction.shutil, "which", lambda name: None)
    monkeypatch.setattr(extraction.importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(extraction.subprocess, "run", lambda *a, **k: pytest.fail("spawned"))

    for name, ext in (("a.pdf", "pdf"), ("b.docx", "docx"), ("c.doc", "doc"), ("d.xlsx", "xlsx")):
        assert extract(aged_file(tmp_path, name), ext).failures == (), name


@pytest.mark.parametrize("ext", ["html", "txt"])
def test_a_file_that_cannot_be_read_directly_is_an_error(monkeypatch, tmp_path, ext):
    monkeypatch.setattr(extraction.shutil, "which", lambda name: None)
    path = aged_file(tmp_path, f"page.{ext}")

    def refuse(*args, **kwargs):
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(extraction.Path, "open", refuse)

    result = extract(path, ext)

    assert result.text == ""
    (failure,) = result.failures
    assert failure.kind == "error"
    assert "PermissionError" in failure.detail


def test_a_failure_reads_as_converter_kind_and_detail():
    failure = ConverterFailure("pdftotext", "timeout", "no answer after 15 s")

    assert failure.describe() == "pdftotext: timeout: no answer after 15 s"


def test_extract_text_still_gives_the_text_alone(monkeypatch, tmp_path, only_pdftotext):
    _pdftotext_does(monkeypatch, _times_out)

    assert extraction.extract_text(_pdf(tmp_path), "pdf") == ""


# --- the parser child reports its failures ----------------------------------------


def test_the_parser_child_exits_non_zero_when_its_parser_raises(monkeypatch, tmp_path, capsys):
    def exploding(path, max_chars):
        raise MemoryError

    monkeypatch.setitem(parsers.PARSERS, "pdf", exploding)

    status = parsers.main(["pdf", str(tmp_path / "a.pdf"), "100"])

    assert status == parsers.EXIT_PARSE_FAILED != 0
    assert "MemoryError" in capsys.readouterr().err


def test_a_real_parser_child_failing_on_a_corrupt_workbook_is_reported(monkeypatch, tmp_path):
    pytest.importorskip("openpyxl")
    monkeypatch.setattr(extraction.shutil, "which", lambda name: None)
    path = aged_file(tmp_path, "book.xlsx", "not a zip file at all")

    result = extract(path, "xlsx")

    assert result.text == ""
    (failure,) = result.failures
    assert (failure.converter, failure.kind) == ("xlsx parser", "exit")
    assert f"status {parsers.EXIT_PARSE_FAILED}" in failure.detail
    assert "BadZipFile" in failure.detail  # the exception the child reported


def test_a_real_parser_child_that_hangs_is_a_timeout(monkeypatch):
    monkeypatch.setattr(extraction, "_TIMEOUT", 0.5)
    failures: list[ConverterFailure] = []

    text = extraction._run([sys.executable, "-c", "import time; time.sleep(30)"], failures, "slow")

    assert text == ""
    assert [(f.converter, f.kind) for f in failures] == [("slow", "timeout")]


def test_a_real_child_killed_by_a_signal_is_a_crash():
    failures: list[ConverterFailure] = []
    script = "import os, signal; os.kill(os.getpid(), signal.SIGKILL)"

    extraction._run([sys.executable, "-c", script], failures, "doomed")

    assert [(f.converter, f.kind) for f in failures] == [("doomed", "crash")]
    assert "SIGKILL" in failures[0].detail


# --- the port reports each file once ---------------------------------------------


def test_build_ref_reports_a_failed_extraction_once(monkeypatch, tmp_path, only_pdftotext):
    _pdftotext_does(monkeypatch, _times_out)
    seen: list[tuple[Path, tuple[ConverterFailure, ...]]] = []

    ref = build_ref(_pdf(tmp_path), on_extraction_failure=lambda p, f: seen.append((p, f)))
    ref.text()
    ref.text()  # memoised: no second extraction, no second report

    ((path, failures),) = seen
    assert path.name == "scan.pdf"
    assert failures[0].kind == "timeout"


# --- the sort falls back as before, and says so ----------------------------------


def _content_config(source: Path):
    categories = (
        Category(name="Invoices", content_patterns=("numero de facture",)),
        Category(name="Documents", extensions=frozenset({"pdf"})),
    )
    return config_for(source, *categories, content_scan=True)


@pytest.mark.parametrize("breakage", [_times_out, _exits(1), _exits(-signal.SIGKILL)])
def test_a_broken_converter_changes_no_destination(monkeypatch, tmp_path, only_pdftotext, breakage):
    broken, clean = tmp_path / "broken", tmp_path / "clean"
    _pdf(broken)
    _pdf(clean)

    _pdftotext_does(monkeypatch, _answers(b""))
    (expected,) = Sorter(_content_config(clean)).sort_once(apply=True)
    _pdftotext_does(monkeypatch, breakage)
    (outcome,) = Sorter(_content_config(broken)).sort_once(apply=True)

    assert outcome.error is None
    assert outcome.moved_to is not None
    assert expected.moved_to is not None
    assert outcome.moved_to.relative_to(broken) == expected.moved_to.relative_to(clean)


def test_the_sorter_warns_and_counts_extraction_failures(monkeypatch, tmp_path, only_pdftotext):
    _pdftotext_does(monkeypatch, _times_out)
    source = tmp_path / "Downloads"
    _pdf(source, "a.pdf")
    _pdf(source, "b.pdf")
    aged_file(source, "notes.txt", "plain")
    warnings: list[str] = []
    ledger = Ledger(tmp_path / "state")

    Sorter(_content_config(source), warn=warnings.append, ledger=ledger).sort_once(apply=True)

    (record,) = ledger.runs()
    assert record.extraction_failures == 2
    assert record.failed == 0  # an unreadable content is not a failed move
    assert record.status == "ok"
    assert sorted(w for w in warnings if "content extraction failed" in w) == [
        f"content extraction failed for {name}: pdftotext: timeout: no answer after "
        f"{extraction._TIMEOUT} s"
        for name in ("a.pdf", "b.pdf")
    ]


def test_a_missing_converter_warns_nothing_and_counts_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(extraction.shutil, "which", lambda name: None)
    monkeypatch.setattr(extraction.importlib.util, "find_spec", lambda name: None)
    source = tmp_path / "Downloads"
    _pdf(source)
    warnings: list[str] = []
    ledger = Ledger(tmp_path / "state")

    Sorter(_content_config(source), warn=warnings.append, ledger=ledger).sort_once(apply=True)

    assert warnings == []
    assert ledger.runs()[0].extraction_failures == 0


# --- end to end: the log line, the ledger and `cubby status` ----------------------


def _log_records() -> list[dict]:
    return [json.loads(line) for line in state.log_path().read_text("utf-8").splitlines()]


def test_a_run_logs_a_warning_with_its_run_id_kind_and_file(monkeypatch, tmp_path, only_pdftotext):
    _pdftotext_does(monkeypatch, _exits(-signal.SIGSEGV))
    source = tmp_path / "Downloads"
    _pdf(source, 'odd "name".pdf')

    main(["run", "--source", str(source), "--delay", "0"])

    (record,) = Ledger().runs()
    assert record.extraction_failures == 1
    warnings = [r for r in _log_records() if r["level"] == "WARNING"]
    (line,) = warnings
    assert line["run"] == record.run
    assert 'odd "name".pdf' in line["msg"]
    assert "pdftotext: crash: killed by SIGSEGV" in line["msg"]


def test_a_run_with_a_missing_converter_logs_no_warning(monkeypatch, tmp_path):
    monkeypatch.setattr(extraction.shutil, "which", lambda name: None)
    monkeypatch.setattr(extraction.importlib.util, "find_spec", lambda name: None)
    source = tmp_path / "Downloads"
    _pdf(source)

    main(["run", "--source", str(source), "--delay", "0"])

    assert [r for r in _log_records() if r["level"] != "INFO"] == []
    assert Ledger().runs()[0].extraction_failures == 0


def test_status_shows_the_day_s_extraction_failures(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli_agent, "detect_service", lambda: None)
    now = datetime.now()
    for run, count in (("r1", 2), ("r2", 1)):
        Ledger().record(
            RunRecord(
                run=run,
                mode="watch",
                source="/d",
                started=now.isoformat(timespec="seconds"),
                finished=(now - timedelta(minutes=1)).isoformat(timespec="seconds"),
                moved=3,
                failed=0,
                extraction_failures=count,
            )
        )

    main(["status", "--json"])
    payload = json.loads(capsys.readouterr().out)
    main(["status"])
    text = capsys.readouterr().out

    assert payload["activity"]["extraction_failures"] == 3
    assert payload["last_run"]["extraction_failures"] == 1
    assert "content unreadable for 3 (see cubby log --warnings)" in text


def test_status_says_nothing_of_extraction_when_none_failed(monkeypatch, capsys):
    monkeypatch.setattr(cli_agent, "detect_service", lambda: None)
    now = datetime.now().isoformat(timespec="seconds")
    Ledger().record(RunRecord("r1", "watch", "/d", now, now, moved=1, failed=0))

    main(["status"])

    assert "unreadable" not in capsys.readouterr().out


# --- the ledger ---------------------------------------------------------------------


def test_the_count_survives_the_ledger_round_trip(tmp_path):
    ledger = Ledger(tmp_path)
    ledger.record(RunRecord("r", "run", "/d", "a", "b", moved=1, failed=0, extraction_failures=4))

    assert ledger.runs()[0].extraction_failures == 4


def test_a_record_from_before_the_count_reads_as_zero():
    old = {
        "run": "r",
        "mode": "run",
        "source": "/d",
        "started": "a",
        "finished": "b",
        "moved": 1,
        "failed": 0,
    }

    assert RunRecord.from_json(old).extraction_failures == 0


def test_the_day_sums_extraction_failures_apart_from_failed_moves():
    now = datetime(2026, 9, 28, 12)
    stamp = (now - timedelta(hours=1)).isoformat(timespec="seconds")
    records = [
        RunRecord("r1", "watch", "/d", stamp, stamp, moved=2, failed=0, extraction_failures=2),
        RunRecord("r2", "watch", "/d", stamp, stamp, moved=1, failed=0, extraction_failures=3),
    ]

    day = summarize(records, since=now - timedelta(hours=24), now=now)

    assert (day.failed, day.extraction_failures) == (0, 5)
    assert day.errors == ()


def test_a_damaged_count_is_refused_like_any_damaged_record(tmp_path):
    ledger = Ledger(tmp_path)
    good = RunRecord("r", "run", "/d", "a", "b", moved=1, failed=0).to_json()
    ledger.runs_path.parent.mkdir(parents=True, exist_ok=True)
    ledger.runs_path.write_text(
        json.dumps({**good, "run": "bad", "extraction_failures": "many"}) + "\n" + json.dumps(good),
        encoding="utf-8",
    )

    assert [r.run for r in ledger.runs()] == ["r"]


# --- round 2: a file whose text a later backend rescued is not a failure ---------


def _rescue_pdf_by_pypdf(monkeypatch):
    """pdftotext exits 1, then the pdf parser child reads the text."""
    monkeypatch.setattr(
        extraction.shutil, "which", lambda name: PDFTOTEXT if name == "pdftotext" else None
    )
    monkeypatch.setattr(extraction.importlib.util, "find_spec", lambda name: object())

    def run(cmd, *args, **kwargs):
        if cmd[0] == PDFTOTEXT:
            return subprocess.CompletedProcess(cmd, 1, b"", b"")
        return subprocess.CompletedProcess(cmd, 0, b"numero de facture 42", b"")

    monkeypatch.setattr(extraction.subprocess, "run", run)
    return "scan.pdf"


def _rescue_docx_by_textutil(monkeypatch):
    """The docx parser child exits 3, then textutil reads the text."""
    monkeypatch.setattr(extraction.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(extraction.importlib.util, "find_spec", lambda name: object())

    def run(cmd, *args, **kwargs):
        if cmd[0] == "/usr/bin/textutil":
            return subprocess.CompletedProcess(cmd, 0, b"numero de facture 42", b"")
        return subprocess.CompletedProcess(cmd, parsers.EXIT_PARSE_FAILED, b"", b"KeyError: x\n")

    monkeypatch.setattr(extraction.subprocess, "run", run)
    return "letter.docx"


def _rescue_html_by_direct_read(monkeypatch):
    """textutil exits 1, then cubby strips the tags itself."""
    monkeypatch.setattr(extraction.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        extraction.subprocess,
        "run",
        lambda cmd, *a, **k: subprocess.CompletedProcess(cmd, 1, b"", b""),
    )
    return "page.html"


def _text_despite_a_non_zero_exit(monkeypatch):
    """pdftotext prints the text, then exits 1 (a warning it treats as an error)."""
    monkeypatch.setattr(
        extraction.shutil, "which", lambda name: PDFTOTEXT if name == "pdftotext" else None
    )
    monkeypatch.setattr(extraction.importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(
        extraction.subprocess,
        "run",
        lambda cmd, *a, **k: subprocess.CompletedProcess(cmd, 1, b"numero de facture 42", b""),
    )
    return "scan.pdf"


RESCUES = [
    _rescue_pdf_by_pypdf,
    _rescue_docx_by_textutil,
    _rescue_html_by_direct_read,
    _text_despite_a_non_zero_exit,
]


@pytest.mark.parametrize("rescue", RESCUES)
def test_a_rescued_file_keeps_its_text_and_reports_no_failure(monkeypatch, tmp_path, rescue):
    name = rescue(monkeypatch)
    path = aged_file(tmp_path, name, "<p>numero de facture 42</p>")

    result = extract(path, name.rpartition(".")[2])

    assert "numero de facture 42" in result.text
    assert result.failures == ()


@pytest.mark.parametrize("rescue", RESCUES)
def test_a_rescued_file_is_sorted_by_content_with_no_warning_and_no_count(
    monkeypatch, tmp_path, rescue
):
    name = rescue(monkeypatch)
    source = tmp_path / "Downloads"
    aged_file(source, name, "<p>numero de facture 42</p>")
    categories = (
        Category(name="Invoices", content_patterns=("numero de facture",)),
        Category(name="Documents", extensions=frozenset({"pdf", "docx", "html"})),
    )
    warnings: list[str] = []
    ledger = Ledger(tmp_path / "state")

    (outcome,) = Sorter(
        config_for(source, *categories, content_scan=True), warn=warnings.append, ledger=ledger
    ).sort_once(apply=True)

    assert outcome.category == "Invoices"  # read by its content
    assert warnings == []
    assert ledger.runs()[0].extraction_failures == 0


def test_whitespace_is_not_a_rescue(monkeypatch, tmp_path, only_pdftotext):
    _pdftotext_does(monkeypatch, lambda cmd: subprocess.CompletedProcess(cmd, 1, b" \n\f\n", b""))

    (failure,) = extract(_pdf(tmp_path), "pdf").failures

    assert failure.kind == "exit"


# --- round 2: what a failure says ------------------------------------------------


def test_the_first_non_blank_line_of_stderr_is_the_reason(monkeypatch, tmp_path, only_pdftotext):
    _pdftotext_does(monkeypatch, _exits(1, b"\n   \nreason one\nreason two\n"))

    (failure,) = extract(_pdf(tmp_path), "pdf").failures

    assert failure.detail == "status 1, reason one"


def test_a_traceback_is_summed_up_by_its_last_line(monkeypatch, tmp_path, only_pdftotext):
    traceback = (
        b"Traceback (most recent call last):\n"
        b'  File "<frozen runpy>", line 198, in _run_module_as_main\n'
        b"MemoryError\n"
    )
    _pdftotext_does(monkeypatch, _exits(1, traceback))

    (failure,) = extract(_pdf(tmp_path), "pdf").failures

    assert failure.detail == "status 1, MemoryError"


def test_the_detail_names_the_file_not_its_absolute_path(monkeypatch, tmp_path, only_pdftotext):
    path = _pdf(tmp_path / "deep" / "folder")
    said = f"PackageNotFoundError: Package not found at '{path}'".encode()
    _pdftotext_does(monkeypatch, _exits(1, said))

    (failure,) = extract(path, "pdf").failures

    assert failure.detail == "status 1, PackageNotFoundError: Package not found at 'scan.pdf'"
    assert str(tmp_path) not in failure.detail


def test_a_direct_read_error_names_the_file_not_its_absolute_path(monkeypatch, tmp_path):
    monkeypatch.setattr(extraction.shutil, "which", lambda name: None)
    path = aged_file(tmp_path / "deep", "notes.txt")

    def refuse(*args, **kwargs):
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(extraction.Path, "open", refuse)

    (failure,) = extract(path, "txt").failures

    assert str(tmp_path) not in failure.detail
    assert "notes.txt" in failure.detail


# --- round 2: the survivors of the review's mutation run -----------------------


def test_a_warning_that_cannot_reach_stderr_does_not_fail_the_file(
    monkeypatch, tmp_path, only_pdftotext
):
    _pdftotext_does(monkeypatch, _times_out)
    source = tmp_path / "Downloads"
    _pdf(source)
    ledger = Ledger(tmp_path / "state")

    def closed_stderr(message: str) -> None:
        raise BrokenPipeError(32, "Broken pipe")

    (outcome,) = Sorter(_content_config(source), warn=closed_stderr, ledger=ledger).sort_once(
        apply=True
    )

    assert outcome.error is None
    assert outcome.moved_to is not None
    (record,) = ledger.runs()
    assert (record.failed, record.extraction_failures) == (0, 1)


def test_status_shows_a_single_extraction_failure(monkeypatch, capsys):
    monkeypatch.setattr(cli_agent, "detect_service", lambda: None)
    now = datetime.now().isoformat(timespec="seconds")
    Ledger().record(
        RunRecord("r1", "watch", "/d", now, now, moved=1, failed=0, extraction_failures=1)
    )

    main(["status"])

    assert "content unreadable for 1 (see cubby log --warnings)" in capsys.readouterr().out


# --- round 2: the ledger refuses a count cubby never writes ---------------------


@pytest.mark.parametrize("field", ["moved", "failed", "extraction_failures"])
@pytest.mark.parametrize("value", [-2, True, 2.9, "3", None])
def test_a_count_that_is_not_a_non_negative_integer_makes_the_line_damaged(tmp_path, field, value):
    ledger = Ledger(tmp_path)
    good = RunRecord("r", "run", "/d", "a", "b", moved=1, failed=0, extraction_failures=1).to_json()
    ledger.runs_path.parent.mkdir(parents=True, exist_ok=True)
    ledger.runs_path.write_text(
        json.dumps({**good, "run": "bad", field: value}) + "\n" + json.dumps(good) + "\n",
        encoding="utf-8",
    )

    assert [r.run for r in ledger.runs()] == ["r"]
