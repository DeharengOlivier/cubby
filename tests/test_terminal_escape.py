"""File names never reach the terminal, or a notification, raw.

Anyone can drop a file into Downloads, so a file name is untrusted text. A
name holding ``ESC [2J`` would clear the screen of the person running
``cubby plan`` and could forge the rest of its output; a bidi override would
make ``evil.exe`` read as another name. Every human rendering shows such a
character as a visible escape (``\\x1b``, ``\\u202e``) instead, while plain
names, accented or not, read exactly as they are. The ``--json`` outputs are
valid JSON that decodes to the same values, with the same characters written
as ``\\uXXXX`` escapes instead of raw.

These tests were written against the defect (audit 3, SEC-08-004) and failed
before the fix.
"""

from __future__ import annotations

import json
import re
import subprocess
import unicodedata
from datetime import datetime
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from cubby.adapters import notify as notify_module
from cubby.adapters import state
from cubby.adapters.ledger import Failure, Ledger, RunRecord
from cubby.adapters.service import ServiceError
from cubby.adapters.ui import Palette, dumps_for_terminal, escape_for_terminal
from cubby.app.explain import Explanation
from cubby.app.report import SortOutcome, render_plan
from cubby.cli import EXIT_BAD_CONFIG, EXIT_FAILED, EXIT_OK, main
from cubby.cli import agent as cli_agent
from cubby.cli import inspect as cli_inspect
from cubby.cli import sorting as cli_sorting
from cubby.cli.common import make_loud
from cubby.domain.file_ref import Stage
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


def test_json_outputs_hold_no_raw_control_and_decode_to_the_same_names(hostile, capsys):
    assert main(["plan", "--json", *_flags(hostile)]) == EXIT_OK
    out = capsys.readouterr().out
    assert raw_controls(out) == []
    assert json.loads(out)["items"][0]["name"] == HOSTILE

    target = str(hostile / HOSTILE)
    assert main(["explain", "--json", *_flags(hostile), target]) == EXIT_OK
    out = capsys.readouterr().out
    assert raw_controls(out) == []
    assert json.loads(out)["items"][0]["path"] == target


def test_status_and_history_json_hold_no_raw_control(capsys):
    now = datetime.now().isoformat(timespec="seconds")
    Ledger().record(
        RunRecord(
            run="r1",
            mode="run",
            source=HOSTILE,
            started=now,
            finished=now,
            moved=0,
            failed=1,
            failures=(Failure(HOSTILE, f"OSError: {HOSTILE}"),),
        )
    )
    for command in (["status", "--json"], ["history", "--json"]):
        main(command)
        out = capsys.readouterr().out
        assert raw_controls(out) == []
    main(["status", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["last_run"]["failures"][0]["file"] == HOSTILE


def test_json_keeps_accents_cjk_and_emoji_literal(tmp_path, capsys):
    name = "Notes été 日本 🎉 👨\u200d👩\u200d👧 x\u00a0y.pdf"
    aged_file(tmp_path, name)
    assert main(["plan", "--json", *_flags(tmp_path)]) == EXIT_OK
    out = capsys.readouterr().out
    assert f'"name": "{name}"' in out


# --- outputs that only some failures reach ---------------------------------------


def test_status_escapes_a_log_tail_line_that_is_not_json(capsys):
    state.append_line(state.log_path(), "Traceback naming " + HOSTILE.replace("\r\n", ""))
    main(["status"])
    out = capsys.readouterr().out
    assert raw_controls(out) == []
    assert "Traceback naming " + SHOWN.replace("\\r\\n", "") in out


def test_status_escapes_the_kind_of_an_error_group(capsys):
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
            failures=(Failure("plain.pdf", f"{HOSTILE}: boom"),),
        )
    )
    main(["status"])
    out = capsys.readouterr().out
    assert raw_controls(out) == []
    assert f"1x {SHOWN}: boom" in out


def test_status_escapes_the_watched_folder_and_the_agent_file(monkeypatch, capsys):
    real = cli_agent._agent_state

    def hostile_state():
        return {**real(), "watching": HOSTILE, "unit": "unit-" + HOSTILE}

    monkeypatch.setattr(cli_agent, "_agent_state", hostile_state)
    main(["status"])
    out = capsys.readouterr().out
    assert raw_controls(out) == []
    assert f"last watched    {SHOWN}" in out
    assert f"agent file      unit-{SHOWN}" in out


class _FakeService:
    """Installs nothing: the real service manager is never touched."""

    name = "fake"

    def install(self, spec):
        return Path("/tmp") / ("unit-" + HOSTILE)


