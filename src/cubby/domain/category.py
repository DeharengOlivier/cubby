"""Value objects describing how files are routed.

These are plain, immutable-ish dataclasses with no IO. Loading them from a
config file is an adapter concern (see ``cubby.adapters.config``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .naming import safe_component

#: Month folder styles and the languages the "letters" style can be written in.
MONTH_STYLES = ("numeric", "letters")
MONTH_LANGS = ("fr", "en")


@dataclass(frozen=True)
class Category:
    """A destination folder and the rules that route files to it.

    A category can participate in several stages of the cascade at once:
    ``name_patterns`` feed stage 1, ``content_patterns`` (then
    ``late_content_patterns``) stage 2 and
    ``extensions`` stage 3. When ``strong_ext`` is set, its extensions become
    decisive at stage 0 (a ``.dmg`` is an installer whatever its name).
    """

    name: str
    name_patterns: tuple[str, ...] = ()
    content_patterns: tuple[str, ...] = ()
    # Stage 2 as well, but tried only once no category's content_patterns
    # matched: for a broad sign ("Facture" as a title) that a statement or a
    # contract can carry too, and whose own patterns must then win.
    late_content_patterns: tuple[str, ...] = ()
    extensions: frozenset[str] = frozenset()
    strong_ext: bool = False
    date_folders: bool = False  # file into a month/year subfolder (invoices, statements)
    vendor_rename: bool = False  # also rename to "<vendor> facture <date>" (invoices)

    def __post_init__(self) -> None:
        """Validate the name, which becomes a folder inside the watched tree.

        Raises:
            ValueError: The name is not a single safe folder component.
        """
        object.__setattr__(self, "name", safe_component(self.name, field="category name"))


@dataclass(frozen=True)
class Settings:
    """Runtime knobs, all overridable from config or the CLI."""

    source: Path = field(default_factory=lambda: Path.home() / "Downloads")
    delay: float = 60.0  # min age in seconds before a file is eligible
    interval: float = 30.0  # poll interval for watch mode, in seconds
    content_scan: bool = True
    content_max_bytes: int = 4000
    unsorted_dir: str = "_Unsorted"
    dedupe: bool = False  # drop byte-identical duplicates instead of keeping (1) copies
    skip_ext: frozenset[str] = frozenset(
        {"crdownload", "part", "download", "tmp", "partial", "opdownload"}
    )
    month_style: str = "numeric"  # month/year folder style: "numeric" (2026-07) or "letters"
    month_lang: str = "fr"  # language for the letters style: "fr" (juillet) or "en" (July)
    vendors: tuple[str, ...] = ()  # known vendor names, matched first when renaming invoices
    ignore: tuple[str, ...] = ()  # glob patterns (case-insensitive) of names never touched
    notify: bool = True  # desktop notification when the agent cannot sort

    def __post_init__(self) -> None:
        """Validate every setting, wherever it came from.

        Raises:
            ValueError: A setting is outside the range or set of values cubby
                can act on. The message names the setting.
        """
        object.__setattr__(
            self, "unsorted_dir", safe_component(self.unsorted_dir, field="unsorted_dir")
        )

        if self.delay < 0:
            raise ValueError(
                f"delay must not be negative, got {self.delay}. A negative delay "
                "makes a download that is still being written eligible to move."
            )
        if self.interval <= 0:
            raise ValueError(
                f"interval must be strictly positive, got {self.interval}. "
                "Watch mode sleeps for interval between passes; zero spins."
            )
        if self.content_max_bytes <= 0:
            raise ValueError(
                f"content_max_bytes must be strictly positive, got "
                f"{self.content_max_bytes}. Zero or less would read whole files "
                "into memory rather than the window cubby needs."
            )
        if self.month_style not in MONTH_STYLES:
            raise ValueError(
                f"month_style must be one of: {', '.join(MONTH_STYLES)}. Got {self.month_style!r}."
            )
        if self.month_lang not in MONTH_LANGS:
            raise ValueError(
                f"month_lang must be one of: {', '.join(MONTH_LANGS)}. Got {self.month_lang!r}."
            )

        object.__setattr__(self, "ignore", _check_ignore(self.ignore))

        source = Path(self.source)
        if source == Path(source.anchor) or source == Path.home():
            raise ValueError(
                f"source must be a folder to tidy, not {source}. cubby creates "
                "category folders inside it and moves what it finds there, which "
                "is not something to do to a whole home directory or a filesystem "
                "root."
            )


def _check_ignore(patterns: object) -> tuple[str, ...]:
    """Validate the ``ignore`` globs: a list of non-empty file-name patterns.

    Raises:
        ValueError: Not a list of strings, an empty pattern, or a path.
    """
    if isinstance(patterns, str) or not isinstance(patterns, (list, tuple)):
        raise ValueError(
            f"ignore must be a list of file-name patterns, got {patterns!r}. "
            'Example: ignore = ["*.torrent", "keep-*"]'
        )
    checked: list[str] = []
    for pattern in patterns:
        if not isinstance(pattern, str) or not pattern.strip():
            raise ValueError(f"ignore patterns must be non-empty text, got {pattern!r}.")
        if "/" in pattern:
            raise ValueError(
                f"ignore patterns match a file name, not a path, got {pattern!r}. "
                "Cubby only sorts the top level of its folder."
            )
        checked.append(pattern.strip())
    return tuple(checked)


@dataclass(frozen=True)
class Config:
    """The fully resolved configuration handed to the engine and use cases."""

    settings: Settings
    categories: tuple[Category, ...]

    @property
    def managed_dirs(self) -> frozenset[str]:
        """Folder names cubby owns and must never treat as input."""
        return frozenset(c.name for c in self.categories) | {self.settings.unsorted_dir}
