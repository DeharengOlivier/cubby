"""Shared CLI plumbing: exit codes, config loading, argument types, output helpers."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from ..adapters.config import (
    find_user_config,
    load_config,
)
from ..adapters.logging import Level, LevelLogger
from ..adapters.pause import MAX_DURATION as MAX_PAUSE
from ..adapters.ui import Palette, supports_color
from ..domain.category import Config
from ..domain.duration import format_duration, parse_duration

#: Exit codes, named so callers and tests do not repeat the integers.
EXIT_OK = 0


EXIT_FAILED = 1


EXIT_BAD_CONFIG = 2


def palette() -> Palette:
    return Palette(supports_color(sys.stdout))


def build_overrides(args: argparse.Namespace) -> dict[str, Any]:
    settings: dict[str, Any] = {}
    if getattr(args, "source", None):
        settings["source"] = args.source
    if getattr(args, "delay", None) is not None:
        settings["delay"] = args.delay
    if getattr(args, "interval", None) is not None:
        settings["interval"] = args.interval
    if getattr(args, "no_content", False):
        settings["content_scan"] = False
    if getattr(args, "month_style", None):
        settings["month_style"] = args.month_style
    if getattr(args, "month_lang", None):
        settings["month_lang"] = args.month_lang
    return {"settings": settings} if settings else {}


def config_path_of(args: argparse.Namespace) -> Path | str:
    """The config file in play, for an error message."""
    if getattr(args, "config", None):
        return Path(args.config).expanduser()
    return find_user_config() or "the packaged defaults"


def load_from_args(args: argparse.Namespace) -> Config:
    """The configuration, with the command line's overrides.

    Raises:
        ValueError: The configuration is invalid, or ``--source`` names a folder
            cubby files into inside the watched folder (it would be sorted into
            itself, ``Documents/Documents``).
    """
    user_path = Path(args.config).expanduser() if getattr(args, "config", None) else None
    overrides = build_overrides(args)
    config = load_config(user_path=user_path, overrides=overrides)
    if getattr(args, "source", None):
        _refuse_a_managed_source(config, user_path, overrides)
    return config


class SourceFlagError(ValueError):
    """``--source`` names a folder cubby must not sort (a usage error, not a config one)."""


def _refuse_a_managed_source(
    config: Config, user_path: Path | None, overrides: dict[str, Any]
) -> None:
    """Refuse a ``--source`` that is a folder cubby files into, in the watched folder.

    Raises:
        SourceFlagError: It is one.
    """
    others = {key: value for key, value in overrides.get("settings", {}).items() if key != "source"}
    try:
        watched = load_config(
            user_path=user_path, overrides={"settings": others} if others else {}
        ).settings.source.resolve()
        chosen = config.settings.source.resolve()
    except ValueError:
        return  # the configured folder is unusable: --source replaces it, nothing to nest in
    except (OSError, RuntimeError):
        return  # a symlink loop (RuntimeError before 3.13): the source check reports it
    inside = _parts_below(chosen, watched)
    # Another folder inside the watched one (an inbox) is fine; a folder cubby
    # files into is not, whatever the case of its name on a case-insensitive disk.
    if inside and _is_managed(watched, inside[0], config.managed_dirs):
        raise SourceFlagError(
            f"--source {config.settings.source} is inside the watched folder {watched}, "
            f"in {inside[0]}/, a folder cubby files into: its files would be sorted into "
            f"{chosen.name}/{inside[0]}/... Sort {watched} instead."
        )


def _parts_below(chosen: Path, watched: Path) -> tuple[str, ...]:
    """The names leading from ``watched`` down to ``chosen``; empty if it is not below.

    Compared as folders, not as strings, so that on a case-insensitive disk
    ``~/downloads/Documents`` is found below ``~/Downloads``.
    """
    if chosen.is_relative_to(watched):
        return chosen.relative_to(watched).parts
    for parent in chosen.parents:
        try:
            if parent.exists() and watched.exists() and parent.samefile(watched):
                return chosen.relative_to(parent).parts
        except OSError:
            continue
    return ()


def _is_managed(watched: Path, name: str, managed: frozenset[str]) -> bool:
    if name in managed:
        return True
    entry = watched / name
    return any(
        (watched / folder).exists() and entry.exists() and entry.samefile(watched / folder)
        for folder in managed
    )


def require_source(config: Config, args: argparse.Namespace | None = None) -> str | None:
    """Return an error message if the source folder is unusable, else None."""
    source = config.settings.source
    origin = ""
    if args is not None:
        from_flag = getattr(args, "source", None)
        origin = " (--source)" if from_flag else f" (source in {config_path_of(args)})"
    if not source.exists():
        return f"source folder does not exist: {source}{origin}"
    if not source.is_dir():
        return f"source is not a folder: {source}{origin}"
    return None


def source_error(config: Config, args: argparse.Namespace | None = None) -> bool:
    """Print why the source folder is unusable, if it is. True means stop."""
    if error := require_source(config, args):
        print(f"cubby: {error}", file=sys.stderr)
        return True
    return False


def make_loud(log: LevelLogger) -> LevelLogger:
    """A logger whose warnings and errors also reach stderr, whatever the verbosity."""

    def log_and_tell(message: str, *, level: Level = "INFO") -> None:
        log(message, level=level)
        if level != "INFO":
            print(f"cubby: {level.lower()}: {message}", file=sys.stderr)

    return log_and_tell


def at_least_one(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1, got {number}")
    return number


def interval_text(value: str) -> str:
    """``--interval``: a duration above zero, checked here so an error names the flag."""
    if duration_text(value) and parse_duration(value) <= 0:
        raise argparse.ArgumentTypeError(f"must be above zero, such as 30s, got {value!r}")
    return value


def duration_text(value: str) -> str:
    """A duration flag, checked here so a bad one names the flag (and is exit 2)."""
    try:
        parse_duration(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from None
    return value


def positive_duration(value: str) -> float:
    try:
        seconds = parse_duration(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from None
    if seconds <= 0:
        raise argparse.ArgumentTypeError(f"must be a positive duration such as 2h, got {value!r}")
    if seconds > MAX_PAUSE:
        raise argparse.ArgumentTypeError(
            f"must be at most 366d, got {value!r}; to pause until you resume, omit --for"
        )
    return seconds


def kv(pal: Palette, key: str, value: str) -> None:
    # A key longer than the column still gets two spaces before its value.
    print(f"{pal.dim(key.ljust(max(16, len(key) + 2)))}{value}")


def format_features(pal: Palette, mapping: dict[str, bool]) -> str:
    parts = [
        pal.green(f"{name} ok") if present else pal.dim(f"{name} -")
        for name, present in mapping.items()
    ]
    return "  ".join(parts)


def format_age(seconds: float) -> str:
    return format_duration(max(0, round(seconds))) + " ago"
