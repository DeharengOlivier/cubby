"""Value objects describing how files are routed.

These are plain, immutable-ish dataclasses with no IO. Loading them from a
config file is an adapter concern (see ``cubby.adapters.config``).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Category:
    """A destination folder and the rules that route files to it.

    A category can participate in several stages of the cascade at once:
    ``name_patterns`` feed stage 1, ``content_patterns`` stage 2 and
    ``extensions`` stage 3. When ``strong_ext`` is set, its extensions become
    decisive at stage 0 (a ``.dmg`` is an installer whatever its name).
    """

    name: str
    name_patterns: tuple[str, ...] = ()
    content_patterns: tuple[str, ...] = ()
    extensions: frozenset[str] = frozenset()
    strong_ext: bool = False


@dataclass(frozen=True)
class Settings:
    """Runtime knobs, all overridable from config or the CLI."""

    source: Path = field(default_factory=lambda: Path.home() / "Downloads")
    delay: float = 60.0            # min age in seconds before a file is eligible
    interval: float = 30.0         # poll interval for watch mode, in seconds
    content_scan: bool = True
    content_max_bytes: int = 4000
    unsorted_dir: str = "_Unsorted"
    skip_ext: frozenset[str] = frozenset(
        {"crdownload", "part", "download", "tmp", "partial", "opdownload"}
    )


@dataclass(frozen=True)
class Config:
    """The fully resolved configuration handed to the engine and use cases."""

    settings: Settings
    categories: tuple[Category, ...]

    @property
    def managed_dirs(self) -> frozenset[str]:
        """Folder names cubby owns and must never treat as input."""
        return frozenset(c.name for c in self.categories) | {self.settings.unsorted_dir}
