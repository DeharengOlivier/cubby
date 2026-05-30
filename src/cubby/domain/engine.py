"""The classification cascade.

Stages, in order; the first to yield a category wins:

  0. strong extension - a ``.dmg`` is an installer whatever it is called
  1. filename patterns - fast and high precision
  2. content patterns  - parse the file when the name says nothing
  3. type / extension  - generic fallback (Images, Video, ...)
  else unsorted

Within each stage, categories are tried in config order, so list specific ones
first.
"""

from __future__ import annotations

import re

from .category import Config
from .file_ref import Decision, FileRef, Stage

_CompiledRules = list[tuple[str, list[re.Pattern[str]]]]

# A stem made only of a UUID or a long digit run carries no human signal. We
# skip the filename stage for these so a spurious substring (say "ad" inside a
# hex id) cannot hijack the routing; content/type still get their chance.
_CRYPTIC = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}|^\d{8,}$|^[0-9a-f]{16,}$",
    re.IGNORECASE,
)


class Engine:
    def __init__(self, config: Config):
        self._config = config
        self._name: _CompiledRules = [
            (c.name, [re.compile(p, re.IGNORECASE) for p in c.name_patterns])
            for c in config.categories
            if c.name_patterns
        ]
        self._content: _CompiledRules = [
            (c.name, [re.compile(p, re.IGNORECASE) for p in c.content_patterns])
            for c in config.categories
            if c.content_patterns
        ]
        self._strong_ext: dict[str, str] = {}
        self._ext: list[tuple[str, frozenset[str]]] = []
        for category in config.categories:
            if category.extensions:
                self._ext.append((category.name, category.extensions))
                if category.strong_ext:
                    for ext in category.extensions:
                        self._strong_ext.setdefault(ext, category.name)

    @staticmethod
    def _first_match(text: str, rules: _CompiledRules) -> str | None:
        for name, patterns in rules:
            if any(p.search(text) for p in patterns):
                return name
        return None

    def classify(self, ref: FileRef) -> Decision:
        ext = ref.ext

        # Stage 0: decisive extensions.
        if ext in self._strong_ext:
            return Decision(self._strong_ext[ext], Stage.STRONG_EXT)

        # Stage 1: filename (skipped for cryptic stems).
        if not _CRYPTIC.match(ref.stem):
            cat = self._first_match(ref.name, self._name)
            if cat:
                return Decision(cat, Stage.NAME)

        # Stage 2: content.
        if self._config.settings.content_scan and self._content and ref.is_file:
            cat = self._first_match(ref.text(), self._content)
            if cat:
                return Decision(cat, Stage.CONTENT)

        # Stage 3: type fallback.
        for name, extensions in self._ext:
            if ext in extensions:
                return Decision(name, Stage.TYPE)

        return Decision(self._config.settings.unsorted_dir, Stage.UNSORTED)
