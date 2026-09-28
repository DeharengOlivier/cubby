"""Where the memory of one agent pass goes: tracemalloc, grouped by the cubby line.

    python benchmarks/profile_pass.py 50000

Builds a folder of settled files in a throwaway directory, runs one pass of the
agent over it (``Watcher``, one cycle, journal and ledger), and prints:

- what is alive at the end of the per-file loop, just before journal
  compaction, grouped by the innermost cubby line that allocated it;
- the traced peak of the loop, and of compaction on top of what the loop left.

Tracing 25 frames per allocation is slow (minutes at 50 000 files). The numbers
in docs/PERFORMANCE.md, "Memory of one pass", come from this probe.
"""

from __future__ import annotations

import argparse
import os
import shutil
import tempfile
import time
import tracemalloc
from pathlib import Path

from cubby.adapters.journal import Journal
from cubby.adapters.ledger import Ledger
from cubby.app.sorter import Sorter
from cubby.app.watcher import Watcher
from cubby.domain.category import Category, Config, Settings

MB = 1024 * 1024


def _by_cubby_line(snapshot: tracemalloc.Snapshot, label: str, limit: int = 8) -> None:
    groups: dict[str, list[int]] = {}
    for stat in snapshot.statistics("traceback"):
        where = "outside cubby"
        for frame in reversed(stat.traceback):  # innermost frame last
            if "/cubby/" in frame.filename:
                where = f"{frame.filename.split('/cubby/')[-1]}:{frame.lineno}"
                break
        group = groups.setdefault(where, [0, 0])
        group[0] += stat.size
        group[1] += stat.count
    total = sum(size for size, _ in groups.values())
    print(f"--- {label}: {total / MB:.1f} MB live ---")
    for where, (size, count) in sorted(groups.items(), key=lambda kv: -kv[1][0])[:limit]:
        print(f"  {size / MB:7.1f} MB  {count:8d} blocks  {where}")


def profile(n: int) -> None:
    root = Path(tempfile.mkdtemp())
    # The agent's lock and state stay in the throwaway root.
    os.environ["CUBBY_STATE_DIR"] = str(root / "state")
    try:
        source = root / "downloads"
        source.mkdir()
        aged = time.time() - 10_000
        for index in range(n):
            path = source / f"file{index:06d}.pdf"
            path.write_text("content", encoding="utf-8")
            os.utime(path, (aged, aged))
        config = Config(
            settings=Settings(source=source, delay=0, content_scan=False),
            categories=(
                Category(name="Documents", extensions=frozenset({"pdf"}), strong_ext=True),
            ),
        )
        journal = Journal(root / "state" / "journal.jsonl")
        sorter = Sorter(config, journal=journal, ledger=Ledger(root / "state"), mode="watch")
        agent = Watcher(sorter, 60.0, ledger=Ledger(root / "state"))
        peaks: dict[str, float] = {}
        real_compact = Journal.compact

        def compact(self: Journal) -> None:
            peaks["loop"] = tracemalloc.get_traced_memory()[1] / MB
            _by_cubby_line(tracemalloc.take_snapshot(), "end of the per-file loop")
            tracemalloc.reset_peak()
            real_compact(self)
            peaks["compaction"] = tracemalloc.get_traced_memory()[1] / MB

        Journal.compact = compact  # type: ignore[method-assign]
        tracemalloc.start(25)
        moved = agent.run(max_cycles=1)
        tracemalloc.stop()
        print(
            f"moved {moved}; traced peak of the loop {peaks['loop']:.1f} MB "
            f"({peaks['loop'] * MB / n:.0f} B a file); of compaction {peaks['compaction']:.1f} MB "
            f"on a {journal.path.stat().st_size / MB:.1f} MB journal"
        )
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", type=int)
    profile(parser.parse_args(argv).files)
    return 0


if __name__ == "__main__":  # pragma: no cover - a script, not an import
    raise SystemExit(main())
