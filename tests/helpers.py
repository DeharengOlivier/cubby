"""Builders shared by the tests, so a change to ``Config`` or ``Settings`` is
made once here instead of in every file that needs a small configuration."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from cubby.domain.category import Category, Config, Settings

#: The smallest useful rule set: every ``.txt`` file is a document.
TEXT_DOCUMENTS = Category(name="Documents", extensions=frozenset({"txt"}))


def config_for(source: Path, *categories: Category, **settings: Any) -> Config:
    """A configuration sorting ``source`` at once, without reading file contents.

    Categories default to :data:`TEXT_DOCUMENTS`; ``settings`` override the
    defaults (``delay=0``, ``content_scan=False``).
    """
    base: dict[str, Any] = {"source": source, "delay": 0, "content_scan": False}
    base.update(settings)
    return Config(settings=Settings(**base), categories=categories or (TEXT_DOCUMENTS,))


def aged_file(folder: Path, name: str, content: str = "x", age: float = 10_000) -> Path:
    """Write ``name`` in ``folder`` (created if needed), dated ``age`` seconds back."""
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(content, encoding="utf-8")
    past = time.time() - age
    os.utime(path, (past, past))
    return path
