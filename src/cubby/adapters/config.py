"""Load configuration from TOML into the domain ``Config``.

Resolution order, later winning:

  1. the packaged ``cubby/data/default.toml`` (generic categories)
  2. a user file (``~/.config/cubby/config.toml`` or ``$CUBBY_CONFIG``)
  3. explicit overrides passed by the CLI

``[settings]`` keys merge individually; a ``[[category]]`` list in the user file
*replaces* the defaults, so personal routing can be defined from scratch.
"""

from __future__ import annotations

import difflib
import os
import re
import sys
import tomllib
from importlib.resources import files
from pathlib import Path
from typing import Any

from ..domain.category import Category, Config, Settings
from ..domain.duration import parse_duration
from . import state


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


def default_user_config_path() -> Path:
    """Where ``cubby init`` writes, and the first place looked for a config."""
    env = os.environ.get("CUBBY_CONFIG")
    return Path(env).expanduser() if env else Path.home() / ".config" / "cubby" / "config.toml"


_STARTER_HEADER = """\
# Cubby configuration, written by `cubby init`.
#
# Edit freely: categories are tried top to bottom within each stage, so put the
# most specific ones first. `cubby plan` previews the result and `cubby explain
# FILE` says which rule decides a given file. Reference: docs/configuration.md.
"""


def write_starter_config(path: Path, *, force: bool = False) -> Path:
    """Write a starter config (the packaged defaults, annotated) to ``path``.

    Raises:
        FileExistsError: ``path`` exists and ``force`` is false.
        OSError: The file could not be written.
    """
    text = _starter_text()
    path.parent.mkdir(parents=True, exist_ok=True)
    if force:
        # Staged then swapped: a failed write keeps the old file, and a symlink
        # at ``path`` is replaced rather than written through.
        state.replace_text(path, text)
        return path
    with path.open("x", encoding="utf-8") as handle:
        handle.write(text)
    return path


def _starter_text() -> str:
    body = default_config_path().read_text(encoding="utf-8")
    # Drop the packaged file's own header comment; the starter has its own.
    return _STARTER_HEADER + "\n" + body[body.index("[settings]") :]


def find_user_config() -> Path | None:
    for candidate in user_config_candidates():
        if candidate.is_file():
            return candidate
    return None


def _load_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        try:
            return tomllib.load(handle)
        except RecursionError:
            # The parser recurses per level of nesting: too deep is invalid TOML,
            # reported as such, not a traceback.
            raise _too_deep() from None


def _too_deep() -> tomllib.TOMLDecodeError:
    message = "nested too deep to read"
    if sys.version_info >= (3, 14):  # the one-argument form is deprecated there
        return tomllib.TOMLDecodeError(message, "", 0)
    return tomllib.TOMLDecodeError(message)


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


#: Every key a config file may use. Anything else is refused, with the nearest
#: known key suggested: a misspelt ``ignore`` silently dropped would let cubby
#: move the files it was meant to leave alone.
SETTINGS_KEYS = frozenset(
    {
        "source", "delay", "interval", "content_scan", "content_max_bytes", "unsorted_dir",
        "dedupe", "skip_ext", "month_style", "month_lang", "vendors", "ignore", "notify",
    }
)  # fmt: skip
CATEGORY_KEYS = frozenset(
    {
        "name", "name_patterns", "content_patterns", "extensions", "strong_ext",
        "date_folders", "vendor_rename",
    }
)  # fmt: skip
TOP_LEVEL_KEYS = frozenset({"settings", "category"})


def _check_keys(raw: dict[str, Any], known: frozenset[str], what: str) -> None:
    """Refuse a key cubby does not know, naming it and the closest known one.

    Raises:
        ValueError: ``raw`` holds a key outside ``known``.
    """
    for key in raw:
        if key in known:
            continue
        close = difflib.get_close_matches(key, sorted(known), n=1)
        hint = f"; did you mean {close[0]!r}?" if close else f". Known: {', '.join(sorted(known))}."
        raise ValueError(f"unknown {what.format(key=key)}{hint}")


def _switch(raw: dict[str, Any], key: str, default: bool, where: str) -> bool:
    """A true/false setting. ``bool("false")`` is true, so text is refused.

    Raises:
        ValueError: The value is not a TOML boolean.
    """
    value = raw.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"{key} {where}must be true or false, got {value!r}.")
    return value


def _build_settings(raw: dict[str, Any]) -> Settings:
    _check_keys(raw, SETTINGS_KEYS, "setting {key!r} in [settings]")
    defaults = Settings()
    skip = raw.get("skip_ext")
    return Settings(
        source=Path(raw.get("source", str(defaults.source))).expanduser(),
        delay=parse_duration(raw.get("delay", defaults.delay)),
        interval=parse_duration(raw.get("interval", defaults.interval)),
        content_scan=_switch(raw, "content_scan", defaults.content_scan, ""),
        content_max_bytes=int(raw.get("content_max_bytes", defaults.content_max_bytes)),
        unsorted_dir=raw.get("unsorted_dir", defaults.unsorted_dir),
        dedupe=_switch(raw, "dedupe", defaults.dedupe, ""),
        skip_ext=frozenset(e.lower().lstrip(".") for e in skip) if skip else defaults.skip_ext,
        month_style=str(raw.get("month_style", defaults.month_style)),
        month_lang=str(raw.get("month_lang", defaults.month_lang)),
        vendors=tuple(raw.get("vendors", defaults.vendors)),
        ignore=raw.get("ignore", defaults.ignore),
        notify=_switch(raw, "notify", defaults.notify, ""),
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


def _build_category(raw: dict[str, Any]) -> Category:
    try:
        name = raw["name"]
    except KeyError:
        raise ValueError(f"a [[category]] entry has no name: {raw!r}") from None
    _check_keys(raw, CATEGORY_KEYS, f"key {{key!r}} in category {name!r}")
    where = f"in category {name!r} "
    return Category(
        name=name,
        name_patterns=_check_patterns(name, "name_patterns", tuple(raw.get("name_patterns", ()))),
        content_patterns=_check_patterns(
            name, "content_patterns", tuple(raw.get("content_patterns", ()))
        ),
        extensions=frozenset(e.lower().lstrip(".") for e in raw.get("extensions", ())),
        strong_ext=_switch(raw, "strong_ext", False, where),
        date_folders=_switch(raw, "date_folders", False, where),
        vendor_rename=_switch(raw, "vendor_rename", False, where),
    )


def load_config(
    user_path: Path | None = None,
    overrides: dict[str, Any] | None = None,
    default_path: Path | None = None,
) -> Config:
    data = _load_toml(default_path or default_config_path())

    resolved_user = user_path or find_user_config()
    if resolved_user and resolved_user.is_file():
        data = _deep_merge(data, _load_toml(resolved_user))

    if overrides:
        data = _deep_merge(data, overrides)

    _check_keys(data, TOP_LEVEL_KEYS, "table or key {key!r}")
    categories = tuple(_build_category(c) for c in data.get("category", ()))
    if not categories:
        raise ValueError("configuration defines no categories")
    return Config(settings=_build_settings(data.get("settings", {})), categories=categories)
