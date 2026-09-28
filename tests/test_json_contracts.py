"""Every ``--json`` output matches the schema published in ``docs/schemas``.

The schemas are the contract scripts rely on: a renamed or dropped field fails
here instead of in someone's pipeline. Each output is produced by the real
command, in states that fill its optional parts, and checked against a strict
copy of its schema (no field the schema does not name), while the published
schemas stay open to fields added later within a version.
"""

from __future__ import annotations

import copy
import json
import os
import typing
from pathlib import Path

import fastjsonschema
import pytest

from cubby.adapters import state
from cubby.adapters.ledger import Ledger, RunRecord
from cubby.adapters.logging import LEVELS, file_logger, run_context
from cubby.adapters.pause import pause_path, set_pause
from cubby.adapters.service.launchd import LaunchdService
from cubby.adapters.service.systemd import SystemdService
from cubby.app import sorter as sorter_module
from cubby.app.history import UndoState
from cubby.cli import agent as cli_agent
from cubby.cli import main
from cubby.domain.file_ref import Stage
from tests.helpers import aged_file, process_named_cubby_watch

needs_permissions = pytest.mark.skipif(os.geteuid() == 0, reason="root ignores folder permissions")

SCHEMAS = Path(__file__).resolve().parents[1] / "docs" / "schemas"
META = Path(__file__).resolve().parent / "data" / "json-schema-draft-07.json"
NAMES = ["explain", "history", "log-record", "plan", "status"]

#: The draft-07 keywords these schemas may use; anything else is a typo
#: ("requried") that the validator would silently ignore.
KEYWORDS = {
    "$schema", "title", "description", "type", "properties", "required", "items",
    "enum", "const", "minimum", "oneOf", "additionalProperties",
}  # fmt: skip


def _schema(name: str) -> dict:
    return json.loads((SCHEMAS / f"{name}.schema.json").read_text("utf-8"))


def _strict(schema: dict) -> dict:
    """The schema with no room for unnamed fields: what cubby itself must emit."""
    strict = copy.deepcopy(schema)

    def close(node):
        if isinstance(node, dict):
            if "properties" in node:
                node["additionalProperties"] = False
            for value in node.values():
                close(value)
        elif isinstance(node, list):
            for value in node:
                close(value)

    close(strict)
    return strict


def _validator(name: str):
    return fastjsonschema.compile(_strict(_schema(name)))


def _json_of(capsys, argv: list[str]):
    main(argv)
    return json.loads(capsys.readouterr().out)


def _enum_at(schema: dict, *path: str) -> set:
    node = schema
    for key in path:
        node = node[key]
    return set(node["enum"])


@pytest.fixture
def downloads(tmp_path) -> Path:
    folder = tmp_path / "Downloads"
    aged_file(folder, "notes.txt")
    aged_file(folder, "invoice-2026-03.pdf", "Invoice 2026-03-14")
    aged_file(folder, "photo.png")
    return folder


# --- the schemas themselves ------------------------------------------------------


def test_every_schema_is_a_draft_07_schema_with_known_keywords_only():
    meta = fastjsonschema.compile(json.loads(META.read_text("utf-8")))
    assert sorted(p.name.removesuffix(".schema.json") for p in SCHEMAS.glob("*.json")) == NAMES

    def keywords(node, found):
        if isinstance(node, dict):
            for key, value in node.items():
                found.add(key)
                if key == "properties":
                    for sub in value.values():
                        keywords(sub, found)
                else:
                    keywords(value, found)
        elif isinstance(node, list):
            for value in node:
                keywords(value, found)
        return found

    for name in NAMES:
        meta(_schema(name))
        assert keywords(_schema(name), set()) <= KEYWORDS, name


def test_the_listed_values_are_the_code_s():
    plan, explain, history, status = (_schema(n) for n in ("plan", "explain", "history", "status"))
    stages = {stage.value for stage in Stage}
    assert _enum_at(plan, "properties", "items", "items", "properties", "stage") == stages | {None}
    assert _enum_at(explain, "properties", "items", "items", "properties", "stage") == stages
    run = history["properties"]["runs"]["items"]["properties"]
    assert set(run["undo"]["enum"]) == set(typing.get_args(UndoState))
    agent = status["properties"]["agent"]["properties"]
    assert set(agent["manager"]["enum"]) == {LaunchdService.name, SystemdService.name, None}
    outcomes = {
        RunRecord("r", "run", "/d", "a", "b", moved=m, failed=f).status
        for m, f in ((1, 0), (1, 1), (0, 1))
    }
    assert set(run["status"]["enum"]) == outcomes
    assert _enum_at(_schema("log-record"), "properties", "level") == set(LEVELS)


@pytest.mark.parametrize(
    ("name", "broken"),
    [
        ("plan", {"version": 1, "applied": True, "count": 0, "failed": 0}),  # no items
        ("history", {"version": 2, "runs": []}),  # another version
        ("status", {"version": 1, "healthy": True}),  # parts missing
        ("explain", {"version": 1, "items": [{"path": "/x"}]}),  # item incomplete
    ],
)
def test_the_schemas_refuse_what_is_not_the_contract(name, broken):
    with pytest.raises(fastjsonschema.JsonSchemaException):
        fastjsonschema.compile(_schema(name))(broken)