def test_install_escapes_the_unit_and_the_watched_folder(tmp_path, monkeypatch, capsys):
    source = tmp_path / HOSTILE
    source.mkdir()
    monkeypatch.setattr(cli_agent, "get_service", _FakeService)
    monkeypatch.setenv("CUBBY_STATE_DIR", str(tmp_path / ("state-" + HOSTILE)))
    assert main(["install", "--source", str(source)]) == EXIT_OK
    out = capsys.readouterr().out
    assert raw_controls(out) == []
    assert f"agent: /tmp/unit-{SHOWN} (running)" in out
    assert f"Cubby will watch {tmp_path}/{SHOWN} " in out
    assert f"Logs: {tmp_path}/state-{SHOWN}/" in out


def test_doctor_escapes_a_failed_notification(tmp_path, monkeypatch, capsys):
    def broken_notifier(enabled, *, warn):
        return lambda message: warn(f"notification failed: {HOSTILE}")

    monkeypatch.setattr(cli_agent, "notifier", broken_notifier)
    assert main(["doctor", "--source", str(tmp_path), "--notify"]) == EXIT_FAILED
    err = capsys.readouterr().err
    assert raw_controls(err) == []
    assert f"notification failed: {SHOWN}" in err


def _explanation(tmp_path: Path, **changes) -> Explanation:
    fields = {
        "path": tmp_path / "a.pdf",
        "category": "Documents",
        "stage": Stage.NAME,
        "rule": None,
        "destination": tmp_path / "Documents" / "a.pdf",
        "renamed_to": None,
        "skipped": None,
        "outside": False,
    }
    return Explanation(**{**fields, **changes})


@pytest.mark.parametrize(
    ("changes", "row"),
    [
        ({"skipped": "left alone: " + HOSTILE}, "stays where it is  left alone: " + SHOWN),
        ({"error": "blocked by " + HOSTILE}, "would fail         blocked by " + SHOWN),
        ({"duplicate_of": Path("dup-" + HOSTILE)}, "as a duplicate of dup-" + SHOWN),
        ({"renamed_to": "new-" + HOSTILE}, "renamed            new-" + SHOWN),
        ({"rule": "rule-" + HOSTILE}, "decided by         rule-" + SHOWN),
    ],
)
def test_explain_escapes_every_row(tmp_path, capsys, changes, row):
    cli_inspect._print_explanation(Palette(False), _explanation(tmp_path, **changes), tmp_path)
    out = capsys.readouterr().out
    assert raw_controls(out) == []
    assert row in out


def _failing_plan(monkeypatch, error: BaseException) -> None:
    def load(args):
        raise error

    monkeypatch.setattr(cli_sorting, "load_from_args", load)


@pytest.mark.parametrize(
    ("error", "code", "expected"),
    [
        (ValueError("bad " + HOSTILE), EXIT_BAD_CONFIG, "cubby: config error: bad " + SHOWN),
        (ServiceError("systemctl said " + HOSTILE), EXIT_FAILED, "cubby: systemctl said " + SHOWN),
        (UnicodeError("cannot encode " + HOSTILE), EXIT_FAILED, "cubby: cannot encode " + SHOWN),
        # Python quotes the file name of an OSError with repr(); cubby shows it
        # through the same escaping as everything else, once: a real ESC reads
        # \x1b, not \\x1b (which is how a literal backslash name reads).
        (
            PermissionError(13, "Permission denied", "/d/" + HOSTILE),
            EXIT_FAILED,
            f"cubby: [Errno 13] Permission denied: '/d/{SHOWN}'",
        ),
        (
            OSError(18, "Invalid cross-device link", "a-" + HOSTILE, None, "b-" + HOSTILE),
            EXIT_FAILED,
            f"cubby: [Errno 18] Invalid cross-device link: 'a-{SHOWN}' -> 'b-{SHOWN}'",
        ),
    ],
)
def test_the_last_resort_error_handlers_escape_the_message(
    monkeypatch, capsys, error, code, expected
):
    _failing_plan(monkeypatch, error)
    assert main(["plan"]) == code
    err = capsys.readouterr().err
    assert raw_controls(err) == []
    assert expected + "\n" in err


def test_an_os_error_without_a_file_name_reads_as_python_writes_it(monkeypatch, capsys):
    _failing_plan(monkeypatch, OSError("no detail " + HOSTILE))
    assert main(["plan"]) == EXIT_FAILED
    assert f"cubby: no detail {SHOWN}\n" in capsys.readouterr().err


def test_a_toml_error_escapes_the_config_path(tmp_path, capsys):
    folder = tmp_path / HOSTILE
    folder.mkdir()
    (folder / "config.toml").write_text("[settings\n", encoding="utf-8")
    assert main(["plan", "--config", str(folder / "config.toml")]) == EXIT_BAD_CONFIG
    err = capsys.readouterr().err
    assert raw_controls(err) == []
    assert f"{SHOWN}/config.toml is not valid TOML" in err


