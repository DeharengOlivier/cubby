"""What plan, explain and the run summary show is what a run does.

Found by an exploratory session: with ``dedupe = true`` the previews announced
a move for a file the run then deleted as a duplicate; ``plan`` listed
in-progress downloads as going to ``_Unsorted``; nothing said which files were
left alone, or that a plain file named like a category folder blocked it; and
``explain`` printed "goes to" above "left alone" for the same file.
"""

from __future__ import annotations

import json

import pytest

from cubby.app.explain import explain
from cubby.cli import main
from tests.helpers import aged_file, config_for


@pytest.fixture
def folder(tmp_path):
    aged_file(tmp_path / "Documents", "report.txt", content="SAME")
    aged_file(tmp_path, "report.txt", content="SAME")
    aged_file(tmp_path, "notes.txt")
    aged_file(tmp_path, "movie.mkv.crdownload")
    aged_file(tmp_path, "fresh.txt", age=0)
    return tmp_path


def _config_file(tmp_path, **settings) -> str:
    path = tmp_path.parent / f"{tmp_path.name}.toml"
    body = "".join(f"{key} = {json.dumps(value)}\n" for key, value in settings.items())
    path.write_text("[settings]\n" + body, encoding="utf-8")
    return str(path)


def _cli(capsys, *args):
    code = main(list(args))
    return code, capsys.readouterr().out


def test_plan_says_a_duplicate_would_be_deleted(folder, capsys):
    config = _config_file(folder, dedupe=True)

    _, text = _cli(capsys, "plan", "--config", config, "--source", str(folder))
    _, raw = _cli(capsys, "plan", "--config", config, "--source", str(folder), "--json")
    items = {item["name"]: item for item in json.loads(raw)["items"]}

    assert "Would delete as duplicates  (1)" in text
    assert "report.txt   duplicate of Documents/report.txt" in text
    assert "Would move 2 item(s) and delete 1 duplicate(s)." in text  # the age is ignored
    assert items["report.txt"]["duplicate_of"] == str(folder / "Documents" / "report.txt")
    assert items["notes.txt"]["duplicate_of"] is None


def test_the_run_summary_says_a_duplicate_was_deleted(folder, capsys):
    config = _config_file(folder, dedupe=True)

    _, text = _cli(capsys, "run", "--config", config, "--source", str(folder), "--delay", "1h")

    assert "Deleted as duplicates  (1)" in text
    assert "Moved 1 item(s) and deleted 1 duplicate(s)." in text
    assert not (folder / "report.txt").exists()


def test_plan_leaves_in_progress_downloads_alone_and_says_so(folder, capsys):
    _, text = _cli(capsys, "plan", "--source", str(folder))
    _, raw = _cli(capsys, "plan", "--source", str(folder), "--json")
    payload = json.loads(raw)

    assert "movie.mkv.crdownload" not in {item["name"] for item in payload["items"]}
    assert {"name": "movie.mkv.crdownload", "reason": "download in progress (.crdownload)"} in (
        payload["left_alone"]
    )
    assert "Left alone  (1)" in text  # plan ignores the age: fresh.txt is planned
    assert "fresh.txt" in {item["name"] for item in payload["items"]}


def test_the_run_summary_lists_what_it_left_alone(folder, capsys):
    _, text = _cli(capsys, "run", "--source", str(folder), "--delay", "1h")

    assert "Left alone  (2)" in text
    assert "movie.mkv.crdownload   download in progress (.crdownload)" in text
    assert "fresh.txt   too recent: moves once it is 1h old" in text


def test_a_file_blocking_a_category_folder_is_reported_before_anything_fails(tmp_path, capsys):
    aged_file(tmp_path, "Documents", content="a plain file")
    aged_file(tmp_path, "notes.txt")

    _, text = _cli(capsys, "plan", "--source", str(tmp_path))
    _, raw = _cli(capsys, "plan", "--source", str(tmp_path), "--json")

    assert "Documents is a file where the Documents/ folder goes" in text
    assert "rename or move it" in text
    assert json.loads(raw)["blocked"] == ["Documents"]


def test_explain_says_first_that_a_file_stays(folder):
    item = explain(folder / "movie.mkv.crdownload", config_for(folder))

    assert item.skipped == "download in progress (.crdownload)"


def test_explain_text_puts_left_alone_first(folder, capsys):
    _, text = _cli(capsys, "explain", "--source", str(folder), str(folder / "movie.mkv.crdownload"))
    lines = [line.strip() for line in text.splitlines()[1:]]

    assert lines[0].startswith("stays where it is")
    assert lines[1].startswith("would go to")
    assert not any(line.startswith("goes to") for line in lines)