def test_the_published_schemas_accept_a_field_added_later():
    payload = {"version": 1, "items": [], "new_field": True}
    fastjsonschema.compile(_schema("explain"))(payload)
    with pytest.raises(fastjsonschema.JsonSchemaException):
        _validator("explain")(payload)  # but cubby itself emits nothing unnamed


# --- the real outputs --------------------------------------------------------------


def test_plan(downloads, capsys):
    payload = _json_of(capsys, ["plan", "--source", str(downloads), "--json"])

    _validator("plan")(payload)
    assert {item["stage"] for item in payload["items"]} >= {"name", "type"}


@needs_permissions
def test_history_with_a_partial_run_and_a_partly_undone_one(downloads, capsys, monkeypatch):
    real_move = sorter_module.move_into

    def refuse_png(path, *args, **kwargs):
        if path.suffix == ".png":
            raise PermissionError(13, "Permission denied")
        return real_move(path, *args, **kwargs)

    monkeypatch.setattr(sorter_module, "move_into", refuse_png)
    main(["run", "--source", str(downloads), "--delay", "0"])  # partial: the png fails
    monkeypatch.setattr(sorter_module, "move_into", real_move)
    aged_file(downloads, "second.txt")
    aged_file(downloads, "third.txt")
    main(["run", "--source", str(downloads), "--delay", "0"])  # moves photo, second, third
    main(["run", "--source", str(downloads), "--delay", "0"])  # nothing: no ledger line
    capsys.readouterr()
    history = Ledger().runs()
    latest = history[0].run
    (downloads / "Documents" / "third.txt").chmod(0o644)
    (downloads / "Documents").chmod(0o555)  # third.txt cannot go back: partly undone
    try:
        main(["undo", "--run", latest])
    finally:
        (downloads / "Documents").chmod(0o755)
    capsys.readouterr()

    payload = _json_of(capsys, ["history", "--json"])

    _validator("history")(payload)
    runs = {run["run"]: run for run in payload["runs"]}
    assert runs[latest]["undo"] == "partly undone"
    assert runs[history[1].run]["status"] == "partial"
    assert runs[history[1].run]["failures"][0]["error"]


@pytest.mark.parametrize("pause", ["none", "timed", "damaged"])
def test_status_in_its_pause_states(downloads, capsys, monkeypatch, pause):
    monkeypatch.setattr(cli_agent, "detect_service", lambda: None)
    main(["run", "--source", str(downloads), "--delay", "0"])
    capsys.readouterr()
    if pause == "timed":
        set_pause(3600)
    elif pause == "damaged":
        pause_path().parent.mkdir(parents=True, exist_ok=True)
        pause_path().write_text("not json", encoding="utf-8")

    payload = _json_of(capsys, ["status", "--json"])

    _validator("status")(payload)
    paused = payload["paused"]
    if pause == "none":
        assert paused is None
    elif pause == "timed":
        assert paused["damaged"] is False
        assert isinstance(paused["until"], float)
    else:
        assert paused["damaged"] is True


def test_status_after_a_run_with_failures(downloads, capsys, monkeypatch):
    monkeypatch.setattr(cli_agent, "detect_service", lambda: None)
    real_move = sorter_module.move_into

    def refuse_png(path, *args, **kwargs):
        if path.suffix == ".png":
            raise PermissionError(13, "Permission denied")
        return real_move(path, *args, **kwargs)

    monkeypatch.setattr(sorter_module, "move_into", refuse_png)
    main(["run", "--source", str(downloads), "--delay", "0"])
    capsys.readouterr()

    payload = _json_of(capsys, ["status", "--json"])

    _validator("status")(payload)
    assert payload["last_run"]["status"] == "partial"
    assert payload["last_run"]["failures"][0]["error"]


def test_status_with_a_surviving_agent(capsys, monkeypatch):
    monkeypatch.setattr(cli_agent, "detect_service", lambda: None)
    with process_named_cubby_watch() as pid:
        monkeypatch.setattr("os.getpid", lambda: pid)
        Ledger().beat(Path("/d"), 30.0)

        payload = _json_of(capsys, ["status", "--json"])

    _validator("status")(payload)
    assert payload["agent"]["live_pid"] == pid


def test_explain_inside_and_outside_the_folder(downloads, tmp_path, capsys):
    outside = aged_file(tmp_path / "elsewhere", "report.txt")
    files = [str(p) for p in sorted(downloads.iterdir())] + [str(outside)]

    payload = _json_of(capsys, ["explain", "--source", str(downloads), "--json", *files])

    _validator("explain")(payload)
    assert {item["outside_source"] for item in payload["items"]} == {True, False}


def test_log_records_including_lines_cubby_did_not_write(capsys):
    log = file_logger()
    log("agent started")
    with run_context("r1"):
        log("could not sort x", level="WARNING")
    with state.log_path().open("a", encoding="utf-8") as handle:
        handle.write("Traceback (most recent call last):\n")
        handle.write('{"level": "DEBUG", "msg": 5}\n')  # JSON, but not a cubby record

    main(["log", "--json"])
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]

    validate = _validator("log-record")
    for record in records:
        validate(record)
    assert records[-1] == {"msg": '{"level": "DEBUG", "msg": 5}'}


def test_log_json_with_no_log_prints_nothing(capsys):
    assert main(["log", "--json"]) == 0
    assert capsys.readouterr().out == ""
