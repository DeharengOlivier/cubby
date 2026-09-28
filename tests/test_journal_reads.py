"""Reading one run, or the undo state of every run, without parsing every entry.

`cubby history` needs counts and `cubby undo` needs one run; building every
entry of a 200 000-move journal took 6 s (docs/PERFORMANCE.md). Every read must
answer exactly what the reader of cubby 0.2 answered, damaged lines included:
that reader is frozen in ``tests/journal_reference.py`` as the reference.
"""

from __future__ import annotations

import json
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from cubby.adapters import journal as journal_module
from cubby.adapters.journal import Entry, Journal, Run
from tests import journal_reference

RUN_IDS = st.sampled_from(["r1", "r2", "r3", "r4"])
SEQS = st.integers(min_value=0, max_value=5)
NAMES = st.text(st.characters(blacklist_characters="\0/"), min_size=1, max_size=6)

v2_move = st.fixed_dictionaries(
    {
        "v": st.just(2),
        "run": RUN_IDS,
        "seq": SEQS,
        "op": st.sampled_from(["move", "dedupe"]),
        "from": NAMES.map(lambda n: f"/d/{n}"),
        "to": NAMES.map(lambda n: f"/d/X/{n}"),
    }
)
v2_settle = st.fixed_dictionaries(
    {"v": st.just(2), "run": RUN_IDS, "seq": SEQS, "op": st.sampled_from(["restored", "gone"])}
)
v1_run = st.fixed_dictionaries(
    {
        "ts": st.just("2026-01-01T00:00:00"),
        "moves": st.lists(
            st.fixed_dictionaries({"from": NAMES, "to": NAMES}), min_size=1, max_size=3
        ),
    }
)
# What a damaged or foreign line can look like: each must cost that line only.
noise = st.one_of(
    st.just("not json"),
    st.just('{"v": 2, "run": "r1", "seq": -1, "op": "move", "from": "/a", "to": "/b"}'),
    st.just('{"v": 2, "run": "r2", "seq": 1, "op": "move", "from": 5, "to": "/b"}'),
    st.just('{"v": 2, "run": 7, "seq": 0, "op": "move", "from": "/a", "to": "/b"}'),
    st.just('{"v": 2, "run": "r3", "seq": true, "op": "gone"}'),
    st.just('{"ts": "x", "moves": [{"from": "/a", "to": "/b"}, {"from": null}]}'),
    st.just("[1, 2]"),
    st.just('{"v": 3, "run": "r1", "seq": 0, "op": "move", "from": "/a", "to": "/b"}'),
    st.just('{"v": 2, "run": "", "seq": 0, "op": "move", "from": "", "to": "/b"}'),
    st.just('{"v": 2, "run": null, "seq": 1, "op": "dedupe", "from": "/a", "to": "/b"}'),
    st.just('{"v": 2, "run": 7, "seq": 0, "op": "restored"}'),
    st.just('{"ts": "x", "moves": [{"from": "/a", "to": 5}]}'),
    # A version 2 run whose id is the id a version 1 line gets.
    st.just('{"v": 2, "run": "v1-5f879c1314f9", "seq": 4, "op": "move", "from": "/a", "to": "/b"}'),
    st.just('{"ts": "t", "moves": [{"from": "/a", "to": "/b"}]}'),
)
lines = st.lists(
    st.one_of(v2_move.map(json.dumps), v2_settle.map(json.dumps), v1_run.map(json.dumps), noise),
    max_size=25,
)


def _journal(tmp_path_factory, content: list[str]) -> Journal:
    path = tmp_path_factory.mktemp("j") / "journal.jsonl"
    path.write_text("".join(line + "\n" for line in content), encoding="utf-8")
    return Journal(path)


def _plain(run: Run) -> journal_reference.RefRun:
    entries = [(e.seq, e.op, e.source, e.destination) for e in run.entries]
    assert all(e.run == run.run_id for e in run.entries)
    return run.run_id, entries, dict(run.settled)


@settings(max_examples=300, deadline=None)
@given(content=lines)
def test_every_read_answers_what_the_reader_of_0_2_answered(tmp_path_factory, content):
    journal = _journal(tmp_path_factory, content)
    full = journal.runs()

    assert [_plain(run) for run in full] == journal_reference.runs(content)
    assert journal.tallies() == {run.run_id: (len(run.entries), len(run.pending)) for run in full}
    for run in full:
        assert journal.run(run.run_id) == run
    assert journal.run("absent") is None
    assert journal.last_pending_run() == next((r for r in reversed(full) if r.pending), None)


def test_reading_one_run_builds_only_its_entries(tmp_path, monkeypatch):
    journal = Journal(tmp_path / "journal.jsonl")
    for index in range(50):
        journal.record(Entry(f"r{index}", 0, "move", Path(f"/a/{index}"), Path(f"/b/{index}")))
    built: list[str] = []
    real_entry = journal_module.Entry

    def counting_entry(run, *args):
        built.append(run)
        return real_entry(run, *args)

    monkeypatch.setattr(journal_module, "Entry", counting_entry)

    journal.run("r7")
    assert built == ["r7"]
    built.clear()
    journal.last_pending_run()
    assert built == ["r49"]
    built.clear()
    journal.tallies()
    assert built == []


# --- the cases the property found worth pinning --------------------------------


def test_a_version_1_run_keeps_the_moves_before_a_damaged_one(tmp_path):
    line = json.dumps(
        {
            "ts": "t",
            "moves": [{"from": "/a", "to": "/b"}, {"from": None}, {"from": "/c", "to": "/d"}],
        }
    )
    journal = _journal_at(tmp_path, [line])

    (run,) = journal.runs()

    assert [(e.seq, e.source, e.destination) for e in run.entries] == [(0, Path("/a"), Path("/b"))]
    assert journal.tallies() == {run.run_id: (1, 1)}


def test_a_numeric_run_id_reads_as_its_text(tmp_path):
    journal = _journal_at(
        tmp_path, ['{"v": 2, "run": 7, "seq": 0, "op": "move", "from": "/a", "to": "/b"}']
    )

    assert [run.run_id for run in journal.runs()] == ["7"]
    assert journal.run("7") is not None


def test_a_move_whose_path_is_not_text_is_skipped(tmp_path):
    journal = _journal_at(
        tmp_path,
        [
            '{"v": 2, "run": "r1", "seq": 0, "op": "move", "from": 5, "to": "/b"}',
            '{"v": 2, "run": "r1", "seq": 1, "op": "move", "from": "/a", "to": "/b"}',
        ],
    )

    assert [e.seq for e in journal.runs()[0].entries] == [1]


def test_compaction_keeps_a_run_the_reads_can_undo_whatever_its_id(tmp_path, monkeypatch):
    # Measured by review: a run read as "7" was dropped by compaction, which
    # keyed lines by string ids only, while it still had a move to undo.
    monkeypatch.setattr(journal_module, "MAX_BYTES", 0)
    line = '{"v": 2, "run": 7, "seq": 0, "op": "move", "from": "/a", "to": "/b"}'
    journal = _journal_at(tmp_path, [line])

    journal.compact()

    assert journal.tallies() == {"7": (1, 1)}


def _journal_at(tmp_path: Path, content: list[str]) -> Journal:
    path = tmp_path / "journal.jsonl"
    path.write_text("".join(line + "\n" for line in content), encoding="utf-8")
    return Journal(path)
