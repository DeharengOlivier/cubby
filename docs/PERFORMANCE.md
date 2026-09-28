# Performance and capacity

What sorting costs, how it grows with the size of the folder, where it stops being
comfortable, and what to change then. Re-run with `benchmarks/bench_sort.py`.

## Method

- `python benchmarks/bench_sort.py 1000 10000 20000 --repeat 5` and
  `python benchmarks/bench_sort.py 200000 --repeat 3`, at commit `f816371`, 2026-09-28
  (the section "Memory of one pass" below has its own method, from 50 000 to 400 000 files).
- Each repetition runs in a fresh process on a fresh folder of settled `.pdf` files, with
  content reading off, and with the undo journal and the run ledger the agent writes: the
  cost measured is the agent's.
- Three timings per repetition: **plan** (nothing moved), **apply** (one pass moving every
  file), and **idle** (the next pass, once the folder is sorted: what the agent pays at every
  interval in the steady state). Apply is also measured in CPU time of the process.
- Reported: the median and the slowest repetition. With 3 to 5 samples a p90 or p99 is the
  slowest sample, so it is reported as such rather than under a percentile's name.
- Machine: Linux 6.8, AMD EPYC (12 vCPU), 47 GB RAM, ext4 on a virtual disk, Python 3.11.16.
  The machine was shared with other workloads (load average 17 to 20 during the runs), so
  the wall-clock columns carry contention; the CPU column is the steadier one.

## Results

The table of 0.3.0, kept as the record it was. Its memory column is the peak of one process
that planned, then applied, then idled, with the plan's list still held during the apply;
the next section measures the pass alone.

| Files | Plan | Apply | Apply CPU | CPU per file | Idle pass | Peak memory |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1,000 | 0.06 / 0.08 s | 1.12 / 1.29 s | 0.72 / 0.73 s | 721 us | 0.00 / 0.00 s | 1 MB |
| 10,000 | 0.43 / 0.74 s | 7.94 / 15.45 s | 5.98 / 7.74 s | 598 us | 0.00 / 0.00 s | 14 MB |
| 20,000 | 0.96 / 1.06 s | 15.07 / 18.48 s | 11.08 / 11.82 s | 554 us | 0.00 / 0.00 s | 29 MB |
| 200,000 | 12.91 / 12.92 s | 141.16 / 166.70 s | 124.57 / 134.07 s | 623 us | 0.01 / 0.01 s | 528 MB |

Median / slowest. The 200 000 row is ten times the largest size tested before (audit 1 went
to 20 000).

Reading the undo journal, which `cubby history` and `cubby undo` do, measured separately
(`journal_probe2`, method below) on a journal of 2 000 runs of 100 moves still to undo
(200 000 moves, 29.9 MB), CPU time, median / slowest of 5:

| Command | Before (0.2.0) | After |
| --- | ---: | ---: |
| `cubby history` | 6.42 / 6.58 s | 1.58 / 1.59 s |
| `cubby undo` (latest run with something left) | 6.20 / 6.76 s | 1.48 / 1.53 s |
| `cubby undo --run ID` | 5.97 / 6.47 s | 1.29 / 1.39 s |

Before, every command built an entry, with two `Path` objects, for each of the 200 000 moves
(65% of the time, per the profile). Now each line is validated once into plain fields;
`history` only counts them, and `undo` builds the entries of the one run it reverts. A
property test checks that every read answers exactly what the reader of 0.2.0 answered,
damaged lines included, against a frozen copy of that reader (`tests/test_journal_reads.py`,
`tests/journal_reference.py`). The probe writes the journal directly, then times
`recent_runs`, `Journal.last_pending_run` and `Journal.run` in one process.

Peak memory (`tracemalloc`) on the same journal: `cubby undo` 200 MB before, 126 MB after;
`cubby undo --run ID` 200 MB before, 89 MB after. The full read (`Journal.runs`, which no
command uses any more) went from 200 MB to 254 MB, as it now holds the raw fields before
building the entries.

## Memory of one pass

