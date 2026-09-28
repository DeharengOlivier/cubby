"""The journal compaction of cubby 0.3.0, frozen: the reference for the streaming one.

0.3.0 read the whole journal into memory, parsed every line into fields grouped
by run, and kept the lines of the runs worth keeping. Compaction now streams the
file twice instead (docs/PERFORMANCE.md, "Memory of one pass"). This is the
0.3.0 decision (commit b6dd32b), self-contained, over a list of lines. Do not
update it to match the code: a difference is a finding.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator

_Fields = tuple[str, int, str]


def _v1_run_id(line: str) -> str:
    return "v1-" + hashlib.sha256(line.encode("utf-8")).hexdigest()[:12]


def _seq(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"not a sequence number: {value!r}")
    return value


def _path(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError(f"not a path: {value!r}")
    return value


def _loads(line: str) -> object:
    try:
        return json.loads(line)
    except RecursionError:
        raise json.JSONDecodeError("nested too deep to read", line[:100], 0) from None


def _fields(line: str) -> Iterator[_Fields]:
    try:
        record = _loads(line)
    except json.JSONDecodeError:
        return
    if not isinstance(record, dict):
        return
    try:
        if "moves" in record and "v" not in record:
            run_id = _v1_run_id(line)
            for seq, move in enumerate(record["moves"]):
                _path(move["from"]), _path(move["to"])
                yield run_id, seq, "move"
            return
        if record.get("v") != 2:
            return
        op = record["op"]
        if op in ("move", "dedupe"):
            run, seq = str(record["run"]), _seq(record["seq"])
            _path(record["from"]), _path(record["to"])
            yield run, seq, op
        elif op in ("restored", "gone"):
            yield str(record["run"]), _seq(record["seq"]), op
    except (KeyError, TypeError, ValueError):
        return


def _run_of(line: str) -> str | None:
    try:
        record = _loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(record, dict):
        return None
    if "moves" in record and "v" not in record:
        return _v1_run_id(line)
    return str(record["run"]) if "run" in record else None


def _tally(lines: list[str]) -> dict[str, tuple[int, int]]:
    groups: dict[str, list[_Fields]] = {}
    for line in lines:
        for fields in _fields(line):
            groups.setdefault(fields[0], []).append(fields)
    counts = {}
    for run_id, fields in groups.items():
        moves = [seq for _, seq, op in fields if op in ("move", "dedupe")]
        settled = {seq for _, seq, op in fields if op not in ("move", "dedupe")}
        counts[run_id] = (len(moves), sum(1 for seq in moves if seq not in settled))
    return {run_id: count for run_id, count in counts.items() if count[0]}


def compact(lines: list[str], keep_runs: int) -> list[str] | None:
    """The lines 0.3.0 kept, or None when it left the file as it was."""
    tallies = _tally(lines)
    keep = set(list(tallies)[-keep_runs:]) | {run for run, (_, left) in tallies.items() if left}
    kept = [line for line in lines if _run_of(line) in keep]
    return kept if len(kept) < len(lines) else None
