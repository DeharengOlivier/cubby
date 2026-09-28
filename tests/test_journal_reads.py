"""Reading one run, or the undo state of every run, without parsing every entry.

`cubby history` needs counts and `cubby undo` needs one run; building every
entry of a 200 000-move journal took 6 s (docs/PERFORMANCE.md). The fast paths
must answer exactly what the full read answers, damaged lines included.
"""

from __future__ import annotations

import json
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from cubby.adapters import journal as journal_module
from cubby.adapters.journal import Entry, Journal

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
)
lines = st.lists(
    st.one_of(v2_move.map(json.dumps), v2_settle.map(json.dumps), v1_run.map(json.dumps), noise),
    max_size=25,
)


def _journal(tmp_path_factory, content: list[str]) -> Journal:
    path = tmp_path_factory.mktemp("j") / "journal.jsonl"
    path.write_text("".join(line + "\n" for line in content), encoding="utf-8")
    return Journal(path)


@settings(max_examples=150, deadline=None)
@given(content=lines)
def test_the_fast_reads_answer_what_the_full_read_answers(tmp_path_factory, content):
    journal = _journal(tmp_path_factory, content)
    full = journal.runs()

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
