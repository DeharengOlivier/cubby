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
    user_path = Path(args.config).expanduser() if getattr(args, "config", None) else None
    return load_config(user_path=user_path, overrides=build_overrides(args))


def require_source(config: Config) -> str | None:
    """Return an error message if the source folder is unusable, else None."""
    source = config.settings.source
    if not source.exists():
        return f"source folder does not exist: {source}"
    if not source.is_dir():
        return f"source is not a folder: {source}"
    return None


def source_error(config: Config) -> bool:
    """Print why the source folder is unusable, if it is. True means stop."""
    if error := require_source(config):
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
    print(f"{pal.dim(key.ljust(16))}{value}")


def format_features(pal: Palette, mapping: dict[str, bool]) -> str:
    parts = [
        pal.green(f"{name} ok") if present else pal.dim(f"{name} -")
        for name, present in mapping.items()
    ]
    return "  ".join(parts)


def format_age(seconds: float) -> str:
    return format_duration(max(0, round(seconds))) + " ago"
