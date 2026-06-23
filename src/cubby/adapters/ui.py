"""Terminal presentation: ANSI colours and the ASCII logo.

Pure stdlib. Colour is auto-disabled when the stream is not a TTY, when
``NO_COLOR`` is set, or when ``TERM=dumb``, so piped/redirected output and the
``--json`` mode stay clean and parseable.
"""

from __future__ import annotations

import os
from typing import TextIO

from .. import __version__

_RESET = "\033[0m"
_CODES = {
    "bold": "1",
    "dim": "2",
    "accent": "38;5;105",  # indigo, matches the logo
    "cyan": "38;5;44",
    "green": "38;5;42",
    "yellow": "38;5;179",
}


def supports_color(stream: TextIO) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    return bool(getattr(stream, "isatty", lambda: False)())


class Palette:
    """Style helpers that become no-ops when colour is disabled."""

    def __init__(self, enabled: bool):
        self.enabled = enabled

    def _wrap(self, name: str, text: str) -> str:
        if not self.enabled:
            return text
        return f"\033[{_CODES[name]}m{text}{_RESET}"

    def bold(self, text: str) -> str:
        return self._wrap("bold", text)

    def dim(self, text: str) -> str:
        return self._wrap("dim", text)

    def accent(self, text: str) -> str:
        return self._wrap("accent", text)

    def cyan(self, text: str) -> str:
        return self._wrap("cyan", text)

    def green(self, text: str) -> str:
        return self._wrap("green", text)

    def yellow(self, text: str) -> str:
        return self._wrap("yellow", text)


# The 2x2 shelf from the logo, with two filed items. Box art on the left,
# styled text on the right, so each is coloured independently.
_SHELF = [
    "╭───┬───╮",
    "│   │ ▪ │",
    "├───┼───┤",
    "│ ▪ │   │",
    "╰───┴───╯",
]


def banner(palette: Palette) -> str:
    side = [
        "",
        palette.bold(palette.accent("cubby")),
        palette.dim("tidy your downloads, automatically"),
        "",
        palette.dim(f"v{__version__}"),
    ]
    lines = [""]
    for art, text in zip(_SHELF, side, strict=True):
        shelf = palette.accent(art)
        lines.append(f"  {shelf}   {text}".rstrip())
    lines.append("")
    return "\n".join(lines)
