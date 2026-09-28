"""A config file is read strictly: a key cubby does not know is an error.

Measured before the change: ``ignor = ["*.pdf"]`` (a typo of ``ignore``) was
dropped without a word and the PDFs it was meant to protect were moved;
``dedupe = "false"`` turned deduplication on, because ``bool("false")`` is true.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from cubby.adapters.config import load_config
from cubby.cli import EXIT_BAD_CONFIG, main


def _load(tmp_path, text: str):
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return load_config(user_path=path)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (
            '[settings]\nignor = ["*.pdf"]\n',
            "unknown setting 'ignor' in [settings]; did you mean 'ignore'?",
        ),
        (
            '[[category]]\nname = "Docs"\nextention = ["pdf"]\n',
            "unknown key 'extention' in category 'Docs'; did you mean 'extensions'?",
        ),
        ('[setting]\ndelay = "1m"\n', "unknown table or key 'setting'; did you mean 'settings'?"),
        ("[settings]\nzzz = 1\n", "unknown setting 'zzz' in [settings]"),
    ],
)
def test_an_unknown_key_is_named_with_a_suggestion(tmp_path, text, message):
    with pytest.raises(ValueError, match="^" + re.escape(message)):
        _load(tmp_path, text)


@pytest.mark.parametrize(
    "text",
    [
        '[settings]\ndedupe = "false"\n',
        "[settings]\ncontent_scan = 0\n",
        '[settings]\nnotify = "no"\n',
        '[[category]]\nname = "Docs"\nstrong_ext = "yes"\n',
    ],
)
def test_a_switch_must_be_true_or_false(tmp_path, text):
    with pytest.raises(ValueError, match="must be true or false"):
        _load(tmp_path, text)


def test_the_cli_reports_it_as_a_config_error(tmp_path, capsys):
    path = tmp_path / "config.toml"
    path.write_text('[settings]\nignor = ["*.pdf"]\n', encoding="utf-8")
    assert main(["plan", "--config", str(path), "--source", str(tmp_path)]) == EXIT_BAD_CONFIG
    assert "did you mean 'ignore'" in capsys.readouterr().err


def test_every_documented_key_is_accepted(tmp_path):
    config = _load(
        tmp_path,
        """
[settings]
source = "~/Downloads"
delay = "1m"
interval = "30s"
content_scan = true
content_max_bytes = 4000
unsorted_dir = "_Unsorted"
dedupe = false
skip_ext = ["crdownload"]
month_style = "numeric"
month_lang = "fr"
vendors = ["spotify"]
ignore = ["*.torrent"]
notify = true

[[category]]
name = "Invoices"
name_patterns = ["invoice"]
content_patterns = ["total"]
extensions = ["pdf"]
strong_ext = false
date_folders = true
vendor_rename = true
""",
    )
    assert config.settings.notify is True


@pytest.mark.parametrize(
    "example", sorted((Path(__file__).parent.parent / "examples").glob("*.toml")), ids=str
)
def test_the_shipped_examples_still_load(example):
    assert load_config(user_path=example).categories
