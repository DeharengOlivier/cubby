"""One pass of the agent holds memory bounded by what it must remember, not by the folder.

Audit 3 (PRF-04, SCL-02) measured about 2.6 KB per file moved in one pass, 528 MB
at 200 000 files. A tracemalloc profile of one pass (docs/PERFORMANCE.md) put it
in three places: the outcome of every file, kept to the end of the pass although
the agent only counts them; the folder listing, held as ``Path`` objects; and
journal compaction, which read and parsed the whole journal at once.

Each test here pins one of those, and that the summaries (ledger, heartbeat,
alerts, journal) are what they were when every outcome was kept.
"""

from __future__ import annotations

import json
import tracemalloc
import weakref
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from cubby.adapters import journal as journal_module
from cubby.adapters import state
from cubby.adapters.filesystem import candidate_skip_reason, iter_candidates
from cubby.adapters.journal import Entry, Journal
from cubby.adapters.ledger import MAX_FAILURES_RECORDED, Failure, Ledger, RunRecord
from cubby.app.report import PassTally, SortOutcome
from cubby.app.sorter import Sorter
from cubby.app.watcher import Watcher
from cubby.domain.category import Category
from tests import compaction_reference
from tests.helpers import TEXT_DOCUMENTS, aged_file, config_for
from tests.test_journal_reads import lines as journal_lines

IMAGES = Category(name="Images", extensions=frozenset({"png"}))


def _folder_with_failures(root: Path, *, sorted_ok: int, failing: int) -> Path:
    """``sorted_ok`` images that sort, ``failing`` texts blocked by a file named Documents."""
    aged_file(root, "Documents")  # a file where the Documents/ folder goes
    for index in range(sorted_ok):
        aged_file(root, f"img{index:03d}.png")
    for index in range(failing):
        aged_file(root, f"doc{index:03d}.txt")
    return root


# --- the pass keeps no outcome ---------------------------------------------------


def test_the_agent_pass_keeps_no_outcome_once_it_is_counted(tmp_path, monkeypatch):
    for index in range(40):
        aged_file(tmp_path, f"f{index:02d}.txt")
    alive: list[weakref.ReferenceType[SortOutcome]] = []
    most_alive = 0
    real = Sorter._outcome

    def spied(self, path, **kwargs):  # type: ignore[no-untyped-def]
        nonlocal most_alive
        outcome = real(self, path, **kwargs)
        alive.append(weakref.ref(outcome))
        # Outcomes hold no reference cycle: one nobody keeps is freed at once.
        most_alive = max(most_alive, sum(1 for ref in alive if ref() is not None))
        return outcome

    monkeypatch.setattr(Sorter, "_outcome", spied)
    sorter = Sorter(
        config_for(tmp_path),
        journal=Journal(tmp_path.parent / "j.jsonl"),
        ledger=Ledger(tmp_path.parent / "state"),
        mode="watch",
    )

    moved = Watcher(sorter, 1.0, sleep=lambda _: None).run(max_cycles=1)

    assert moved == 40
    assert len(alive) == 40
    assert most_alive == 1  # the file being sorted, never the ones before it


def test_a_plan_still_returns_every_outcome(tmp_path):
    for index in range(5):
        aged_file(tmp_path, f"f{index}.txt")

    outcomes = Sorter(config_for(tmp_path)).sort_once(apply=False)

    assert [o.name for o in outcomes] == [f"f{index}.txt" for index in range(5)]


# --- what is counted is what the full list said ------------------------------------