def test_undo_shows_a_hostile_name_in_an_os_error_once(hostile, monkeypatch, capsys):
    assert main(["run", *_flags(hostile)]) == EXIT_OK
    capsys.readouterr()

    def refuse(*args, **kwargs):
        raise PermissionError(13, "Permission denied", str(hostile / HOSTILE))

    monkeypatch.setattr("cubby.app.undo.move_no_clobber", refuse)
    assert main(["undo"]) == EXIT_FAILED
    out = capsys.readouterr().out
    assert raw_controls(out) == []
    assert f"[Errno 13] Permission denied: '{hostile}/{SHOWN}'" in out


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


def test_the_text_is_escaped_before_it_is_shortened(tools):
    cmd = notify_module.command("\x1b" * 1000, platform="darwin")
    assert cmd is not None
    assert cmd[-1] == "\\x1b" * 59 + "\\x1" + "…"
    assert len(cmd[-1]) == notify_module.MAX_CHARS


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


def _harmless_invisible(ch: str) -> bool:
    """Invisible characters a terminal does not act on, kept as they are: the
    joiners, the tag characters of subdivision flags and the spaces (Zs)."""
    return ch in "\u200c\u200d" or 0xE0020 <= ord(ch) <= 0xE007F or unicodedata.category(ch) == "Zs"


def _is_control(ch: str) -> bool:
    """A character a terminal could act on, or that hides what a name is: a
    control, a format character, a line or paragraph separator, a surrogate,
    a private or unassigned code point."""
    if _harmless_invisible(ch):
        return False
    return not ch.isprintable() or unicodedata.category(ch) in {"Cc", "Cf", "Zl", "Zp"}


@given(st.text(st.characters(codec=None)))
def test_escaped_text_holds_no_control_character_and_loses_nothing(text):
    shown = escape_for_terminal(text)
    assert not any(_is_control(ch) for ch in shown)
    assert _unescape(shown) == text


_PLAIN = st.characters(codec=None).filter(
    lambda ch: (ch.isprintable() or _harmless_invisible(ch)) and ch != "\\"
)


@given(st.text(_PLAIN))
def test_plain_printable_text_is_left_as_it_is(text):
    assert escape_for_terminal(text) == text


def test_a_literal_backslash_name_never_reads_like_an_escaped_one():
    assert escape_for_terminal("a\\x1b") == "a\\\\x1b"
    assert escape_for_terminal("a\x1b") == "a\\x1b"


_JSON_VALUES = st.recursive(
    st.none() | st.booleans() | st.integers() | st.text(st.characters(codec=None)),
    lambda inner: (
        st.lists(inner, max_size=4)
        | st.dictionaries(st.text(st.characters(codec=None)), inner, max_size=4)
    ),
    max_leaves=12,
)


@given(_JSON_VALUES)
def test_json_for_a_terminal_decodes_to_the_same_values(value):
    text = dumps_for_terminal(value)
    assert json.loads(text) == value
    assert not any(_is_control(ch) for ch in text if ch != "\n")


@given(st.dictionaries(st.text(_PLAIN), st.text(_PLAIN), max_size=4))
def test_json_for_a_terminal_writes_plain_text_literally(value):
    assert dumps_for_terminal(value) == json.dumps(value, ensure_ascii=False, indent=2)


def test_json_escapes_characters_above_the_basic_plane_as_surrogate_pairs():
    assert dumps_for_terminal("\U000f0000") == '"\\udb80\\udc00"'
    assert dumps_for_terminal("\x85\u202e\udcff\x7f") == '"\\u0085\\u202e\\udcff\\u007f"'


# A family emoji (joined by U+200D) and a Persian word (with U+200C) keep their joiners;
# spaces and the tag characters of a subdivision flag stay too.
@pytest.mark.parametrize(
    "name",
    [
        "été.pdf",
        "日本.pdf",
        "🎉.pdf",
        "👨\u200d👩\u200d👧.pdf",
        "\u0645\u06cc\u200c\u062e\u0648\u0627\u0647\u0645",
        # macOS screenshots put a narrow no-break space before AM/PM.
        "Screenshot 2026-09-28 at 12.05.46\u202fPM.png",
        "nbsp\u00a0x.txt",
        # The England flag: a black flag, then tag characters.
        "\U0001f3f4\U000e0067\U000e0062\U000e0065\U000e006e\U000e0067\U000e007f.pdf",
    ],
)
def test_ordinary_names_are_unchanged(name):
    assert escape_for_terminal(name) == name
