"""Every ``--json`` output matches the schema published in ``docs/schemas``.

The schemas are the contract scripts rely on: a renamed or dropped field fails
here instead of in someone's pipeline. Each output is produced by the real
command, in states that exercise its optional parts (failures, a pause, a
damaged pause file, a surviving agent, a line that is not JSON).
"""

from __future__ import annotations

import json
from pathlib import Path

import fastjsonschema
import pytest

from cubby.adapters import state
from cubby.adapters.logging import file_logger, run_context
from cubby.adapters.pause import pause_path, set_pause
from cubby.cli import agent as cli_agent
from cubby.cli import main
from tests.helpers import aged_file, process_named_cubby_watch

SCHEMAS = Path(__file__).resolve().parents[1] / "docs" / "schemas"


def _validator(name: str):
    return fastjsonschema.compile(json.loads((SCHEMAS / f"{name}.schema.json").read_text("utf-8")))


def _json_of(capsys, argv: list[str]):
    main(argv)
    return json.loads(capsys.readouterr().out)


@pytest.fixture
def downloads(tmp_path) -> Path:
    folder = tmp_path / "Downloads"
    aged_file(folder, "notes.txt")
    aged_file(folder, "invoice-2026-03.pdf", "Invoice 2026-03-14")
    aged_file(folder, "photo.png")
    return folder


def test_every_schema_is_a_valid_schema():
    names = sorted(p.name.removesuffix(".schema.json") for p in SCHEMAS.glob("*.schema.json"))
    assert names == ["explain", "history", "log-record", "plan", "status"]
    for name in names:
        _validator(name)


def test_plan(downloads, capsys):
    payload = _json_of(capsys, ["plan", "--source", str(downloads), "--json"])

    _validator("plan")(payload)
    assert {item["stage"] for item in payload["items"]} >= {"name", "type"}


def test_history_with_undone_and_failed_runs(downloads, capsys):
    main(["run", "--source", str(downloads), "--delay", "0"])
    aged_file(downloads, "second.txt")
    main(["run", "--source", str(downloads), "--delay", "0"])
    main(["undo"])
    capsys.readouterr()

    payload = _json_of(capsys, ["history", "--json"])

    _validator("history")(payload)
    assert {run["undo"] for run in payload["runs"]} == {"undoable", "undone"}


@pytest.mark.parametrize("pause", ["none", "timed", "damaged"])
def test_status_in_its_states(downloads, capsys, monkeypatch, pause):
    monkeypatch.setattr(cli_agent, "detect_service", lambda: None)
    main(["run", "--source", str(downloads), "--delay", "0"])
    capsys.readouterr()
    if pause == "timed":
        set_pause(3600)
    elif pause == "damaged":
        pause_path().parent.mkdir(parents=True, exist_ok=True)
        pause_path().write_text("not json", encoding="utf-8")

    _validator("status")(_json_of(capsys, ["status", "--json"]))


def test_status_with_a_surviving_agent(capsys, monkeypatch):
    from cubby.adapters.ledger import Ledger

    monkeypatch.setattr(cli_agent, "detect_service", lambda: None)
    with process_named_cubby_watch() as pid:
        ledger = Ledger()
        ledger.heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr("os.getpid", lambda: pid)
        ledger.beat(Path("/d"), 30.0)

        payload = _json_of(capsys, ["status", "--json"])

    _validator("status")(payload)
    assert payload["agent"]["live_pid"] == pid


def test_explain(downloads, capsys):
    files = [str(p) for p in sorted(downloads.iterdir())] + ["/etc/hostname"]

    _validator("explain")(
        _json_of(capsys, ["explain", "--source", str(downloads), "--json", *files])
    )


def test_log_records(capsys):
    log = file_logger()
    log("agent started")
    with run_context("r1"):
        log("could not sort x", level="WARNING")
    with state.log_path().open("a", encoding="utf-8") as handle:
        handle.write("a traceback line\n")

    main(["log", "--json"])
    validate = _validator("log-record")
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 3
    for line in lines:
        validate(json.loads(line))


@pytest.mark.parametrize(
    ("name", "broken"),
    [
        ("plan", {"version": 1, "applied": True, "count": 0, "failed": 0}),  # no items
        ("history", {"version": 2, "runs": []}),  # unknown version
        ("status", {"version": 1, "healthy": True}),  # parts missing
        ("explain", {"version": 1, "items": [], "extra": 1}),  # unknown field
    ],
)
def test_the_schemas_refuse_what_is_not_the_contract(name, broken):
    with pytest.raises(fastjsonschema.JsonSchemaException):
        _validator(name)(broken)
