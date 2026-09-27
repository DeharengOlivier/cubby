"""The commands that help a person trust and tune cubby.

- ``cubby explain FILE`` says where a file would go and which rule decided it,
  or why it would be left alone. Without it, tuning a config meant moving files
  and reading the log.
- ``cubby history`` lists recent runs with their counts, and whether each was
  undone, so ``cubby undo --run ID`` has something to point at.
- ``cubby init`` writes a starter config that loads cleanly.
- ``ignore`` patterns leave matching files alone.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from cubby.adapters.config import load_config
from cubby.adapters.filesystem import iter_candidates
from cubby.cli import EXIT_BAD_CONFIG, EXIT_FAILED, EXIT_OK, main
from cubby.domain.category import Category, Config, Settings
from cubby.domain.engine import Engine
from cubby.domain.file_ref import Stage

CONFIG = """
[settings]
delay = "1m"
ignore = ["*.torrent", "KEEP-*"]

[[category]]
name = "Invoices"
name_patterns = ["invoice", "facture"]
content_patterns = ["montant ttc"]
date_folders = true
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
    os.utime(path, (past, past))
    return path


def _explain(capsys, config: Path, source: Path, *files: Path, json_out: bool = False):
    argv = ["explain", "--config", str(config), "--source", str(source), *map(str, files)]
    if json_out:
        argv.append("--json")
    code = main(argv)
    out = capsys.readouterr()
    return code, (json.loads(out.out) if json_out else out.out), out.err


# --- the engine says which rule decided ---------------------------------------


def test_every_decision_names_the_rule_that_made_it(make_ref):
    config = Config(
        settings=Settings(),
        categories=(
            Category(name="Invoices", name_patterns=("invoice",), content_patterns=("ttc",)),
            Category(name="Installers", extensions=frozenset({"dmg"}), strong_ext=True),
            Category(name="Documents", extensions=frozenset({"pdf"})),
        ),
    )
    engine = Engine(config)

    assert engine.classify(make_ref("setup.dmg")).rule == "extension .dmg"
    assert engine.classify(make_ref("my-invoice.pdf")).rule == "name matches 'invoice'"
    content = engine.classify(make_ref("3c0fe3ad-1111-2222.pdf", text="Montant TTC 12"))
    assert (content.stage, content.rule) == (Stage.CONTENT, "content matches 'ttc'")
    assert engine.classify(make_ref("notes.pdf")).rule == "extension .pdf"
    assert engine.classify(make_ref("x.qzx")).rule is None


# --- cubby explain -----------------------------------------------------------


def test_explain_names_the_destination_and_the_rule(setup, capsys):
    source, config = setup
    invoice = _old(source / "invoice-spotify.txt", "Facture du 07/07/2026")

    code, out, _ = _explain(capsys, config, source, invoice)

    assert code == EXIT_OK
    assert "Invoices/2026-07/spotify facture 2026-07-07.txt" in out
    assert "name matches 'invoice'  (name stage)" in out


def test_explain_as_json(setup, capsys):
    source, config = setup
    dmg = _old(source / "Setup.dmg")

    code, payload, _ = _explain(capsys, config, source, dmg, json_out=True)

    assert code == EXIT_OK
    (item,) = payload["items"]
    assert item["category"] == "Installers"
    assert item["stage"] == "strong-ext"
    assert item["rule"] == "extension .dmg"
    assert item["skipped"] is None
    assert item["destination"] == str(source / "Installers" / "Setup.dmg")


@pytest.mark.parametrize(
    ("name", "reason"),
    [
        ("movie.torrent", "ignored by pattern '*.torrent'"),
        ("keep-this.pdf", "ignored by pattern 'KEEP-*'"),
        ("big.iso.crdownload", "download in progress (.crdownload)"),
        (".hidden.pdf", "hidden file"),
    ],
)
def test_explain_says_why_a_file_is_left_alone(setup, capsys, name, reason):
    source, config = setup
    path = _old(source / name)

    _, out, _ = _explain(capsys, config, source, path)

    assert reason in out


def test_explain_says_when_a_fresh_file_will_move(setup, capsys):
    source, config = setup
    fresh = source / "notes.txt"
    fresh.write_text("just downloaded")

    _, out, _ = _explain(capsys, config, source, fresh)

    assert "too recent: moves once it is 1m old" in out
    assert "Documents/notes.txt" in out


def test_explain_a_file_outside_the_folder_still_classifies_it(setup, tmp_path, capsys):
    source, config = setup
    elsewhere = _old(tmp_path / "invoice.pdf")

    _, out, _ = _explain(capsys, config, source, elsewhere)

    assert "Invoices" in out
    assert "not in the watched folder" in out


