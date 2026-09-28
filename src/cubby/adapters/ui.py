"""Terminal presentation: ANSI colours, the ASCII logo and safe text.

Pure stdlib. Colour is auto-disabled when the stream is not a TTY, when
``NO_COLOR`` is set, or when ``TERM=dumb``, so piped/redirected output and the
``--json`` mode stay clean and parseable.

A file name is untrusted text (anyone can drop a file into Downloads), so
every human rendering of a name, a path or a message quoting one goes through
:func:`escape_for_terminal` before it is coloured or printed.

What the escapers return is typed :data:`Shown`, which mypy tracks through
the palette, ``kv`` and the renderers; ``tests/test_output_escaping.py``
checks that nothing else reaches a ``print``.
"""

from __future__ import annotations

import json
import os
import re
import unicodedata
from typing import Any, NewType, TextIO, TypeVar, overload

from .. import __version__

#: Text safe to show on a terminal: escaped, or built only from escaped text
#: and cubby's own words. Only the escapers below make one from any text;
#: elsewhere ``Shown(...)`` wraps what the AST check can vouch for.
Shown = NewType("Shown", str)
#: Palette styling keeps text ``Shown``: its codes are cubby's own.
_Text = TypeVar("_Text", Shown, str)

#: Invisible characters kept as they are, since a terminal does not act on
#: them: the zero-width non-joiner and joiner shape Persian or Indic words and
#: compose emoji (a family), and the tag characters spell the subdivision of a
#: flag (England, Scotland, Wales). Spaces (category Zs) are kept too: macOS
#: puts U+202F in a screenshot's name, and Option+Space types U+00A0.
_JOINERS = frozenset("\u200c\u200d")
_TAGS = range(0xE0020, 0xE0080)
#: Escapes shorter than their code, as Python writes them.
_SHORT_ESCAPES = {"\\": "\\\\", "\t": "\\t", "\n": "\\n", "\r": "\\r"}
#: Where a character that is not plain ASCII can hide in a JSON text.
_NOT_ASCII = re.compile(r"[^\x00-\x7e]")


def is_harmless(ch: str) -> bool:
    """Whether a terminal shows ``ch`` as text: it has a glyph, or it is one of
    the invisible characters that only join, tag or space the glyphs around it."""
    return (
        ch.isprintable() or ch in _JOINERS or ord(ch) in _TAGS or unicodedata.category(ch) == "Zs"
    )


def escape_for_terminal(text: str) -> Shown:
    """``text`` with every character a terminal could act on shown as an escape.

    A character that is not :func:`is_harmless` (a control from C0, DEL or C1,
    a format character such as a bidi override or isolate, a line or paragraph
    separator, a surrogate from an undecodable name, a private or unassigned
    code point) becomes a Python-style escape: ``\\t``, ``\\n``, ``\\r``, else
    ``\\xNN``, ``\\uNNNN`` or ``\\UNNNNNNNN``. A name holding ``ESC [2J`` then
    reads ``\\x1b[2J`` instead of clearing the screen, and a right-to-left
    override reads ``\\u202e`` instead of turning ``fdp.exe`` around. Every
    other character, accented letters, CJK, emoji, spaces, is left as it is,
    so ordinary names read as they are.

    A backslash is always doubled. Without that, a harmless file literally
    named ``a\\x1b`` would read exactly like a file holding a real ESC; with
    it, the escaped text decodes back to one text only (it is unambiguous).
    """
    if text.isprintable() and "\\" not in text:
        return Shown(text)  # the common case: nothing to escape
    return Shown("".join(_escape_character(ch) for ch in text))


def _escape_character(ch: str) -> str:
    if ch in _SHORT_ESCAPES:
        return _SHORT_ESCAPES[ch]
    if is_harmless(ch):
        return ch
    code = ord(ch)
    if code <= 0xFF:
        return f"\\x{code:02x}"
    if code <= 0xFFFF:
        return f"\\u{code:04x}"
    return f"\\U{code:08x}"


def os_error_text(error: OSError) -> str:
    """What ``str(error)`` says, with the file names as they are.

    Python quotes the file names of an ``OSError`` with ``repr()``, which
    escapes their controls in its own way; :func:`escape_for_terminal` would
    then double those backslashes, and a real ESC would read ``\\\\x1b``, the
    way a name holding a literal backslash reads. The names are kept raw here
    so that the one escaping, done where the text is shown, applies once.
    """
    if error.strerror is None:
        return str(error)
    text = error.strerror if error.errno is None else f"[Errno {error.errno}] {error.strerror}"
    names = [_file_name(name) for name in (error.filename, error.filename2) if name is not None]
    return f"{text}: {' -> '.join(names)}" if names else text


def _file_name(name: object) -> str:
    return f"'{os.fsdecode(name) if isinstance(name, str | bytes) else name}'"


def dumps_for_terminal(value: Any) -> Shown:
    """``value`` as the indented JSON of a ``--json`` output, safe to print.

    The encoder escapes C0 controls only (``ensure_ascii`` would escape every
    accent and ideogram too). Every other character that is not
    :func:`is_harmless` (DEL, C1, bidi and other format characters, line
    separators, lone surrogates) is written as ``\\uXXXX``, a pair of them
    above U+FFFF, so a ``--json`` output printed to a terminal cannot drive it
    either, and still decodes to the same values (except a lone high
    surrogate right before a lone low one, which JSON reads as the pair they
    spell; file names never hold one). Such characters can only be
    inside a JSON string: the rest of the text is ASCII.
    """
    return Shown(_NOT_ASCII.sub(_json_escape, json.dumps(value, ensure_ascii=False, indent=2)))


def _json_escape(match: re.Match[str]) -> str:
    ch = match.group()
    if is_harmless(ch):
        return ch
    code = ord(ch)
    if code <= 0xFFFF:
        return f"\\u{code:04x}"
    code -= 0x10000
    return f"\\u{0xD800 + (code >> 10):04x}\\u{0xDC00 + (code & 0x3FF):04x}"


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

    @overload
    def _wrap(self, name: str, text: Shown) -> Shown: ...
    @overload
    def _wrap(self, name: str, text: str) -> str: ...
    def _wrap(self, name: str, text: str) -> str:
        if not self.enabled:
            return text
        return f"\033[{_CODES[name]}m{text}{_RESET}"

    def bold(self, text: _Text) -> _Text:
        return self._wrap("bold", text)

    def dim(self, text: _Text) -> _Text:
        return self._wrap("dim", text)

    def accent(self, text: _Text) -> _Text:
        return self._wrap("accent", text)

    def cyan(self, text: _Text) -> _Text:
        return self._wrap("cyan", text)

    def green(self, text: _Text) -> _Text:
        return self._wrap("green", text)

    def yellow(self, text: _Text) -> _Text:
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


def banner(palette: Palette) -> Shown:
    side = [
        "",
        palette.bold(palette.accent("cubby")),
        palette.dim("tidy your downloads, automatically"),
        "",
        palette.dim(f"v{escape_for_terminal(__version__)}"),
    ]
    lines = [""]
    for art, text in zip(_SHELF, side, strict=True):
        shelf = palette.accent(art)
        lines.append(f"  {shelf}   {text}".rstrip())
    lines.append("")
    return Shown("\n".join(lines))
