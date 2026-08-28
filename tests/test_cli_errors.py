"""Tests for how the CLI reports a configuration it cannot use.

Settings are now validated, which is only half the job: a user who mistypes
`interval = 0` was shown a Python traceback, which tells them nothing about
which line of their config file to fix. Exit codes: 0 success, 1 a run that
failed, 2 a configuration that cannot be used.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from cubby.cli import EXIT_BAD_CONFIG, EXIT_FAILED, EXIT_OK, main


def _write_config(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


def _plan(config: Path, source: Path | None = None) -> int:
    """Run `cubby plan` against an explicit config, never the developer's own."""
    argv = ["plan", "--config", str(config)]
    if source is not None:
        argv += ["--source", str(source)]
    return main(argv)


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("[settings]\ninterval = 0\n[[category]]\nname = 'Documents'\n", "interval"),
        ("[settings]\ndelay = -5\n[[category]]\nname = 'Documents'\n", "delay"),
        ("[settings]\nmonth_lang = 'de'\n[[category]]\nname = 'Documents'\n", "month_lang"),
        ("[[category]]\nname = '../escaped'\n", "name"),
        ("[[category]]\nname = 'Invoices'\nname_patterns = ['(unclosed']\n", "Invoices"),
        ("[[category]]\nextensions = ['pdf']\n", "name"),
    ],
)
def test_a_bad_config_is_explained_not_raised(body, expected, tmp_path, capsys):
    source = tmp_path / "downloads"
    source.mkdir()

    assert _plan(_write_config(tmp_path, body), source) == EXIT_BAD_CONFIG

    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert expected in captured.err
    assert "config" in captured.err.lower()


def test_a_malformed_toml_file_is_explained(tmp_path, capsys):
    path = tmp_path / "config.toml"
    path.write_text("[settings\nthis is not toml", encoding="utf-8")

    assert _plan(path) == EXIT_BAD_CONFIG

    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert str(path) in captured.err


def test_an_unreadable_config_file_is_explained(tmp_path, capsys):
    path = tmp_path / "config.toml"
    path.write_text("[[category]]\nname = 'Documents'\n", encoding="utf-8")
    path.chmod(0o000)
    try:
        assert _plan(path) == EXIT_FAILED
    finally:
        path.chmod(0o600)
    assert "Traceback" not in capsys.readouterr().err


def test_a_valid_config_still_runs(tmp_path, capsys):
    source = tmp_path / "downloads"
    source.mkdir()
    config = _write_config(
        tmp_path,
        """
        [[category]]
        name = "Documents"
        extensions = ["pdf"]
        """,
    )

    assert _plan(config, source) == EXIT_OK
    assert capsys.readouterr().err == ""
