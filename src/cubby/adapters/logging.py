"""A tiny append-only file logger. Never raises; logging must not break a sort."""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path

DEFAULT_LOG = Path.home() / "Library" / "Logs" / "cubby.log"


def file_logger(path: Path = DEFAULT_LOG, *, echo: bool = False) -> Callable[[str], None]:
    def log(message: str) -> None:
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"{stamp}  {message}"
        if echo:
            print(line)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            pass

    return log
