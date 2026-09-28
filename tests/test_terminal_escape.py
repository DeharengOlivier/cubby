"""File names never reach the terminal, or a notification, raw.

Anyone can drop a file into Downloads, so a file name is untrusted text. A
name holding ``ESC [2J`` would clear the screen of the person running
``cubby plan`` and could forge the rest of its output; a bidi override would
make ``evil.exe`` read as another name. Every human rendering shows such a
character as a visible escape (``\\x1b``, ``\\u202e``) instead, while plain
names, accented or not, read exactly as they are. JSON outputs are escaped by
the JSON encoder already and are not touched.

These tests were written against the defect (audit 3, SEC-08-004) and failed
before the fix.
"""

from __future__ import annotations

import re
import subprocess
import unicodedata
from datetime import datetime
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from cubby.adapters import notify as notify_module
from cubby.adapters.ledger import Failure, Ledger, RunRecord
from cubby.adapters.ui import escape_for_terminal
from cubby.app.report import SortOutcome, render_plan
from cubby.cli import EXIT_OK, main
from cubby.cli.common import make_loud
from tests.helpers import aged_file

#: A name that tries every kind of character a terminal acts on: C0 controls
#: (ESC sequences, BEL, BS, TAB, CR, LF), DEL, C1 controls (NEL, the 8-bit CSI)
#: and the bidi overrides and isolates.
HOSTILE = (
    "evil\x1b[2J\x1b[31mFAKE\x07\x08\x7f\x85\x9b"
    "\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069\t\r\n.pdf"
)

#: How that name must read: each such character as a visible escape.
SHOWN = (
    "evil\\x1b[2J\\x1b[31mFAKE\\x07\\x08\\x7f\\x85\\x9b"
    "\\u202a\\u202b\\u202c\\u202d\\u202e\\u2066\\u2067\\u2068\\u2069\\t\\r\\n.pdf"
)

_BIDI = {chr(c) for c in (*range(0x202A, 0x202F), *range(0x2066, 0x206A))}


def raw_controls(text: str) -> list[str]:
    """The characters of ``text`` a terminal would act on (a line break aside)."""
    return [
        ch
        for ch in text
        if (ord(ch) < 0x20 and ch != "\n") or 0x7F <= ord(ch) <= 0x9F or ch in _BIDI
    ]


def assert_safe(text: str) -> None:
    assert raw_controls(text) == []
    assert SHOWN in text


@pytest.fixture
def hostile(tmp_path: Path) -> Path:
    source = tmp_path / "Downloads"
    aged_file(source, HOSTILE)
    return source


def _flags(source: Path) -> list[str]:
    return ["--source", str(source), "--delay", "0", "--no-content"]


# --- every command that names a file --------------------------------------------


def test_plan_escapes_a_hostile_name(hostile, capsys):
    assert main(["plan", *_flags(hostile)]) == EXIT_OK
    assert_safe(capsys.readouterr().out)


def test_run_escapes_a_hostile_name_and_its_log_echo(hostile, capsys):
    assert main(["run", "-v", *_flags(hostile)]) == EXIT_OK
    out = capsys.readouterr().out
    assert_safe(out)
    # The verbose echo of the log line names the file too.
    assert any(SHOWN in line for line in out.splitlines() if "INFO" in line)


def test_undo_escapes_a_hostile_name(hostile, capsys):
    assert main(["run", *_flags(hostile)]) == EXIT_OK
    capsys.readouterr()
    assert main(["undo"]) == EXIT_OK
    captured = capsys.readouterr()
    assert_safe(captured.out)
    assert raw_controls(captured.err) == []
    assert (hostile / HOSTILE).exists()


def test_undo_escapes_a_hostile_name_it_cannot_find(hostile, capsys):
    assert main(["run", *_flags(hostile)]) == EXIT_OK
    for filed in (hostile / "Documents").rglob("*.pdf"):
        filed.unlink()
    capsys.readouterr()
    main(["undo"])
    captured = capsys.readouterr()
    assert_safe(captured.out)
    assert raw_controls(captured.err) == []


