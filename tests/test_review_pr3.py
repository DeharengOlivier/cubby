"""Reproducers for the independent review of pull request 3.

Each test failed on the reviewed commit; the review record is on the pull
request.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from cubby.adapters import config as config_module
from cubby.adapters.config import default_config_path, load_config
from cubby.adapters.config import find_user_config as real_find_user_config
from cubby.adapters.journal import Entry, Journal
from cubby.adapters.ledger import Ledger, RunRecord
from cubby.cli import EXIT_FAILED, EXIT_OK, main

CONFIG = """
[settings]
delay = "1m"
ignore = ["*.torrent"]

[[category]]
name = "Invoices"
name_patterns = ["invoice"]
vendor_rename = true

[[category]]
name = "Installers"
extensions = ["dmg"]
strong_ext = true

[[category]]
name = "Documents"
extensions = ["pdf", "txt"]
"""


@pytest.fixture
def setup(tmp_path) -> tuple[Path, Path]:
    source = tmp_path / "Downloads"
    source.mkdir()
    config = tmp_path / "config.toml"
    config.write_text(CONFIG, encoding="utf-8")
    return source, config


def _old(path: Path, text: str = "x") -> Path:
    path.write_text(text, encoding="utf-8")
    past = time.time() - 3600
    os.utime(path, (past, past), follow_symlinks=False)
    return path


def _json(capsys, argv: list[str]) -> dict:
    capsys.readouterr()
    main(argv)
    return json.loads(capsys.readouterr().out)


# --- 1. explain agrees with a run about symlinks -----------------------------


@pytest.mark.parametrize("dangling", [False, True])
def test_explain_and_plan_agree_on_a_symlink_in_the_folder(setup, tmp_path, capsys, dangling):
    source, config = setup
    target = tmp_path / "elsewhere" / "report.dmg"
    target.parent.mkdir()
    if not dangling:
        _old(target)
    link = source / "link.dmg"
    link.symlink_to(target)
    past = time.time() - 3600
    os.utime(link, (past, past), follow_symlinks=False)
    common = ["--config", str(config), "--source", str(source), "--json"]

    planned = _json(capsys, ["plan", *common])["items"]
    explained = _json(capsys, ["explain", *common, str(link)])["items"]

    (plan_item,) = [i for i in planned if i["name"] == "link.dmg"]
    (item,) = explained
    assert item["outside_source"] is False
    assert item["category"] == plan_item["category"]
    # A run skips a dangling link (its age cannot be read) and sorts a live one.
    if dangling:
        assert item["skipped"].startswith("cannot be read")
    else:
        assert item["skipped"] is None


# --- 2. init never shadows the config already in use -------------------------


def test_init_refuses_to_shadow_an_existing_config(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    # conftest hides the real lookup; this test needs it, inside the fake HOME.
    monkeypatch.setattr(config_module, "find_user_config", real_find_user_config)
    legacy = tmp_path / ".cubby.toml"
    legacy.write_text('[settings]\nignore = ["*.dmg"]\n', encoding="utf-8")

    assert main(["init"]) == EXIT_FAILED

    assert not (tmp_path / ".config" / "cubby" / "config.toml").exists()
    err = capsys.readouterr().err
    assert str(legacy) in err
    assert "--force" in err


# --- 3. history tells undone, partly undone and unknown apart ----------------


def _record(ledger: Ledger, run: str, moved: int) -> None:
    ledger.record(
        RunRecord(
            run=run,
            mode="run",
            source="/d",
            started="2026-09-28T10:00:00",
            finished="2026-09-28T10:00:01",
            moved=moved,
            failed=0,
        )
    )


def test_history_reports_partial_and_unknown_runs(capsys):
    ledger, journal = Ledger(), Journal()
    entries = [Entry("partial", i, "move", Path(f"/d/{i}"), Path(f"/d/X/{i}")) for i in range(2)]
    for entry in entries:
        journal.record(entry)
    journal.settle(entries[0], "restored")
    whole = Entry("whole", 0, "move", Path("/d/w"), Path("/d/X/w"))
    journal.record(whole)
    journal.settle(whole, "restored")
    _record(ledger, "forgotten", 1)  # no longer in the journal
    _record(ledger, "partial", 2)
    _record(ledger, "whole", 1)

    runs = {r["run"]: r for r in _json(capsys, ["history", "--json"])["runs"]}

    assert runs["whole"]["undo"] == "undone"
    assert runs["partial"]["undo"] == "partly undone"
    assert runs["forgotten"]["undo"] == "unknown"
    assert runs["forgotten"]["undone"] is False


# --- 4. history rejects a limit below one ------------------------------------


@pytest.mark.parametrize("limit", ["0", "-1"])
def test_history_rejects_a_limit_below_one(limit, capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["history", "-n", limit])
    assert exit_info.value.code == 2
    assert "at least 1" in capsys.readouterr().err


# --- 5. init --force replaces atomically -------------------------------------


def test_init_force_leaves_the_old_config_when_the_write_fails(tmp_path, monkeypatch):
    target = tmp_path / "config.toml"
    target.write_text("# mine\n", encoding="utf-8")

    def broken_write(path: Path, text: str) -> None:
        raise OSError(5, "disk error")

    monkeypatch.setattr(config_module.state, "replace_text", broken_write)

    assert main(["init", "--path", str(target), "--force"]) == EXIT_FAILED
    assert target.read_text(encoding="utf-8") == "# mine\n"


def test_init_force_replaces_a_symlink_rather_than_writing_through_it(tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("keep\n", encoding="utf-8")
    target = tmp_path / "config.toml"
    target.symlink_to(victim)

    assert main(["init", "--path", str(target), "--force"]) == EXIT_OK

    assert victim.read_text(encoding="utf-8") == "keep\n"
    assert not target.is_symlink()


# --- 7. the gaps the review named ---------------------------------------------


def test_the_starter_config_behaves_exactly_like_the_defaults(tmp_path):
    target = tmp_path / "config.toml"
    assert main(["init", "--path", str(target)]) == EXIT_OK

    assert load_config(user_path=target) == load_config(
        user_path=None, default_path=default_config_path()
    )


def test_explain_never_moves_or_renames_an_invoice(setup, capsys):
    source, config = setup
    invoice = _old(source / "invoice-spotify.txt", "Facture du 07/07/2026")
    before = sorted(p.name for p in source.rglob("*"))

    assert main(["explain", "--config", str(config), "--source", str(source), str(invoice)]) == 0

    assert sorted(p.name for p in source.rglob("*")) == before
    assert invoice.read_text(encoding="utf-8") == "Facture du 07/07/2026"
