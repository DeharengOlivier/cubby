"""Reproducers for the independent review of pull request 2.

Each test failed on the reviewed commit; the review record is on the pull
request.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from cubby.adapters import extraction
from cubby.adapters import journal as journal_module
from cubby.adapters.journal import Entry, Journal
from cubby.adapters.ledger import Ledger
from cubby.adapters.logging import file_logger
from cubby.adapters.service import ServiceSpec
from cubby.adapters.service import launchd as launchd_mod
from cubby.adapters.service import systemd as systemd_mod
from cubby.adapters.service.launchd import LaunchdService
from cubby.adapters.service.systemd import SystemdService
from cubby.app.sorter import Sorter
from cubby.app.undo import undo_run
from cubby.cli import EXIT_FAILED, EXIT_OK, main
from cubby.cli import agent as cli_agent
from cubby.cli import sorting as cli_sorting
from tests.helpers import config_for

# --- 1. a downloaded .py must never run --------------------------------------


def test_a_module_planted_in_the_working_directory_is_not_imported(tmp_path, monkeypatch):
    pytest.importorskip("docx")
    planted = tmp_path / "Downloads"
    planted.mkdir()
    marker = tmp_path / "pwned"
    (planted / "docx.py").write_text(f"open({str(marker)!r}, 'w').write('x')\n")
    (planted / "letter.docx").write_bytes(b"PK not really a docx")
    monkeypatch.chdir(planted)
    monkeypatch.setattr(extraction.shutil, "which", lambda _: None)

    extraction.extract_text(planted / "letter.docx", "docx")

    assert not marker.exists(), "a file from the Downloads folder was executed"


def test_the_child_parser_runs_isolated_from_the_working_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(extraction.importlib.util, "find_spec", lambda name: object())
    calls: list[tuple[list[str], dict]] = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(extraction.subprocess, "run", fake_run)
    extraction._in_child("pdf", tmp_path / "a.pdf", 100)

    ((cmd, kwargs),) = calls
    assert cmd[:3] == [sys.executable, "-P", "-m"]
    assert kwargs["cwd"] == "/"


# --- 2. a move is journaled before anything else can fail --------------------


def test_a_logger_that_raises_after_a_move_cannot_unjournal_it(tmp_path):
    source = tmp_path / "Downloads"
    source.mkdir()
    (source / "a.txt").write_text("a")
    journal = Journal(tmp_path / "journal.jsonl")

    def broken_log(message: str) -> None:
        raise BrokenPipeError(32, "Broken pipe")

    outcomes = Sorter(config_for(source), journal=journal, log=broken_log).sort_once(apply=True)

    assert (source / "Documents" / "a.txt").exists()
    assert outcomes[0].error is None, "a moved file must not be reported as failed"
    run = journal.last_pending_run()
    assert run is not None
    assert [e.source.name for e in run.entries] == ["a.txt"]


def test_echo_to_a_closed_pipe_stops_echoing_but_keeps_logging(tmp_path, monkeypatch):
    log = file_logger(tmp_path / "cubby.log", echo=True)

    def closed(*args, **kwargs):
        raise BrokenPipeError(32, "Broken pipe")

    monkeypatch.setattr("builtins.print", closed)
    log("one")
    log("two")
    monkeypatch.undo()

    assert len((tmp_path / "cubby.log").read_text().splitlines()) == 2


def test_run_through_head_journals_every_move(tmp_path):
    source = tmp_path / "Downloads"
    source.mkdir()
    for index in range(200):
        (source / f"f{index:03}.txt").write_text("x")
    env = {**os.environ, "CUBBY_STATE_DIR": str(tmp_path / "state"), "HOME": str(tmp_path)}
    cubby = [sys.executable, "-m", "cubby", "run", "-v", "--source", str(source), "--delay", "0"]

    with subprocess.Popen(cubby, stdout=subprocess.PIPE, env=env) as proc:
        assert proc.stdout is not None
        proc.stdout.readline()
        proc.stdout.close()  # what `| head -1` does
        proc.wait(timeout=60)

    journal = Journal(tmp_path / "state" / "journal.jsonl")
    moved = sorted(p.name for p in (source / "Documents").iterdir())
    journaled = sorted(e.source.name for r in journal.runs() for e in r.entries)
    assert moved == journaled
    assert len(moved) == 200


# --- 3. hostile journal lines --------------------------------------------------


@pytest.mark.parametrize("seq", ["1e999", "0.9", "true", '"0"', "-1"])
def test_a_journal_line_with_a_bad_seq_is_skipped(tmp_path, seq):
    journal = Journal(tmp_path / "journal.jsonl")
    journal.record(Entry("r1", 0, "move", tmp_path / "a", tmp_path / "b"))
    with journal.path.open("a", encoding="utf-8") as handle:
        handle.write(f'{{"v": 2, "run": "r1", "seq": {seq}, "op": "restored"}}\n')

    (run,) = journal.runs()
    assert run.settled == {}, "only an integer seq may settle an entry"


# --- 4. compaction never drops what can still be undone -----------------------


def test_compaction_keeps_pending_runs_and_version_1_lines(tmp_path, monkeypatch):
    journal = Journal(tmp_path / "journal.jsonl")
    journal.path.write_text(
        json.dumps({"ts": "2026-08-01T10:00:00", "moves": [{"from": "/a", "to": "/b"}]}) + "\n"
    )
    for index in range(5):
        entry = Entry(f"r{index}", 0, "move", Path(f"/s{index}"), Path(f"/d{index}"))
        journal.record(entry)
        if index != 1:
            journal.settle(entry, "restored")
    monkeypatch.setattr(journal_module, "MAX_BYTES", 1)
    monkeypatch.setattr(journal_module, "KEEP_RUNS", 1)
    v1_id = journal.runs()[0].run_id

    journal.compact()

    ids = [r.run_id for r in journal.runs()]
    assert v1_id in ids, "a pending 0.1 run was dropped"
    assert "r1" in ids, "a pending run was dropped"
    assert "r4" in ids
    assert "r2" not in ids
    # A version 1 run keeps its identity across the rewrite.
    assert [r.run_id for r in journal.runs() if r.run_id.startswith("v1-")] == [v1_id]


def test_run_ids_do_not_collide_within_a_second():
    ids = {journal_module.new_run_id() for _ in range(5000)}
    assert len(ids) == 5000
    # The random part carries 64 bits: 5 000 ids in one second collide with a
    # probability near 7e-13, where 32 bits failed CI once (0.3%).
    assert {len(run_id.rsplit("-", 1)[1]) for run_id in ids} == {16}


# --- 5. the agent uses the state folder the CLI uses --------------------------


def test_the_systemd_agent_is_given_the_state_folder(tmp_path, monkeypatch):
    from tests.test_service_boundary import FakeManager

    monkeypatch.setattr(systemd_mod, "_UNIT_DIR", tmp_path / "units")
    monkeypatch.setattr("subprocess.run", FakeManager())
    spec = ServiceSpec(
        program_args=["/usr/bin/cubby", "watch"],
        log_path=tmp_path / "log",
        environment={"CUBBY_STATE_DIR": "/home/x/100% state"},
    )

    unit = SystemdService().install(spec).read_text()

    assert 'Environment="CUBBY_STATE_DIR=/home/x/100%% state"' in unit


def test_the_launchd_agent_is_given_the_state_folder(tmp_path, monkeypatch):
    import plistlib

    from tests.test_service_boundary import FakeManager

    monkeypatch.setattr(launchd_mod, "_AGENTS_DIR", tmp_path / "agents")
    monkeypatch.setattr("subprocess.run", FakeManager())
    spec = ServiceSpec(
        program_args=["/usr/bin/cubby", "watch"],
        log_path=tmp_path / "log",
        environment={"CUBBY_STATE_DIR": "/Users/x/state"},
    )

    with LaunchdService().install(spec).open("rb") as handle:
        plist = plistlib.load(handle)

    assert plist["EnvironmentVariables"] == {"CUBBY_STATE_DIR": "/Users/x/state"}


def test_install_bakes_the_state_folder_and_waits_for_the_source(tmp_path, monkeypatch):
    source = tmp_path / "Downloads"
    source.mkdir()
    captured: dict = {}

    class Recording:
        name = "fake"

        def install(self, spec):
            captured["spec"] = spec
            return tmp_path / "unit"

    monkeypatch.setattr(cli_agent, "get_service", Recording)
    assert main(["install", "--source", str(source)]) == EXIT_OK

    spec = captured["spec"]
    assert spec.environment["CUBBY_STATE_DIR"] == os.environ["CUBBY_STATE_DIR"]
    assert "--wait-for-source" in spec.program_args


# --- 6. an agent that never completes a pass is not healthy -------------------


def test_status_flags_an_agent_that_never_completed_a_pass(tmp_path, monkeypatch, capsys):
    unit = tmp_path / "unit"
    unit.write_text("x")
    old = time.time() - 3600
    os.utime(unit, (old, old))

    class Agent:
        name = "fake"

        def is_installed(self, label="com.cubby.agent"):
            return True

        def is_running(self, label="com.cubby.agent"):
            return True

        def unit_path(self, label):
            return unit

    monkeypatch.setattr(cli_agent, "detect_service", Agent)
    assert Ledger().heartbeat() is None

    assert main(["status"]) == EXIT_FAILED
    assert "no pass completed" in capsys.readouterr().out


# --- 7. a run whose moves cannot be undone does not exit 0 --------------------


def test_run_exits_non_zero_when_the_journal_cannot_be_written(tmp_path, monkeypatch, capsys):
    source = tmp_path / "Downloads"
    source.mkdir()
    (source / "a.txt").write_text("a")

    def refuse(self, entry):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(Journal, "record", refuse)

    code = main(["run", "--source", str(source), "--delay", "0"])

    assert code == EXIT_FAILED
    assert "cannot be undone" in capsys.readouterr().out


# --- 8. the agent waits for a folder that is not mounted yet ------------------


def test_the_agent_waits_for_a_missing_source_instead_of_exiting(tmp_path, monkeypatch):
    missing = tmp_path / "Drive"
    runs: list[int] = []

    class OneCycle(cli_sorting.Watcher):
        def run(self, **_):
            runs.append(1)
            return super().run(max_cycles=1)

    monkeypatch.setattr(cli_sorting, "Watcher", OneCycle)

    assert main(["watch", "--source", str(missing), "--wait-for-source"]) == EXIT_OK
    assert runs == [1]


# --- 9. a moved dangling symlink is restored, not declared gone ---------------


def test_undo_restores_a_dangling_symlink(tmp_path):
    source = tmp_path / "Downloads"
    (source / "Documents").mkdir(parents=True)
    link = source / "Documents" / "shortcut.txt"
    link.symlink_to(tmp_path / "nowhere")
    journal = Journal(tmp_path / "journal.jsonl")
    journal.record(Entry("r1", 0, "move", source / "shortcut.txt", link))

    result = undo_run(journal)

    assert result.restored == 1
    assert (source / "shortcut.txt").is_symlink()
