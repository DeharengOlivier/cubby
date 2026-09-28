"""Properties that must hold for every input, not just the examples we thought of.

Hypothesis generates the inputs and shrinks any failure to its smallest form.
The properties are the invariants the rest of cubby leans on:

- durations round-trip through their human form;
- a configured name either is refused or becomes exactly one path component;
- a free destination name never points at an existing file nor leaves its folder;
- the journal gives back what was written, whatever the paths contain, and a
  damaged line never hides the lines around it;
- invoice date reading is total and returns a real date;
- sorting then undoing puts every file back, byte for byte (model-based).
"""

from __future__ import annotations

import string
from datetime import date
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from cubby.adapters.config import load_config
from cubby.adapters.filesystem import unique_destination
from cubby.adapters.journal import Entry, Journal
from cubby.app.sorter import Sorter
from cubby.app.undo import undo_run
from cubby.domain.category import Category, Config, Settings
from cubby.domain.duration import format_duration, parse_duration
from cubby.domain.invoices import parse_invoice_date
from cubby.domain.naming import safe_component

# Filesystem properties make real files: fewer examples, no per-example deadline.
# Each example asks the session-wide tmp_path_factory for a fresh folder.
FS = settings(max_examples=60, deadline=None)

# Names a filesystem accepts: no separator, no NUL, not '.' or '..', short.
_NAME_ALPHABET = string.ascii_letters + string.digits + " -_.()éàç#@+,'"
file_names = st.text(_NAME_ALPHABET, min_size=1, max_size=40).filter(
    lambda s: s not in {".", ".."} and not s.startswith(".") and s.strip() == s
)


# --- durations ----------------------------------------------------------------


@given(st.integers(min_value=0, max_value=10**9))
def test_a_whole_duration_survives_its_human_form(seconds):
    assert parse_duration(format_duration(seconds)) == seconds


@given(st.integers(min_value=0, max_value=10**6), st.sampled_from(["s", "m", "h", "d", ""]))
def test_a_duration_with_a_unit_is_that_many_units(amount, unit):
    factor = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
    assert parse_duration(f"{amount}{unit}") == amount * factor
    assert parse_duration(f"  {amount} {unit.upper()} ") == amount * factor


@given(st.text(max_size=20))
def test_parse_duration_accepts_or_refuses_but_never_crashes(text):
    try:
        value = parse_duration(text)
    except ValueError:
        return
    assert value >= 0


# --- configured names ---------------------------------------------------------


@given(st.text(max_size=30))
def test_a_configured_name_is_refused_or_is_one_component(text):
    try:
        component = safe_component(text, field="name")
    except ValueError:
        return
    root = Path("/watched")
    joined = root / component
    assert joined.parent == root
    assert joined.name == component
    assert component not in {"", ".", ".."}


# --- free destination names ---------------------------------------------------


@FS
@given(name=file_names, taken=st.integers(min_value=0, max_value=5))
def test_a_free_destination_is_free_and_stays_in_its_folder(tmp_path_factory, name, taken):
    folder = tmp_path_factory.mktemp("dest")
    first = folder / name
    first.write_text("0")
    stem, suffix = first.stem, first.suffix
    for i in range(1, taken + 1):
        (folder / f"{stem} ({i}){suffix}").write_text(str(i))

    chosen = unique_destination(folder, name)

    assert not chosen.exists()
    assert chosen.parent == folder
    assert chosen.name.startswith(stem)


# --- journal ------------------------------------------------------------------

paths = st.text(
    # Every character a POSIX name may hold except the separator and NUL;
    # surrogates stand for the undecodable bytes Linux allows.
    st.characters(blacklist_characters="\0/"),
    min_size=1,
    max_size=60,
).map(lambda s: Path("/d") / s.replace("/", "_"))


@st.composite
def runs(draw):
    count = draw(st.integers(min_value=1, max_value=8))
    ops = draw(st.lists(st.sampled_from(["move", "dedupe"]), min_size=count, max_size=count))
    return [Entry("run-a", seq, op, draw(paths), draw(paths)) for seq, op in enumerate(ops)]


