"""A tiny append-only file logger. Never raises; logging must not break a sort.

Lines carry a level because the file is read after something has gone wrong,
and the one line that matters ("your files moved and the undo journal could not
be written") must be findable among the hundreds saying a file was filed.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

DEFAULT_LOG = Path.home() / "Library" / "Logs" / "cubby.log"


def file_logger(path: Path | None = None, *, echo: bool = False) -> Callable[[str], None]:
    """Return a logger appending to ``path`` (the default log when omitted).

    The default is read when the logger is built, not when this module is
    imported, so a test can redirect it away from the user's real log file.
    """
    destination = path or DEFAULT_LOG

    def log(message: str, *, level: str = "INFO") -> None:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"{stamp}  {level:<7} {message}"
        if echo:
            print(line)
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            pass

    return log