def test_explain_escapes_a_hostile_name(hostile, capsys):
    assert main(["explain", *_flags(hostile), str(hostile / HOSTILE)]) == EXIT_OK
    assert_safe(capsys.readouterr().out)


def test_explain_escapes_a_missing_hostile_name(hostile, capsys):
    main(["explain", *_flags(hostile), str(hostile / ("gone-" + HOSTILE))])
    err = capsys.readouterr().err
    assert raw_controls(err) == []
    assert "gone-" + SHOWN in err


def test_log_and_status_escape_a_hostile_name(hostile, capsys):
    assert main(["run", *_flags(hostile)]) == EXIT_OK
    capsys.readouterr()
    assert main(["log"]) == EXIT_OK
    assert_safe(capsys.readouterr().out)
    main(["status"])
    assert_safe(capsys.readouterr().out)


def test_log_escapes_a_line_that_is_not_json(capsys):
    from cubby.adapters import state

    # The log is read line by line: a line ending cannot be inside one.
    one_line = HOSTILE.replace("\r\n", "")
    state.append_line(state.log_path(), "a traceback about " + one_line)
    assert main(["log"]) == EXIT_OK
    out = capsys.readouterr().out
    assert raw_controls(out) == []
    assert SHOWN.replace("\\r\\n", "") in out


@pytest.mark.parametrize("value", [0, False, ""])
def test_a_log_line_keeps_a_falsy_message(value):
    from cubby.adapters.logging import human_line

    line = human_line({"ts": "2026-09-28T12:00:00", "level": "INFO", "msg": value})
    assert line == f"2026-09-28T12:00:00  INFO    {value}"


def test_status_escapes_hostile_failures(capsys):
    now = datetime.now().isoformat(timespec="seconds")
    Ledger().record(
        RunRecord(
            run="r1",
            mode="watch",
            source="/d",
            started=now,
            finished=now,
            moved=0,
            failed=1,
            failures=(Failure(HOSTILE, f"PermissionError: denied: {HOSTILE}"),),
        )
    )
    main(["status"])
    assert_safe(capsys.readouterr().out)


def test_doctor_and_a_missing_source_escape_a_hostile_folder(tmp_path, capsys):
    folder = tmp_path / HOSTILE
    folder.mkdir()
    main(["doctor", "--source", str(folder)])
    assert_safe(capsys.readouterr().out)
    main(["run", "--source", str(tmp_path / ("gone-" + HOSTILE))])
    err = capsys.readouterr().err
    assert raw_controls(err) == []
    assert "gone-" + SHOWN in err


def test_warnings_on_stderr_escape_a_hostile_name(capsys):
    make_loud(lambda message, *, level="INFO": None)(f"could not sort {HOSTILE}", level="WARNING")
    err = capsys.readouterr().err
    assert raw_controls(err) == []
    assert SHOWN in err


def test_the_plan_escapes_failures_duplicates_and_files_left_alone(tmp_path):
    path = tmp_path / HOSTILE
    outcomes = [
        SortOutcome.failed(path, f"PermissionError: {HOSTILE}"),
        SortOutcome(
            source=tmp_path / ("dup-" + HOSTILE),
            category="Documents",
            stage=None,
            duplicate_of=tmp_path / "Documents" / HOSTILE,
        ),
        SortOutcome(
            source=tmp_path / "plain.pdf",
            category="Documents",
            stage=None,
            renamed_to="renamed-" + HOSTILE,
            journaled=False,
        ),
    ]
    text = render_plan(
        outcomes,
        applied=True,
        left_alone=[(HOSTILE, f"still downloading: {HOSTILE}")],
        blocked=[HOSTILE],
    )
    assert raw_controls(text) == []
    assert text.count(SHOWN) >= 7


