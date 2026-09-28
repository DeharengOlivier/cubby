"""A state line nested deeper than the JSON parser recurses is a damaged line.

Found by review: ``json.loads`` raises ``RecursionError``, not a decode error,
on a value nested a few thousand levels deep, and the journal readers let it
through, so one such line stopped every read, every undo and every compaction.
cubby never writes such a line; a damaged or hostile state file must still cost
that line only, as every other damaged line does.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cubby.adapters import journal as journal_module
from cubby.adapters.journal import Journal
from cubby.adapters.ledger import Ledger
from cubby.adapters.logging import read_all
from cubby.adapters.pause import current_pause, pause_path

DEEP = '{"v": 2, "run": ' + "[" * 100_000 + "]" * 100_000 + ', "seq": 0, "op": "move"}'
GOOD = '{"v": 2, "run": "r1", "seq": 0, "op": "move", "from": "/a", "to": "/b"}'


def _write(path: Path, *lines: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")


def test_the_journal_skips_it_in_every_read_and_in_compaction(tmp_path, monkeypatch):
    journal = Journal(tmp_path / "journal.jsonl")
    _write(journal.path, DEEP, GOOD)
    monkeypatch.setattr(journal_module, "MAX_BYTES", 0)

    assert [run.run_id for run in journal.runs()] == ["r1"]
    assert journal.tallies() == {"r1": (1, 1)}
    assert journal.last_pending_run() is not None
    journal.compact()
    assert journal.tallies() == {"r1": (1, 1)}


def test_the_ledger_skips_it():
    ledger = Ledger()
    _write(ledger.runs_path, DEEP)

    assert ledger.runs() == []


def test_the_heartbeat_reads_as_none():
    ledger = Ledger()
    _write(ledger.heartbeat_path, DEEP)

    assert ledger.heartbeat() is None


def test_the_log_shows_it_as_a_foreign_line(tmp_path):
    log = tmp_path / "cubby.log"
    _write(log, DEEP)

    (record,) = read_all(log)

    assert record["msg"] == DEEP


def test_a_pause_file_holding_it_is_a_damaged_pause():
    _write(pause_path(), DEEP)

    pause = current_pause()

    assert pause is not None
    assert pause.damaged


@pytest.mark.parametrize("depth", [10, 900])
def test_ordinary_nesting_still_parses(tmp_path, depth):
    run = "[" * depth + "]" * depth
    journal = Journal(tmp_path / "journal.jsonl")
    _write(
        journal.path, f'{{"v": 2, "run": {run}, "seq": 0, "op": "move", "from": "/a", "to": "/b"}}'
    )

    assert len(journal.runs()) == 1


def test_a_config_file_nested_too_deep_is_a_config_error(tmp_path, capsys):
    from cubby.cli import main

    config = tmp_path / "cubby.toml"
    config.write_text("x = " + "[" * 100_000 + "]" * 100_000 + "\n", encoding="utf-8")

    code = main(["plan", "--config", str(config), "--source", str(tmp_path)])

    assert code == 2
    assert "is not valid TOML: nested too deep to read" in capsys.readouterr().err