Audit 3 (PRF-04, SCL-02) found the memory of one pass unbounded: about 2.6 KB per file, and a
breaking point near 400 000 files at 1 GB that was extrapolated, not measured.

**Method.** `python benchmarks/bench_sort.py 50000 100000 200000 --repeat 3` and
`python benchmarks/bench_sort.py 400000 --repeat 1`, the same harness run against 0.3.0
(`b6dd32b`, before) and against this change (after), 2026-09-28. The harness now measures
the plan and the agent's pass in two processes of their own, each on a fresh folder: the
memory a plan frees stays resident and would be reused by a pass run after it, hiding what
the pass needs. The pass is the agent's own (`Watcher`, one cycle, journal and ledger), not
`sort_once`. Memory is the growth of the peak resident set (`ru_maxrss`) over the process
before the phase, the largest of the repetitions. Same machine as above; load average 19 to
42 during these runs, from other workloads and, for part of them, from the other benchmark
running beside it, so the wall-clock columns are noisy and the CPU column is the one to
compare.

**Where the memory was.** `tracemalloc` over one pass of 50 000 files, grouped by the cubby
line that allocated it (`python benchmarks/profile_pass.py 50000`):

| Held by | 0.3.0 | After |
| --- | ---: | ---: |
| outcome of every file, kept to the end of the pass (`SortOutcome`, its destination `Path` and its text, the rule text) | 33.8 MB (680 B a file) | none |
| folder listing held as `Path` objects, with the text each caches when stat'ed | 23.5 MB (490 B a file) | names only (72 B a file) |
| **traced peak of the per-file loop** | **58.1 MB** | **6.2 MB** |
| **traced peak while compacting the 21.7 MB journal**, what the loop left included | **121.8 MB** | **5.2 MB** |

In 0.3.0 compaction held the journal's whole text, its lines and every line's parsed fields
at once: 64 MB on top of the loop, three times the journal.

The outcomes were a little over a quarter of that peak, the listing a fifth; compaction, which
the audit did not name, was the largest single cost (half), and it grows with the journal (every
move still undoable), not with the pass.

**What changed.** The agent's pass hands each outcome to its caller and keeps counts
(`Sorter.sort_pass`, `PassTally`); the watcher gathers what its alerts need as files fail
(the first 1 000 names, how many are new, the first three to name). The listing is sorted as
names and each path made when its turn comes. Compaction streams the journal twice: a census
of sequence numbers per run, then the kept lines into the staged file.

**Before and after**, peak resident growth, largest of the repetitions:

| Files | Pass, 0.3.0 | Pass, after | Plan, 0.3.0 / after | Apply CPU per file, 0.3.0 / after |
| ---: | ---: | ---: | ---: | ---: |
| 50,000 | 130 MB | 6 MB | 66 / 66 MB | 860 / 647 us |
| 100,000 | 260 MB | 13 MB | 132 / 132 MB | 722 / 850 us |
| 200,000 | 522 MB | 26 MB | 264 / 264 MB | 726 / 901 us |
| 400,000 | 1,046 MB | 54 MB | 535 / 530 MB | 1,019 / 843 us |

After the change, at 1 000 and 20 000 files (5 repetitions), the pass holds under 1 MB and
4 MB. The 400 000 row is one real run on each side, not an extrapolation. It confirms the audit's
estimate for 0.3.0 (1 046 MB for one pass) and measures the new ceiling: 400 000 files moved
in one pass in 618 s of wall clock (337 s of CPU) holding 54 MB, then an idle pass of 0.01 s.

- The pass now costs about **135 bytes per file** (2.6 KB before): the sorted listing of
  names, which a pass must hold to go through the folder in order, and nothing per outcome.
  It is still linear, twenty times flatter: a million files would be about 135 MB
  (extrapolated, not measured).
- CPU per file is unchanged within the noise of this machine (722 to 1 019 us before, 647 to
  901 us after, at load averages of 19 to 42); the change adds no work per file.