def test_plain_names_are_shown_as_they_are(tmp_path, capsys):
    names = ["Notes été 2026.pdf", "日本語の書類.pdf", "photo 🎉.pdf", "a (1) - b_c.pdf"]
    for name in names:
        aged_file(tmp_path, name)
    assert main(["plan", *_flags(tmp_path)]) == EXIT_OK
    out = capsys.readouterr().out
    for name in names:
        assert f"    {name}   <- type\n" in out


def test_json_outputs_are_unchanged(hostile, capsys):
    import json

    assert main(["plan", "--json", *_flags(hostile)]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["items"][0]["name"] == HOSTILE


# --- notifications ----------------------------------------------------------------


@pytest.fixture
def tools(monkeypatch):
    monkeypatch.setattr(notify_module.shutil, "which", lambda name: f"/usr/bin/{name}")


def test_notify_send_body_is_not_markup(tools):
    cmd = notify_module.command(f"Could not sort <b>{HOSTILE}</b> & more", platform="linux")
    assert cmd is not None
    body = cmd[-1]
    assert body == f"Could not sort &lt;b&gt;{SHOWN}&lt;/b&gt; &amp; more"
    assert raw_controls(body) == []


def test_osascript_text_is_escaped_but_not_as_markup(tools):
    cmd = notify_module.command(f"Could not sort <b>{HOSTILE}</b> & more", platform="darwin")
    assert cmd is not None
    assert cmd[-1] == f"Could not sort <b>{SHOWN}</b> & more"


def test_a_shortened_body_never_cuts_an_entity(tools):
    cmd = notify_module.command("&" * 1000, platform="linux")
    assert cmd is not None
    assert cmd[-1] == "&amp;" * (notify_module.MAX_CHARS - 1) + "…"


def test_the_notifier_sends_the_escaped_text(tools, monkeypatch):
    sent: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        sent.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(notify_module.subprocess, "run", fake_run)
    monkeypatch.setattr(notify_module.sys, "platform", "linux")
    notify_module.notifier(True)(f"Could not sort {HOSTILE}. See 'cubby status'.")
    assert sent[0][-1] == f"Could not sort {SHOWN}. See 'cubby status'."


# --- the escaping itself ----------------------------------------------------------

_ESCAPE = re.compile(r"\\(\\|t|n|r|x[0-9a-f]{2}|u[0-9a-f]{4}|U[0-9a-f]{8})")
_SHORT = {"\\": "\\", "t": "\t", "n": "\n", "r": "\r"}


def _unescape(text: str) -> str:
    """The reverse of the escaping, to show that it loses nothing."""

    def one(match: re.Match[str]) -> str:
        code = match.group(1)
        return _SHORT.get(code) or chr(int(code[1:], 16))

    return _ESCAPE.sub(one, text)


def _is_control(ch: str) -> bool:
    """A character with no glyph of its own: a control, a format character, a
    separator other than the space, a surrogate, private or unassigned."""
    if ch in "\u200c\u200d":  # the joiners: kept, see escape_for_terminal
        return False
    return not ch.isprintable() or unicodedata.category(ch) in {"Cc", "Cf", "Zl", "Zp"}


@given(st.text(st.characters(codec=None)))
def test_escaped_text_holds_no_control_character_and_loses_nothing(text):
    shown = escape_for_terminal(text)
    assert not any(_is_control(ch) for ch in shown)
    assert _unescape(shown) == text


@given(st.text(st.characters().filter(lambda ch: ch.isprintable() and ch != "\\")))
def test_plain_printable_text_is_left_as_it_is(text):
    assert escape_for_terminal(text) == text


# A family emoji (joined by U+200D) and a Persian word (with U+200C) keep their joiners.
@pytest.mark.parametrize(
    "name",
    [
        "été.pdf",
        "日本.pdf",
        "🎉.pdf",
        "👨\u200d👩\u200d👧.pdf",
        "\u0645\u06cc\u200c\u062e\u0648\u0627\u0647\u0645",
    ],
)
def test_ordinary_names_are_unchanged(name):
    assert escape_for_terminal(name) == name