def test_the_pass_summary_and_ledger_equal_those_of_the_full_list(tmp_path):
    source = _folder_with_failures(tmp_path / "src", sorted_ok=7, failing=25)
    state_dir = tmp_path / "state"
    sorter = Sorter(
        config_for(source, TEXT_DOCUMENTS, IMAGES),
        journal=Journal(state_dir / "journal.jsonl"),
        ledger=Ledger(state_dir),
        mode="watch",
    )
    seen: list[SortOutcome] = []

    tally = sorter.sort_pass(apply=True, on_outcome=seen.append, run_id="r1")

    assert tally.count == len(seen) == 32
    assert tally.moved == sum(1 for o in seen if o.moved_to is not None) == 7
    assert tally.failed == sum(1 for o in seen if o.error is not None) == 25
    # What 0.3.0 wrote: the record built from every outcome, capped when written.
    (stored,) = [json.loads(line) for line in (state_dir / "runs.jsonl").read_text().splitlines()]
    expected = RunRecord(
        run="r1",
        mode="watch",
        source=str(source),
        started=stored["started"],
        finished=stored["finished"],
        moved=7,
        failed=25,
        failures=tuple(Failure(o.name, o.error) for o in seen if o.error),
    ).to_json()
    assert stored == expected
    assert len(stored["failures"]) == MAX_FAILURES_RECORDED


def test_journal_sequence_numbers_still_count_every_outcome(tmp_path):
    source = tmp_path / "src"
    aged_file(source, "Documents")
    aged_file(source, "a.png")
    aged_file(source, "b.txt")  # fails: Documents is a file
    aged_file(source, "c.png")
    journal = Journal(tmp_path / "journal.jsonl")
    sorter = Sorter(config_for(source, TEXT_DOCUMENTS, IMAGES), journal=journal)

    sorter.sort_pass(apply=True, run_id="r")

    (run,) = journal.runs()
    assert [(e.seq, e.source.name) for e in run.entries] == [(0, "a.png"), (2, "c.png")]


def test_the_watcher_announces_failures_as_it_did_with_the_full_list(tmp_path):
    # 1 005 failures: the watcher remembers 1 000 names, so the five after them
    # are announced again at the next pass, as they were in 0.3.0.
    failures = [SortOutcome.failed(tmp_path / f"f{i:04d}.pdf", "denied") for i in range(1005)]

    class Failing:
        source = tmp_path

        def sort_pass(self, *, apply, on_outcome, stop, run_id, on_waiting):  # type: ignore[no-untyped-def]
            tally = PassTally()
            for outcome in failures:
                tally.add(outcome)
                on_outcome(outcome)
            return tally

    alerts: list[str] = []
    Watcher(Failing(), 1.0, sleep=lambda _: None, alert=alerts.append).run(max_cycles=2)

    assert alerts == [
        "Could not sort f0000.pdf, f0001.pdf, f0002.pdf and 1002 more. See 'cubby status'.",
        "Could not sort f1000.pdf, f1001.pdf, f1002.pdf and 2 more. See 'cubby status'.",
    ]


# --- the listing ---------------------------------------------------------------------


def test_candidates_come_in_the_order_of_the_sorted_listing(tmp_path):
    names = ["b.txt", "B.txt", "a b.txt", "é.txt", "10.txt", "9.txt", ".hidden", "Z", "_x.txt"]
    for name in names:
        aged_file(tmp_path, name)
    (tmp_path / "folder").mkdir()
    config = config_for(tmp_path)

    found = list(iter_candidates(config.settings, config.managed_dirs))

    expected = [
        entry
        for entry in sorted(tmp_path.iterdir())
        if candidate_skip_reason(entry, config.settings, config.managed_dirs) is None
    ]
    assert found == expected
    assert all(type(path) is type(tmp_path) for path in found)


# --- journal compaction streams ------------------------------------------------------


@settings(max_examples=300, deadline=None)
@given(content=journal_lines, keep_runs=st.integers(min_value=0, max_value=3))
def test_compaction_keeps_what_0_3_0_kept(tmp_path_factory, content, keep_runs):
    path = tmp_path_factory.mktemp("j") / "journal.jsonl"
    raw = "".join(line + "\n" for line in content)
    path.write_text(raw, encoding="utf-8")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(journal_module, "MAX_BYTES", 0)
        patch.setattr(journal_module, "KEEP_RUNS", keep_runs)
        Journal(path).compact()

    kept = compaction_reference.compact([line for line in content if line.strip()], keep_runs)
    expected = raw if kept is None else "".join(line + "\n" for line in kept)
    assert path.read_text(encoding="utf-8") == expected


