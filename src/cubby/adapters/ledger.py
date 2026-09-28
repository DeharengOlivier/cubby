"""The run ledger and the agent's heartbeat: what happened, and is it alive.

The journal answers "how do I put it back?". The ledger answers "what did cubby
do, and did it work?": one line per run that moved or failed something, with
its counts and failures. The heartbeat is one small file the agent rewrites on
every pass, so ``cubby status`` can tell a live agent from an installed one.

Both are read by ``cubby status`` and ``cubby history``, never by the sort.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess  # nosec B404 - ps, with a fixed argument list and a timeout
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .. import __version__
from . import state

VERSION = 1
MAX_BYTES = 2_000_000
KEEP_LINES = 2_000
#: At most this many failures are written per run; the count is always exact.
MAX_FAILURES_RECORDED = 20


@dataclass(frozen=True)
class Failure:
    file: str
    error: str


@dataclass(frozen=True)
class RunRecord:
    """One run, as the ledger keeps it."""

    run: str
    mode: str  # "run" or "watch"
    source: str
    started: str
    finished: str
    moved: int
    failed: int
    failures: tuple[Failure, ...] = field(default_factory=tuple)
    version: str = __version__  # the cubby that made the run

    @property
    def status(self) -> str:
        if self.failed and not self.moved:
            return "failed"
        return "partial" if self.failed else "ok"

    def to_json(self) -> dict[str, Any]:
        data = asdict(self)
        data["failures"] = [asdict(f) for f in self.failures[:MAX_FAILURES_RECORDED]]
        data["status"] = self.status
        return {"v": VERSION, **data}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> RunRecord:
        return cls(
            run=str(data["run"]),
            mode=str(data["mode"]),
            source=str(data["source"]),
            started=str(data["started"]),
            finished=str(data["finished"]),
            moved=int(data["moved"]),
            failed=int(data["failed"]),
            failures=tuple(
                Failure(str(f["file"]), str(f["error"])) for f in data.get("failures", [])
            ),
            version=str(data.get("version", "unknown")),  # records from before 0.3
        )


@dataclass(frozen=True)
class Heartbeat:
    at: datetime
    pid: int
    source: str
    interval: float

    def age_seconds(self, now: datetime | None = None) -> float:
        return ((now or datetime.now()) - self.at).total_seconds()

    def process_alive(self) -> bool:
        """Whether the process that beat still exists and is a ``cubby watch``.

        Signal 0 probes without sending anything. A pid that exists but runs
        something else was reused after the agent ended; one whose command line
        cannot be read counts as cubby, so a live agent is never reported gone.
        """
        if self.pid <= 0:
            return False  # 0 and -1 name process groups, not a process
        try:
            os.kill(self.pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            pass  # it exists, under another user
        except (OverflowError, OSError):
            return False  # not a pid this system can have
        argv = _command_line(self.pid)
        return argv is None or is_cubby_watch(argv)


def is_cubby_watch(argv: list[str]) -> bool:
    """``cubby watch ...`` or ``python -m cubby watch ...``, however cubby was installed."""
    names = [Path(arg).name for arg in argv]
    return "cubby" in names and "watch" in names[names.index("cubby") + 1 :]


def _command_line(pid: int) -> list[str] | None:
    """The arguments ``pid`` runs with, or None when they cannot be read."""
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()  # Linux
    except OSError:
        pass
    else:
        return [part.decode(errors="replace") for part in raw.split(b"\0") if part]
    ps = shutil.which("ps")
    if ps is None:
        return None
    try:
        result = subprocess.run(  # macOS and the BSDs have no /proc
            [ps, "-o", "command=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.split() if result.returncode == 0 and result.stdout.strip() else None


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Ledger:
    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or state.state_dir()

    @property
    def runs_path(self) -> Path:
        return self.directory / "runs.jsonl"

    @property
    def heartbeat_path(self) -> Path:
        return self.directory / "heartbeat.json"

    def record(self, record: RunRecord) -> None:
        """Append one run.

        Raises:
            OSError: The ledger could not be written.
        """
        state.append_line(self.runs_path, json.dumps(record.to_json()))
        state.keep_last_lines(self.runs_path, max_bytes=MAX_BYTES, keep=KEEP_LINES)

    def runs(self, limit: int | None = None) -> list[RunRecord]:
        """Recorded runs, most recent first. Damaged lines are skipped.

        Raises:
            OSError: The ledger exists but cannot be read.
        """
        records: list[RunRecord] = []
        for line in reversed(state.read_lines(self.runs_path)):
            try:
                data = json.loads(line)
                if data.get("v") != VERSION:
                    continue
                records.append(RunRecord.from_json(data))
            except (json.JSONDecodeError, KeyError, TypeError, ValueError, AttributeError):
                continue
            if limit is not None and len(records) >= limit:
                break
        return records

    def beat(self, source: Path, interval: float) -> None:
        """Record that the agent completed a pass just now.

        Raises:
            OSError: The heartbeat could not be written.
        """
        payload = {
            "v": VERSION,
            "at": now_iso(),
            "pid": os.getpid(),
            "source": str(source),
            "interval": interval,
        }
        state.replace_text(self.heartbeat_path, json.dumps(payload) + "\n")

    def heartbeat(self) -> Heartbeat | None:
        """The agent's last heartbeat, or None if there is none (or it is unreadable)."""
        try:
            data = json.loads(self.heartbeat_path.read_text("utf-8"))
            return Heartbeat(
                at=datetime.fromisoformat(data["at"]),
                pid=int(data["pid"]),
                source=str(data["source"]),
                interval=float(data["interval"]),
            )
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            # Absent or unreadable: either way there is no heartbeat to report.
            return None
