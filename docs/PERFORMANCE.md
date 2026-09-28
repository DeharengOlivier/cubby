# Performance and capacity

What sorting costs, how it grows with the size of the folder, where it stops being
comfortable, and what to change then. Re-run with `benchmarks/bench_sort.py`; latency
percentiles with `benchmarks/latency.py` (section "Latency percentiles").

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

Rechecked after rebasing onto the terminal-escaping and extraction-failure changes (main at `1f092af`):
three passes of 200 000 files, run one at a time with `bench_sort.py --single-pass 200000`,
held 19.0, 18.9 and 20.2 MB (190 to 250 s of CPU, load average 27 to 85). The full harness
could not be used for that check: at a load average near 100 its per-repetition budget
(6 s per 1 000 files) ran out. The table above keeps the first measurement.

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

## Latency percentiles

The sections above give the median and the slowest of a handful of runs. This one measures
how the wait is spread, on the paths someone waits on, over samples large enough for a
percentile: `python benchmarks/latency.py` (defaults below), smoke-tested by
`tests/test_latency_benchmark.py`.

**Method.**

- **move**: one file of the agent's pass, from the outcome of the file before to its own
  (eligibility, classification, the move and its journal line), stamped with
  `time.perf_counter_ns` inside the real `Watcher` pass. The first file of each pass is left
  out, its interval also holds the folder listing. 30 passes of 1 000 files: 29 970 moves.
- **pass** and **idle pass**: one pass of the agent (lock, journal, ledger, compaction,
  heartbeat) over a fresh folder of 1 000 settled `.pdf` files, each in a process of its
  own, then the next pass once the folder is sorted. 30 of each. As in `bench_sort.py`, the
  agent is built without its file log, so the log line per move is not in these two rows
  (it is in `cubby run`).
- **cubby COMMAND**: the wall time of the command as typed, `python -m cubby ...`, each of
  the 50 invocations a fresh process (interpreter start and imports included;
  `cubby --version` is that floor). The state is a full one: a ledger at its 2 000-run
  limit, a journal of 50 000 moves still undoable (500 runs of 100, 10.4 MB), a folder of
  1 000 files, and the default categories. The commands take turns in each round, so a
  change of load spreads over all of them; each round starts from a copy of the same state,
  and `run` is followed by the `undo` that puts the folder back. `explain` is asked about one
  file of the folder; `status` finds no agent installed.
- Percentiles are nearest-rank. Below 100 samples a p99 is the maximum (the rank rounds up
  to the last sample): the p99 of the passes and of the commands is their slowest run, and
  only the move row has a p99 of its own. The p95 of those rows rests on few samples too: it
  is the 2nd slowest of 30 (rank 29) and the 3rd slowest of 50 (rank 48).
- Every child runs with `HOME`, `CUBBY_STATE_DIR`, `CUBBY_CONFIG` and `TMPDIR` in a
  throwaway folder, and without any `XDG_*` or other `CUBBY_*` variable.
- Conditions, as the script records them: AMD EPYC (12 vCPU), 47 GB RAM, Linux 6.8.0,
  Python 3.11.16, ext4, cubby 0.4.0, 2026-09-28: the measured `src/` is exactly tag `v0.4.0`
  (`git diff v0.4.0 -- src` is empty), with `benchmarks/latency.py` as committed in the pull
  request that added this section (the script printed `8634d6e-dirty`, a branch commit
  later rebased; only the changelog was being edited during the run). The machine was shared: load average 21.0 to 25.9 in
  run 1 and 22.7 to 26.9 in run 2 (1, 5 and 15 minutes, before and after), on 12 CPUs. Two
  full runs, one after the other; run 1 took 7 min 40 s, the duration of run 2 was not
  recorded.

**Budgets**, at p95, set before measuring:

- one move, 10 ms: a first pass of 1 000 files then ends within 10 s. Its CPU cost is about
  0.6 ms (section "Results"); the rest is room for the disk and a busy machine.
- a pass of 1 000 files, 10 s: a third of the default 30-second interval, and the pass holds
  the lock a manual command waits behind.
- an idle pass, 100 ms: paid every interval, forever; under 0.4% of 30 s.
- `status`, `history`, `explain FILE` (and the `--version` floor), 1 s: the usual limit for a
  reply to feel immediate, and these are the commands asked "what happened?".
- `plan` of 1 000 files, 2 s: it lists all of them, and is read, not waited on repeatedly.
- `run` and `undo` of 1 000 files, 5 s: a manual one-off on a large folder, 5 ms a file.

