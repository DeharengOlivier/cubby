"""Regression battery: a setting that cannot work is refused, not absorbed.

Measured before this was written, all from a plausible typo in config.toml:

- ``content_max_bytes = -1`` read a whole 5 MB file, because read(-1) means
  "read everything" in Python. One character in a config file defeated the
  bound on how much cubby reads.
- ``month_style = "nonsense"`` silently produced numeric folders, and
  ``month_lang = "de"`` silently produced French ones.
- ``delay = -5`` made every file eligible immediately, including downloads
  still being written.
- ``interval = 0`` turned watch mode into a spin loop.
- ``source = "/"`` was accepted, pointing an automatic file mover at the root
  of the filesystem.

A configuration file is an input boundary. It is parsed into validated values
once, and trusted afterwards.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from cubby.adapters.config import load_config
from cubby.domain.category import Settings


def _config_with(tmp_path: Path, settings_body: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(
        textwrap.dedent(f"""
            [settings]
            {settings_body}

            [[category]]
            name = "Documents"
            extensions = ["pdf"]
        """),
        encoding="utf-8",
    )
    return path


# --- the bound on reading cannot be turned off ------------------------------


@pytest.mark.parametrize("value", [-1, 0])
def test_content_max_bytes_must_be_positive(value):
    with pytest.raises(ValueError, match="content_max_bytes"):
        Settings(content_max_bytes=value)


def test_content_max_bytes_is_checked_when_loaded(tmp_path):
    with pytest.raises(ValueError, match="content_max_bytes"):
        load_config(user_path=_config_with(tmp_path, "content_max_bytes = -1"))


# --- timings that make no sense ---------------------------------------------


def test_a_negative_delay_is_refused():
    # A negative delay makes an in-progress download eligible immediately.
    with pytest.raises(ValueError, match="delay"):
        Settings(delay=-5)


def test_no_delay_at_all_is_allowed():
    assert Settings(delay=0).delay == 0


def test_a_non_positive_interval_is_refused():
    # Watch mode sleeps for interval between passes; zero is a spin loop.
    with pytest.raises(ValueError, match="interval"):
        Settings(interval=0)


# --- enums that were silently ignored ---------------------------------------


def test_an_unknown_month_style_is_refused():
    with pytest.raises(ValueError, match="month_style"):
        Settings(month_style="nonsense")


def test_an_unknown_month_language_is_refused():
    with pytest.raises(ValueError, match="month_lang"):
        Settings(month_lang="de")


@pytest.mark.parametrize(("style", "lang"), [("numeric", "fr"), ("letters", "en")])
def test_the_supported_combinations_are_accepted(style, lang):
    settings = Settings(month_style=style, month_lang=lang)
    assert settings.month_style == style


# --- where cubby is pointed -------------------------------------------------


def test_the_filesystem_root_is_refused():
    with pytest.raises(ValueError, match="source"):
        Settings(source=Path("/"))


def test_the_home_directory_itself_is_refused():
    # cubby creates category folders inside source and moves everything it
    # finds there; the whole home directory is not a Downloads folder.
    with pytest.raises(ValueError, match="source"):
        Settings(source=Path.home())


def test_an_ordinary_folder_is_accepted(tmp_path):
    assert Settings(source=tmp_path / "Downloads").source == tmp_path / "Downloads"


# --- patterns that cannot compile -------------------------------------------


def test_a_broken_pattern_is_reported_with_its_category(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        textwrap.dedent("""
            [[category]]
            name = "Invoices"
            name_patterns = ["invoice(unclosed"]
        """),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Invoices"):
        load_config(user_path=path)


@pytest.mark.parametrize("key", ["content_patterns", "late_content_patterns"])
def test_a_broken_content_pattern_is_reported_too(tmp_path, key):
    path = tmp_path / "config.toml"
    path.write_text(
        textwrap.dedent(f"""
            [[category]]
            name = "Legal"
            {key} = ["[unterminated"]
        """),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Legal"):
        load_config(user_path=path)


def test_the_shipped_default_configuration_is_valid():
    # The defaults must satisfy every rule above, or none of this is credible.
    config = load_config(user_path=None)
    assert config.settings.delay > 0
    assert config.categories