def test_explain_a_missing_file_is_an_error(setup, capsys):
    source, config = setup
    code, _, err = _explain(capsys, config, source, source / "gone.pdf")
    assert code == EXIT_FAILED
    assert "no such file" in err


# --- ignore patterns ---------------------------------------------------------


def test_ignored_files_are_never_candidates(tmp_path):
    for name in ("a.torrent", "KEEP-me.pdf", "keep-me-too.pdf", "b.pdf"):
        (tmp_path / name).write_text("x")
    settings = Settings(source=tmp_path, ignore=("*.torrent", "keep-*"))

    names = [p.name for p in iter_candidates(settings)]

    assert names == ["b.pdf"]


def test_ignore_is_read_from_the_config(setup):
    _, config = setup
    assert load_config(user_path=config).settings.ignore == ("*.torrent", "KEEP-*")


@pytest.mark.parametrize("bad", [[""], ["  "], [3], "*.torrent", ["a/b"]])
def test_an_unusable_ignore_pattern_is_a_config_error(tmp_path, capsys, bad):
    config = tmp_path / "config.toml"
    config.write_text(
        f"[settings]\nignore = {json.dumps(bad)}\n[[category]]\nname = 'Documents'\n",
        encoding="utf-8",
    )
    assert main(["plan", "--config", str(config), "--source", str(tmp_path)]) == EXIT_BAD_CONFIG
    assert "ignore" in capsys.readouterr().err


def test_a_run_leaves_ignored_files_where_they_are(setup):
    source, config = setup
    _old(source / "movie.torrent")
    _old(source / "notes.txt")

    assert main(["run", "--config", str(config), "--source", str(source)]) == EXIT_OK

    assert (source / "movie.torrent").exists()
    assert (source / "Documents" / "notes.txt").exists()


# --- cubby history -----------------------------------------------------------


def test_history_lists_runs_newest_first_and_marks_undone_ones(setup, capsys):
    source, config = setup
    _old(source / "a.txt")
    main(["run", "--config", str(config), "--source", str(source)])
    _old(source / "b.txt")
    main(["run", "--config", str(config), "--source", str(source)])
    main(["undo"])
    capsys.readouterr()

    assert main(["history", "--json"]) == EXIT_OK
    runs = json.loads(capsys.readouterr().out)["runs"]

    assert [r["moved"] for r in runs] == [1, 1]
    assert [r["undone"] for r in runs] == [True, False]

    assert main(["history", "-n", "1"]) == EXIT_OK
    out = capsys.readouterr().out
    assert runs[0]["run"] in out
    assert runs[1]["run"] not in out
    assert "undone" in out


def test_history_undo_of_a_listed_run(setup, capsys):
    source, config = setup
    _old(source / "a.txt")
    main(["run", "--config", str(config), "--source", str(source)])
    _old(source / "b.txt")
    main(["run", "--config", str(config), "--source", str(source)])
    capsys.readouterr()
    main(["history", "--json"])
    first = json.loads(capsys.readouterr().out)["runs"][-1]["run"]

    assert main(["undo", "--run", first]) == EXIT_OK

    assert (source / "a.txt").exists()
    assert (source / "Documents" / "b.txt").exists()


def test_history_when_nothing_ran_yet(capsys):
    assert main(["history"]) == EXIT_OK
    assert "No runs recorded yet" in capsys.readouterr().out


# --- cubby init --------------------------------------------------------------


def test_init_writes_a_starter_config_that_loads(tmp_path, capsys):
    target = tmp_path / "conf" / "config.toml"

    assert main(["init", "--path", str(target)]) == EXIT_OK

    config = load_config(user_path=target)
    assert config.categories
    assert "Invoices" in {c.name for c in config.categories}
    assert str(target) in capsys.readouterr().out


def test_init_never_overwrites_without_force(tmp_path, capsys):
    target = tmp_path / "config.toml"
    target.write_text("# mine\n")

    assert main(["init", "--path", str(target)]) == EXIT_FAILED
    assert target.read_text() == "# mine\n"
    assert "--force" in capsys.readouterr().err

    assert main(["init", "--path", str(target), "--force"]) == EXIT_OK
    assert "[[category]]" in target.read_text()


def test_init_defaults_to_the_user_config_location(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert main(["init"]) == EXIT_OK
    assert (tmp_path / ".config" / "cubby" / "config.toml").is_file()


def test_plan_json_carries_the_deciding_rule(setup, capsys):
    source, config = setup
    _old(source / "Setup.dmg")

    assert main(["plan", "--config", str(config), "--source", str(source), "--json"]) == EXIT_OK

    (item,) = json.loads(capsys.readouterr().out)["items"]
    assert item["rule"] == "extension .dmg"