**Results**, milliseconds unless marked, first run; the last column is the p95 of the second
run, as a check of how much the load moves it:

| Path | n | p50 | p90 | p95 | p99 | max | p95 budget | Met | p95, run 2 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| move | 29,970 | 0.50 | 2.00 | 5.19 | 16.13 | 125.47 | 10 | yes | 8.37 |
| pass, 1 000 files | 30 | 1.32 s | 1.97 s | 2.32 s | 2.85 s | 2.85 s | 10 s | yes | 2.74 s |
| idle pass | 30 | 0.84 | 1.77 | 4.48 | 5.04 | 5.04 | 100 | yes | 39.2 |
| `cubby --version` | 50 | 232 | 343 | 388 | 579 | 579 | 1 000 | yes | 651 |
| `cubby status` | 50 | 279 | 407 | 439 | 875 | 875 | 1 000 | yes | 695 |
| `cubby history` | 50 | 821 | 1 040 | 1 140 | 1 350 | 1 350 | 1 000 | **no** | 1 736 |
| `cubby explain FILE` | 50 | 248 | 333 | 392 | 618 | 618 | 1 000 | yes | 724 |
| `cubby plan` | 50 | 410 | 537 | 604 | 669 | 669 | 2 000 | yes | 1 043 |
| `cubby run` | 50 | 3.62 s | 4.82 s | 5.68 s | 7.13 s | 7.13 s | 5 s | **no** | 6.60 s |
| `cubby undo` | 50 | 1.74 s | 2.21 s | 2.84 s | 3.32 s | 3.32 s | 5 s | yes | 3.65 s |

**What they say.**

- A move is half a millisecond at the median; the tail is the machine, not the file: p99
  16 to 25 ms and a slowest of 125 to 217 ms, on files that are all alike. A whole pass of
  1 000 files stays under 3 s at p99, and the steady state under 50 ms.
- Interpreter start and imports are most of an interactive command: `--version` alone is
  230 to 320 ms at the median, `status` and `explain` add 16 to 99 ms to it at the median.
- Load moves the tails a lot: the same code measured a p95 up to 1.7 times higher in the
  second run (load 22.7 to 26.9 instead of 21.0 to 25.9). A budget met in one run and missed in the
  other would be a noisy verdict; the two missed below are missed in both.

**Findings** (measured, not optimized here):

- **`cubby history` misses its budget with 50 000 undoable moves**: p95 1.14 and 1.74 s,
  median 0.82 to 1.06 s, against 0.28 to 0.42 s for `status`, which reads the same ledger.
  What `history` adds is reading the whole 10.4 MB journal to tell which runs are undone:
  the "Reading a large journal" limit below, now with its percentiles.
- **`cubby run` of 1 000 files is 2.7 times the agent's pass over the same number** (median
  3.62 s against 1.32 s) and misses its 5 s budget (p95 5.68 and 6.60 s). The probe
  `python benchmarks/run_journal_cost.py` (defaults: the same state, 10 rounds), run once
  on the same `src/` (tag `v0.4.0`) in a throwaway `HOME`, load 31.6 before and 24.2 after, says where the
  difference goes. Its output, whole:

  ```text
  cubby run of 1000 files, journal of 50000 moves (10.4 MB), 10 rounds:
    journal as built: median 5.92 s, slowest 7.28 s
    journal removed: median 4.37 s, slowest 5.70 s
  one cubby run under cProfile, cumulative:
    the pass (sort_pass): 5.62 s
    journal compaction (Journal.compact): 1.99 s
    the moves (move_into): 1.79 s
    the log line per move (logging.log): 0.58 s
  ```

  The load was higher than during the latency runs, so every figure is higher than in the
  table above; compare them to each other. The journal adds 1.55 s at the median, and
  compaction is about a third of the profiled pass: one read of the 51 000 lines that
  parses each twice (once for its run, once for its fields), to keep them all, since every
  move is still undoable. The log line written per move (which the agent rows above leave
  out) is a tenth.

  The compaction is the one that is not the file's own cost. The agent tries it again only
  once the journal has doubled since its last attempt (section "A defect this benchmark
  found"), but that memory lives in the process: every `cubby run` starts without it, so
  past 5 MB each run re-reads a journal it cannot shrink. The next step, if it matters, is
  to keep the size at the last compaction in the state folder.

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
