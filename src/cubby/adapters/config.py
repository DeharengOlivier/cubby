"""Load configuration from TOML into the domain ``Config``.

Resolution order, later winning:

  1. the packaged ``cubby/data/default.toml`` (generic categories)
  2. a user file (``~/.config/cubby/config.toml`` or ``$CUBBY_CONFIG``)
  3. explicit overrides passed by the CLI

``[settings]`` keys merge individually; a ``[[category]]`` list in the user file
*replaces* the defaults, so personal routing can be defined from scratch.
"""

from __future__ import annotations

import os
import re
import tomllib
from importlib.resources import files
from pathlib import Path

from ..domain.category import Category, Config, Settings
from ..domain.duration import parse_duration


def default_config_path() -> Path:
    """Locate the packaged default config, working in both source and wheel
    installs via importlib.resources."""
    return Path(str(files("cubby").joinpath("data/default.toml")))


def user_config_candidates() -> list[Path]:
    candidates: list[Path] = []
    env = os.environ.get("CUBBY_CONFIG")
    if env:
        candidates.append(Path(env).expanduser())
    candidates.append(Path.home() / ".config" / "cubby" / "config.toml")
    candidates.append(Path.home() / ".cubby.toml")
    return candidates


def find_user_config() -> Path | None:
    for candidate in user_config_candidates():
        if candidate.is_file():
            return candidate
    return None


def _load_toml(path: Path) -> dict:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _deep_merge(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _build_settings(raw: dict) -> Settings:
    defaults = Settings()
    skip = raw.get("skip_ext")
    return Settings(
        source=Path(os.path.expanduser(raw.get("source", str(defaults.source)))),
        delay=parse_duration(raw.get("delay", defaults.delay)),
        interval=parse_duration(raw.get("interval", defaults.interval)),
        content_scan=bool(raw.get("content_scan", defaults.content_scan)),
        content_max_bytes=int(raw.get("content_max_bytes", defaults.content_max_bytes)),
        unsorted_dir=raw.get("unsorted_dir", defaults.unsorted_dir),
        dedupe=bool(raw.get("dedupe", defaults.dedupe)),
        skip_ext=frozenset(e.lower().lstrip(".") for e in skip) if skip else defaults.skip_ext,
        month_style=str(raw.get("month_style", defaults.month_style)),
        month_lang=str(raw.get("month_lang", defaults.month_lang)),
        vendors=tuple(raw.get("vendors", defaults.vendors)),
    )


def _check_patterns(name: str, field: str, patterns: tuple[str, ...]) -> tuple[str, ...]:
    """Return ``patterns`` after checking each one compiles.

    Compiling here rather than when the engine is built turns a config typo into
    a message naming the category, instead of a raw re.error mid-run.

    Raises:
        ValueError: A pattern is not a valid regular expression.
    """
    for pattern in patterns:
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError(
                f"category {name!r} has an invalid {field} entry {pattern!r}: {exc}"
            ) from exc
    return patterns


def _build_category(raw: dict) -> Category:
    try:
        name = raw["name"]
    except KeyError:
        raise ValueError(f"a [[category]] entry has no name: {raw!r}") from None
    return Category(
        name=name,
        name_patterns=_check_patterns(
            name, "name_patterns", tuple(raw.get("name_patterns", ()))
        ),
        content_patterns=_check_patterns(
            name, "content_patterns", tuple(raw.get("content_patterns", ()))
        ),
        extensions=frozenset(e.lower().lstrip(".") for e in raw.get("extensions", ())),
        strong_ext=bool(raw.get("strong_ext", False)),
        date_folders=bool(raw.get("date_folders", False)),
        vendor_rename=bool(raw.get("vendor_rename", False)),
    )


def load_config(
    user_path: Path | None = None,
    overrides: dict | None = None,
    default_path: Path | None = None,
) -> Config:
    data = _load_toml(default_path or default_config_path())

    resolved_user = user_path or find_user_config()
    if resolved_user and resolved_user.is_file():
        data = _deep_merge(data, _load_toml(resolved_user))

    if overrides:
        data = _deep_merge(data, overrides)

    categories = tuple(_build_category(c) for c in data.get("category", ()))
    if not categories:
        raise ValueError("configuration defines no categories")
    return Config(settings=_build_settings(data.get("settings", {})), categories=categories)