- `cubby plan` and `cubby run` still hold every outcome, 1.3 to 1.4 KB per file, because they
  print every file, grouped by folder and sorted by name, which needs them all before the
  first line. Streaming that list would change the output, so it stays. They gain from the
  listing and compaction changes: `cubby run`'s path (`sort_once` applied, journal, ledger,
  then the rendered text) over 200 000 files, one run each, went from 515 MB to 275 MB.
  `plan` does not compact and lists the folder once, so its memory did not move.
- Compaction now holds the sequence numbers of the journal's runs, not its lines: 0.24 times
  the journal's size in the profile above, 0.37 in `test_compaction_memory_is_a_fraction_of_the_journal`
  (200 runs of 100 moves), where it held three to four times the journal.

The invariants are pinned in `tests/test_bounded_pass_memory.py`: the agent's pass never
holds more than the outcome being made; the ledger line, counts and alerts equal those built
from the full list; journal sequence numbers still count failed files; the listing order is
that of the sorted paths; compaction keeps exactly what 0.3.0 kept (property test against a
frozen copy, `tests/compaction_reference.py`), never reads the whole file, and stays under
half the journal's size; a pass of 2 000 files stays under 300 bytes a file.

## What the numbers say

- **Apply is linear**: 0.55 to 0.72 ms of CPU per file at every size, one link, one unlink,
  one appended journal line and a few stats per file. There is no quadratic step.
- **The steady state is free**: once the folder is sorted, a pass costs a directory listing
  of the top level only (10 ms at 200 000 files, which sit in category folders the
  scan does not enter).
- **Memory of the agent's pass is small and linear**: about 135 bytes per file (54 MB at
  400 000 files, measured), the sorted listing of names. It was 2.6 KB per file in 0.3.0
  (section above). `plan` and `run`, which print every file, hold 1.3 to 1.4 KB per file.

## A defect this benchmark found

The first run of this benchmark showed an idle pass of **11.9 s at 200 000 files** (0.00 s at
20 000). A profile at 60 000 files put 6.8 s of the 6.9 s pass in journal compaction: past
5 MB, every pass re-read the whole journal to drop runs, and dropped nothing because every
entry could still be undone. The agent would have spent 12 s of every 30-second interval
doing nothing. Compaction now waits until the journal has doubled since its last attempt
(`Journal.compact`, test `test_a_journal_that_cannot_shrink_is_not_reread_every_pass`); the
idle pass at 60 000 files went from 6.89 s to 0.001 s, and at 200 000 from 11.9 s to 0.01 s.

## Limits and the next step

| Limit | Where it is reached | Effect | Next step if it matters |
|---|---|---|---|
| First sort of a very large folder | ~200 000 files: 2 to 3 min; 400 000 files: 10 min (337 s of CPU) on this loaded machine | the lock is held for the whole pass; a manual `cubby run` meanwhile waits, then stops with "another cubby process is sorting" | none needed: a one-off, and a stop request ends the pass between two files |
| Memory of the agent's pass | 135 bytes per file: 54 MB at 400 000 files (measured), about 135 MB at a million | none on any machine that runs a desktop | the listing itself: sort names in chunks on disk, only if folders of tens of millions of files appear |
| Memory of `cubby plan` and `cubby run` | 1.3 to 1.4 KB per file: `plan` 530 MB at 400 000 files, `run` 275 MB at 200 000 (measured) | a first manual sort of a very large folder on a small machine; the agent is not affected | print as it goes, in folder order rather than grouped (an output change), or group through a temporary file |
| Reading a large journal | 200 000 undoable moves: 1.3 to 1.6 s for `history` or `undo` (was 6 s) | interactive commands wait a second | a per-run index, so a command reads only the lines it needs |
| Journal size | 150 to 400 bytes per move (it records both paths), never dropped while undoable | 30 to 80 MB after 200 000 moves nobody undid | an age limit on undo, if users ask for one |

A Downloads folder with a few thousand files is two orders of magnitude below every limit.

## Single writer

Cubby is one process per user sorting one folder, serialized by a lock (see
`docs/architecture.md`, "One writer"). Nothing here scales out and nothing needs to: the
capacity questions are the ones in the table above.
