"""One cubby at a time: an advisory lock around every pass and every undo.

The agent sorts every minute, and the user may type ``cubby run`` or
``cubby undo`` at any moment. Two passes over the same folder would race on
the same files and interleave their journal entries; an undo during a pass
could put back a file the pass is about to move again. The lock makes each
pass and each undo exclusive. It is held for one pass, never for the agent's
lifetime, so a manual command waits a few seconds at most.

``flock`` is released by the kernel when the process dies, so a crash never
leaves the lock stuck.
"""

from __future__ import annotations

import fcntl
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from . import state


class Busy(OSError):
    """Another cubby process held the lock for longer than we were willing to wait."""


def default_lock_path() -> Path:
    return state.state_dir() / "cubby.lock"


@contextmanager
def exclusive(
    path: Path | None = None, *, timeout: float = 60.0, poll: float = 0.1
) -> Iterator[None]:
    """Hold the cubby lock for the duration of the ``with`` block.

    Raises:
        Busy: The lock was not free within ``timeout`` seconds.
        OSError: The lock file could not be created.
    """
    lock_path = path or default_lock_path()
    state.ensure_parent(lock_path)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, state.PRIVATE_FILE)
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise Busy(
                        f"another cubby process is sorting or undoing (lock {lock_path}); "
                        "try again in a moment"
                    ) from None
                time.sleep(poll)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
