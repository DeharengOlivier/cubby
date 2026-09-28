"""The journal reader of cubby 0.2, frozen: the reference the fast reads are checked against.

``Journal.runs`` now shares its line parser with the fast reads, so comparing
them with each other would miss a bug in that parser. This is the parser as it
was before the fast reads existed (commit 3941e8c), reduced to plain tuples.
Do not update it to match the code: a difference is a finding.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path

#: ``(run_id, [(seq, op, source, destination)], {seq: resolution})`` per run, oldest first.
RefRun = tuple[str, list[tuple[int, str, Path, Path]], dict[int, str]]


def _v1_run_id(line: str) -> str:
    return "v1-" + hashlib.sha256(line.encode("utf-8")).hexdigest()[:12]


def _seq(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"not a sequence number: {value!r}")
    return value


def _parse(line: str) -> Iterator[tuple[str, int, str, Path | None, Path | None]]:
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return
    if not isinstance(record, dict):
        return
    try:
        if "moves" in record and "v" not in record:
            run_id = _v1_run_id(line)
            for seq, move in enumerate(record["moves"]):
                yield run_id, seq, "move", Path(move["from"]), Path(move["to"])
            return
        if record.get("v") != 2:
            return
        op = record["op"]
        if op in ("move", "dedupe"):
            yield (
                str(record["run"]),
                _seq(record["seq"]),
                op,
                Path(record["from"]),
                Path(record["to"]),
            )
        elif op in ("restored", "gone"):
            yield str(record["run"]), _seq(record["seq"]), op, None, None
    except (KeyError, TypeError, ValueError):
        return


def runs(lines: list[str]) -> list[RefRun]:
    found: dict[str, RefRun] = {}
    for line in lines:
        for run_id, seq, op, source, destination in _parse(line):
            run = found.setdefault(run_id, (run_id, [], {}))
            if source is not None and destination is not None:
                run[1].append((seq, op, source, destination))
            else:
                run[2][seq] = op
    return [run for run in found.values() if run[1]]
