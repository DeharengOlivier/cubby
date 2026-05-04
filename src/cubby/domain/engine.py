"""The classification cascade.

Stages, in order; the first to yield a category wins:

  0. strong extension - a ``.dmg`` is an installer whatever it is called
  3. type / extension  - generic fallback (Images, Video, ...)
  else unsorted

Stages 1 (filename) and 2 (content) are added in later revisions. Within each
stage, categories are tried in config order, so list specific ones first.
"""
from __future__ import annotations

import re

from .category import Config
from .file_ref import Decision, FileRef, Stage


class Engine:
    def __init__(self, config: Config):
        self._config = config
        self._strong_ext: dict[str, str] = {}
        self._ext: list[tuple[str, frozenset[str]]] = []
        for category in config.categories:
            if category.extensions:
                self._ext.append((category.name, category.extensions))
                if category.strong_ext:
                    for ext in category.extensions:
                        self._strong_ext.setdefault(ext, category.name)

    def classify(self, ref: FileRef) -> Decision:
        ext = ref.ext

        # Stage 0: decisive extensions.
        if ext in self._strong_ext:
            return Decision(self._strong_ext[ext], Stage.STRONG_EXT)

        # Stage 3: type fallback.
        for name, extensions in self._ext:
            if ext in extensions:
                return Decision(name, Stage.TYPE)

        return Decision(self._config.settings.unsorted_dir, Stage.UNSORTED)