@FS
@given(entries=runs(), settled=st.sets(st.integers(min_value=0, max_value=7)), noise=st.text())
def test_the_journal_gives_back_what_was_written(tmp_path_factory, entries, settled, noise):
    journal = Journal(tmp_path_factory.mktemp("j") / "journal.jsonl")
    for entry in entries:
        journal.record(entry)
        # A torn or foreign line between records must not hide them.
        with journal.path.open("a", encoding="utf-8") as handle:
            handle.write(noise.replace("\n", " ") + "\n")
    for seq in sorted(settled):
        if seq < len(entries):
            journal.settle(entries[seq], "restored")

    (run,) = journal.runs()

    assert run.entries == entries
    assert [e.seq for e in run.pending] == [e.seq for e in entries if e.seq not in settled]


# --- invoice dates ------------------------------------------------------------


@given(st.text(max_size=300))
def test_reading_an_invoice_date_is_total(text):
    fallback = date(2020, 1, 1)
    found = parse_invoice_date(text, fallback)
    assert isinstance(found, date)
    if not any(ch.isdigit() for ch in text):
        assert found == fallback


# Years outside 2000-2100 are refused on purpose: they are almost always a
# reference number that happens to look like a date.
@given(st.dates(min_value=date(2000, 1, 1), max_value=date(2100, 12, 31)))
def test_a_labelled_iso_date_is_read_back(day):
    text = f"Invoice date: {day.isoformat()}\nTotal 12.00"
    assert parse_invoice_date(text, date(2000, 1, 1)) == day


# --- config parsing -----------------------------------------------------------

toml_scalars = st.one_of(
    st.booleans(),
    st.integers(-5, 10**6),
    st.text(max_size=10),
    st.lists(st.text(max_size=5), max_size=3),
)


@FS
@given(
    settings_table=st.dictionaries(
        st.sampled_from(["delay", "unsorted", "dedupe", "ignore", "content_scan", "max_bytes"]),
        toml_scalars,
        max_size=4,
    )
)
def test_any_settings_table_loads_or_is_a_clear_error(tmp_path_factory, settings_table):
    try:
        loaded = load_config(user_path=None, overrides={"settings": settings_table})
    except (ValueError, TypeError) as error:
        message = str(error)
    else:
        assert loaded.categories
        return
    assert message, "a refused config must say why"


# --- sort then undo -----------------------------------------------------------


def _tree(root: Path) -> dict[str, bytes]:
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@FS
@given(
    files=st.dictionaries(
        # A file named like a folder cubby creates blocks it, and the moves into
        # it fail on purpose (tests/test_file_in_folder_path.py).
        (
            file_names.map(lambda s: s + ".txt") | file_names.map(lambda s: s + ".png") | file_names
        ).filter(lambda s: s not in {"Invoices", "Images", "Documents", "_Unsorted"}),
        st.binary(max_size=8),
        min_size=1,
        max_size=12,
    ),
    dedupe=st.booleans(),
    prefiled=st.booleans(),
)
def test_sorting_then_undoing_restores_every_file(tmp_path_factory, files, dedupe, prefiled):
    source = tmp_path_factory.mktemp("Downloads")
    if prefiled:
        # A copy already filed, so the same name collides (or dedupes).
        (source / "Images").mkdir()
        for name, data in list(files.items())[:2]:
            (source / "Images" / name).write_bytes(data)
    for name, data in files.items():
        (source / name).write_bytes(data)
    before = _tree(source)
    config = Config(
        settings=Settings(source=source, delay=0, content_scan=False, dedupe=dedupe),
        categories=(
            Category(name="Invoices", name_patterns=("a",)),
            Category(name="Images", extensions=frozenset({"png"})),
            Category(name="Documents", extensions=frozenset({"txt"})),
        ),
    )
    journal = Journal(source.parent / f"{source.name}.jsonl")

    outcomes = Sorter(config, journal=journal).sort_once(apply=True)
    assert all(o.error is None for o in outcomes)

    result = undo_run(journal)

    assert result.failed == []
    assert _tree(source) == before


@pytest.mark.parametrize("name", ["a.txt", "b.png", "c"])
def test_the_model_test_covers_each_category(name, tmp_path):
    # Guards against the property above passing vacuously because nothing moved.
    (tmp_path / name).write_text("x")
    config = Config(
        settings=Settings(source=tmp_path, delay=0, content_scan=False),
        categories=(
            Category(name="Invoices", name_patterns=("a",)),
            Category(name="Images", extensions=frozenset({"png"})),
            Category(name="Documents", extensions=frozenset({"txt"})),
        ),
    )
    journal = Journal(tmp_path / "j.jsonl")
    outcomes = Sorter(config, journal=journal).sort_once(apply=True)
    assert [o.error for o in outcomes] == [None]
    assert not (tmp_path / name).exists()