def test_compaction_never_loads_the_whole_journal(tmp_path, monkeypatch):
    journal = Journal(tmp_path / "journal.jsonl")
    for run in range(30):
        entry = Entry(f"r{run}", 0, "move", Path(f"/d/{run}"), Path(f"/d/X/{run}"))
        journal.record(entry)
        if run < 20:
            journal.settle(entry, "restored")

    def refused(_: Path) -> list[str]:
        raise AssertionError("compaction read the whole journal into memory")

    monkeypatch.setattr(state, "read_lines", refused)
    monkeypatch.setattr(journal_module, "MAX_BYTES", 0)
    monkeypatch.setattr(journal_module, "KEEP_RUNS", 5)

    journal.compact()

    monkeypatch.undo()
    assert [run.run_id for run in journal.runs()] == [f"r{run}" for run in range(20, 30)]


def test_compaction_memory_is_a_fraction_of_the_journal(tmp_path, monkeypatch):
    # 20 000 moves still to undo, in 200 runs: every line is kept, so 0.3.0 held
    # the whole text, its lines and their parsed fields at once (3.6 times the
    # file, measured). Streaming holds the runs' sequence numbers only (0.37 times, measured).
    path = tmp_path / "journal.jsonl"
    with path.open("w", encoding="utf-8") as handle:
        for index in range(20_000):
            record = {
                "v": 2,
                "run": f"run-{index // 100:04d}",
                "seq": index % 100,
                "op": "move",
                "from": f"/home/someone/Downloads/a-rather-long-file-name-{index:06d}.pdf",
                "to": f"/home/someone/Downloads/Documents/a-rather-long-file-name-{index:06d}.pdf",
                "id": [66306, 1_000_000 + index, 7, 1_700_000_000_000_000_000],
            }
            handle.write(json.dumps(record) + "\n")
    size = path.stat().st_size
    monkeypatch.setattr(journal_module, "MAX_BYTES", 0)

    tracemalloc.start()
    try:
        Journal(path).compact()
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()

    assert path.stat().st_size == size  # nothing to drop, nothing rewritten
    assert peak < size / 2, f"compaction peaked at {peak} bytes for a {size}-byte journal"


# --- reading lines one at a time reads what reading them all did ---------------------


@settings(max_examples=300, deadline=None)
@given(
    raw=st.lists(
        st.one_of(
            st.binary(max_size=8),
            st.sampled_from([b"\n", b"\r", b"\r\n", b"\xe2\x80\xa8", b"\xc3", b"\xa9", b" "]),
            st.text(max_size=5).map(lambda text: text.encode("utf-8", "surrogatepass")),
        ),
        max_size=30,
    ).map(b"".join)
)
def test_iterating_lines_yields_what_read_lines_returned(tmp_path_factory, raw):
    path = tmp_path_factory.mktemp("lines") / "state.jsonl"
    path.write_bytes(raw)

    assert list(state.iter_lines(path)) == state.read_lines(path)


def test_iterating_the_lines_of_an_absent_file_yields_none(tmp_path):
    assert list(state.iter_lines(tmp_path / "absent")) == []


# --- the whole pass ------------------------------------------------------------------


def test_the_memory_of_an_agent_pass_grows_slowly_with_the_folder(tmp_path):
    # 0.3.0 held about 1.2 KB per file here (outcomes and listing); the pass
    # now keeps a name per file for the sorted listing and nothing per outcome.
    count = 2_000
    source = tmp_path / "src"
    for index in range(count):
        aged_file(source, f"file-with-a-typical-name-{index:05d}.txt")
    sorter = Sorter(
        config_for(source),
        journal=Journal(tmp_path / "state" / "journal.jsonl"),
        ledger=Ledger(tmp_path / "state"),
        mode="watch",
    )
    watcher = Watcher(sorter, 1.0, sleep=lambda _: None)

    tracemalloc.start()
    try:
        baseline = tracemalloc.get_traced_memory()[0]
        moved = watcher.run(max_cycles=1)
        peak = tracemalloc.get_traced_memory()[1] - baseline
    finally:
        tracemalloc.stop()

    assert moved == count
    assert peak / count < 300, f"{peak / count:.0f} bytes per file"
