"""Where cubby keeps its own files, and how it writes them.

Cubby's state is small and personal: the undo journal, the run ledger, the
agent's heartbeat, the log and a lock. It lives under
``$CUBBY_STATE_DIR``, else ``$XDG_STATE_HOME/cubby``, else
``~/.local/state/cubby``. On macOS the log goes to ``~/Library/Logs`` so
Console.app finds it, unless ``CUBBY_STATE_DIR`` is set.

Every path is computed when it is asked for, not at import, so the test suite
(and a user) can redirect all of it with one environment variable.

The files name what the user downloaded, so they are created readable by the
owner only.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

#: Permission bits for every file cubby creates in its state folder.
PRIVATE_FILE = 0o600
PRIVATE_DIR = 0o700


def parse_json(text: str) -> Any:
    """``json.loads`` for state files: every way a line can be damaged is a decode error.

    The parser recurses once per level of nesting, so a value nested a few
    thousand levels deep raises ``RecursionError``. cubby never writes one, and
    it must cost its line only, like any other damage, rather than stop every
    read of the file.

    Raises:
        json.JSONDecodeError: ``text`` is not JSON, or is nested too deep to read.
    """
    try:
        return json.loads(text)
    except RecursionError:
        raise json.JSONDecodeError("nested too deep to read", text[:100], 0) from None


def state_dir() -> Path:
    """The folder holding the journal, ledger, heartbeat and lock."""
    explicit = os.environ.get("CUBBY_STATE_DIR")
    if explicit:
        return Path(explicit).expanduser()
    xdg = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "state"
    return base / "cubby"


def log_path() -> Path:
    """The activity log: Console.app's folder on macOS, the state folder elsewhere."""
    if sys.platform == "darwin" and not os.environ.get("CUBBY_STATE_DIR"):
        return Path.home() / "Library" / "Logs" / "cubby.log"
    return state_dir() / "cubby.log"


def ensure_parent(path: Path) -> None:
    """Create ``path``'s folder, owner-only when cubby creates it."""
    path.parent.mkdir(mode=PRIVATE_DIR, parents=True, exist_ok=True)


def append_line(path: Path, line: str) -> None:
    """Append one line to ``path``, creating it owner-only.

    One ``write`` of a line ending in a newline on a file opened with
    ``O_APPEND``: a crash leaves at worst a truncated last line, which every
    reader of these files skips.

    Raises:
        OSError: The file could not be created or written.
    """
    ensure_parent(path)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, PRIVATE_FILE)
    with os.fdopen(fd, "a", encoding="utf-8") as handle:
        handle.write(line if line.endswith("\n") else line + "\n")


def replace_text(path: Path, text: str) -> None:
    """Replace ``path``'s content atomically: staged beside it, then swapped.

    An interrupted replace leaves the previous content intact.

    Raises:
        OSError: The file could not be written. The original is untouched.
    """
    ensure_parent(path)
    staging = path.with_name(path.name + ".staging")
    fd = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, PRIVATE_FILE)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        staging.replace(path)
    except OSError:
        staging.unlink(missing_ok=True)
        raise


def read_lines(path: Path) -> list[str]:
    """Non-empty lines of ``path``; an absent file has none.

    Raises:
        OSError: The file exists but cannot be read.
    """
    try:
        raw = path.read_text("utf-8", errors="replace")
    except FileNotFoundError:
        return []
    # Split on "\n" only, the one separator append_line writes. str.splitlines()
    # also breaks on U+2028, U+0085 and others, which JSON leaves unescaped, so a
    # file named with one of them tore its journal line in two.
    return [line for line in raw.split("\n") if line.strip()]


def keep_last_lines(path: Path, *, max_bytes: int, keep: int) -> None:
    """Bound an append-only file: past ``max_bytes``, keep its last ``keep`` lines.

    Raises:
        OSError: The file could not be rewritten. It is left as it was.
    """
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return
    if size <= max_bytes:
        return
    lines = read_lines(path)[-keep:]
    replace_text(path, "".join(line + "\n" for line in lines))
