"""Terminal presentation: ANSI colours, the ASCII logo and safe text.

Pure stdlib. Colour is auto-disabled when the stream is not a TTY, when
``NO_COLOR`` is set, or when ``TERM=dumb``, so piped/redirected output and the
``--json`` mode stay clean and parseable.

A file name is untrusted text (anyone can drop a file into Downloads), so
every human rendering of a name, a path or a message quoting one goes through
:func:`escape_for_terminal` before it is coloured or printed.
"""

from __future__ import annotations

import json
import os
from typing import Any, TextIO

from .. import __version__

#: Invisible characters kept as they are: the zero-width non-joiner and joiner
#: shape Persian or Indic words and compose emoji (a family, a flag), and a
#: terminal does not act on them.
_JOINERS = frozenset("\u200c\u200d")
#: Escapes shorter than their code, as Python writes them.
_SHORT_ESCAPES = {"\\": "\\\\", "\t": "\\t", "\n": "\\n", "\r": "\\r"}


def escape_for_terminal(text: str) -> str:
    """``text`` with every character a terminal could act on shown as an escape.

    A character with no glyph of its own (a control from C0, DEL or C1, a
    format character such as a bidi override or isolate, a line or paragraph
    separator, a space other than U+0020, a surrogate from an undecodable
    name, a private or unassigned code point) becomes a Python-style escape:
    ``\\t``, ``\\n``, ``\\r``, else ``\\xNN``, ``\\uNNNN`` or ``\\UNNNNNNNN``. A
    name holding ``ESC [2J`` then reads ``\\x1b[2J`` instead of clearing the
    screen, and a right-to-left override reads ``\\u202e`` instead of turning
    ``fdp.exe`` around. Every other character, accented letters, CJK, emoji,
    is left as it is, so ordinary names read as they are.

    A backslash is always doubled. Without that, a harmless file literally
    named ``a\\x1b`` would read exactly like a file holding a real ESC; with
    it, the escaped text decodes back to one text only (it is unambiguous).
    """
    if text.isprintable() and "\\" not in text:
        return text  # the common case: nothing to escape
    return "".join(_escape_character(ch) for ch in text)


def _escape_character(ch: str) -> str:
    if ch in _SHORT_ESCAPES:
        return _SHORT_ESCAPES[ch]
    if ch.isprintable() or ch in _JOINERS:
        return ch
    code = ord(ch)
    if code <= 0xFF:
        return f"\\x{code:02x}"
    if code <= 0xFFFF:
        return f"\\u{code:04x}"
    return f"\\U{code:08x}"


def dumps_for_terminal(value: Any) -> str:
    """``value`` as the indented JSON of a ``--json`` output."""
    return json.dumps(value, ensure_ascii=False, indent=2)


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