def test_explain_says_a_duplicate_would_be_deleted(folder, capsys):
    config = _config_file(folder, dedupe=True)

    _, text = _cli(capsys, "explain", "--config", config, "--source", str(folder),
                   str(folder / "report.txt"))  # fmt: skip
    _, raw = _cli(capsys, "explain", "--config", config, "--source", str(folder), "--json",
                  str(folder / "report.txt"))  # fmt: skip

    (line,) = [line.split() for line in text.splitlines() if "duplicate of" in line]
    assert line == ["deleted", "as", "a", "duplicate", "of", "Documents/report.txt"]
    assert json.loads(raw)["items"][0]["duplicate_of"] == str(folder / "Documents" / "report.txt")


def test_explain_names_a_file_that_has_a_category_folder_name(tmp_path, capsys):
    aged_file(tmp_path, "Documents", content="a plain file")

    _, text = _cli(capsys, "explain", "--source", str(tmp_path), str(tmp_path / "Documents"))

    assert "a file with the name of a folder cubby files into: rename or move it" in text


# --- from the review of this change ---------------------------------------------


@pytest.fixture
def twins(tmp_path):
    # Both are renamed to the same invoice name: the second one the run meets
    # is a duplicate of the first, although nothing was filed before the run.
    aged_file(tmp_path, "spotify invoice.txt", content="SAME")
    aged_file(tmp_path, "spotify invoice (1).txt", content="SAME")
    return tmp_path


def test_plan_predicts_a_duplicate_made_within_the_same_pass(twins, capsys):
    config = _config_file(twins, dedupe=True)

    _, planned = _cli(capsys, "plan", "--config", config, "--source", str(twins))
    _, ran = _cli(capsys, "run", "--config", config, "--source", str(twins))

    assert "Would move 1 item(s) and delete 1 duplicate(s)." in planned
    assert "Moved 1 item(s) and deleted 1 duplicate(s)." in ran


def test_explain_predicts_a_duplicate_made_within_the_same_pass(twins, capsys):
    config = _config_file(twins, dedupe=True)
    first, second = twins / "spotify invoice (1).txt", twins / "spotify invoice.txt"

    _, raw = _cli(capsys, "explain", "--config", config, "--source", str(twins), "--json",
                  str(first), str(second))  # fmt: skip
    items = json.loads(raw)["items"]

    assert items[0]["duplicate_of"] is None
    assert items[1]["duplicate_of"] == items[1]["destination"]


def test_plan_does_not_count_a_file_bound_for_a_blocked_folder_as_moved(tmp_path, capsys):
    aged_file(tmp_path, "Documents", content="a plain file")
    aged_file(tmp_path, "notes.txt")

    _, planned = _cli(capsys, "plan", "--source", str(tmp_path))
    code, ran = _cli(capsys, "run", "--source", str(tmp_path), "--delay", "0")

    assert "Would move 0 item(s). 1 would fail." in planned
    assert "notes.txt   a file named Documents is in the way" in planned
    assert code == 1
    assert "Moved 0 item(s). 1 could not be sorted." in ran


def test_plan_names_a_file_blocking_a_month_folder(tmp_path, capsys):
    aged_file(tmp_path, "spotify invoice.txt")
    _, raw = _cli(capsys, "plan", "--source", str(tmp_path), "--json")
    (item,) = json.loads(raw)["items"]
    month = tmp_path / item["category"] / item["subdir"]
    aged_file(month.parent, month.name, content="a file where the month folder goes")

    _, planned = _cli(capsys, "plan", "--source", str(tmp_path))

    shown = f"{item['category']}/{item['subdir']}"
    assert f"a file named {shown} is in the way" in planned
    assert "1 would fail." in planned


def test_explain_values_line_up(folder, capsys):
    _, text = _cli(capsys, "explain", "--source", str(folder), str(folder / "movie.mkv.crdownload"))
    lines = text.splitlines()[1:]

    starts = {line.index(value) for line, value in zip(lines, _values(lines), strict=True)}
    assert len(starts) == 1, text


def _values(lines: list[str]) -> list[str]:
    return [line.strip().split("  ", 1)[1].strip() for line in lines]


def test_explain_gives_no_destination_for_a_file_never_sorted(tmp_path, capsys):
    aged_file(tmp_path, "Documents", content="a plain file")
    aged_file(tmp_path, ".hidden")

    _, text = _cli(capsys, "explain", "--source", str(tmp_path),
                   str(tmp_path / "Documents"), str(tmp_path / ".hidden"))  # fmt: skip

    assert "would go to" not in text


def test_explain_names_no_duplicate_for_a_file_that_stays(tmp_path):
    aged_file(tmp_path / "Documents", "report.txt", content="SAME")
    aged_file(tmp_path, "report.txt", content="SAME", age=0)

    item = explain(tmp_path / "report.txt", config_for(tmp_path, dedupe=True, delay=3600))

    assert item.skipped is not None
    assert item.duplicate_of is None


def test_the_run_says_where_to_see_every_file_it_left_alone(tmp_path, capsys):
    for i in range(12):
        aged_file(tmp_path, f"f{i:02}.txt", age=0)

    _, text = _cli(capsys, "run", "--source", str(tmp_path))

    assert "and 2 more; 'cubby explain FILE' says why for any file" in text
