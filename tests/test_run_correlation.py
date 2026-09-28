"""A pass can be followed across the log, the ledger and the journal.

Every log line written during a pass carries the pass's run id, the one the
ledger and the undo journal record, and the cubby version that wrote it, so an
incident can be traced with ``jq 'select(.run == "...")'`` and pinned to a release.
"""

from __future__ import annotations

import json
from pathlib import Path

from cubby import __version__
from cubby.adapters.journal import Journal
from cubby.adapters.ledger import Ledger, RunRecord
from cubby.adapters.logging import file_logger, run_context
from cubby.app.sorter import Sorter
from tests.helpers import config_for


def _lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text("utf-8").splitlines()]


def test_every_line_names_the_version_and_only_lines_of_a_pass_name_a_run(tmp_path):
    log = file_logger(tmp_path / "cubby.log")

    log("before")
    with run_context("run-1"):
        log("during")
    log("after")

    before, during, after = _lines(tmp_path / "cubby.log")
    assert {line["version"] for line in (before, during, after)} == {__version__}
    assert during["run"] == "run-1"
    assert "run" not in before
    assert "run" not in after


def test_the_log_lines_of_a_pass_carry_the_run_id_of_its_ledger_and_journal(tmp_path):
    source = tmp_path / "Downloads"
    source.mkdir()
    (source / "notes.txt").write_text("x")
    config = config_for(source)
    ledger = Ledger(tmp_path / "state")
    journal = Journal(tmp_path / "state" / "journal.jsonl")
    log_path = tmp_path / "cubby.log"

    Sorter(config, log=file_logger(log_path), journal=journal, ledger=ledger).sort_once(apply=True)

    (record,) = ledger.runs()
    lines = _lines(log_path)
    assert lines, "the pass wrote no log line"
    assert {line["run"] for line in lines} == {record.run}
    assert [run.run_id for run in journal.runs()] == [record.run]
    assert record.version == __version__


def test_a_ledger_record_from_before_the_version_field_still_reads(tmp_path):
    old = {
        "run": "r",
        "mode": "run",
        "source": "/d",
        "started": "2026-09-01T10:00:00",
        "finished": "2026-09-01T10:00:01",
        "moved": 1,
        "failed": 0,
    }
    assert RunRecord.from_json(old).version == "unknown"
    assert RunRecord.from_json(RunRecord.from_json(old).to_json()).version == "unknown"
