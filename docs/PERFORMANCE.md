# Performance and capacity

What sorting costs, how it grows with the size of the folder, where it stops being
comfortable, and what to change then. Re-run with `benchmarks/bench_sort.py`.

## Method

- `python benchmarks/bench_sort.py 1000 10000 20000 --repeat 5` and
  `python benchmarks/bench_sort.py 200000 --repeat 3`, at commit `f816371`, 2026-09-28.
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

## What the numbers say

- **Apply is linear**: 0.55 to 0.72 ms of CPU per file at every size, one link, one unlink,
  one appended journal line and a few stats per file. There is no quadratic step.
- **The steady state is free**: once the folder is sorted, a pass costs a directory listing
  of the top level only (10 ms at 200 000 files, which sit in category folders the
  scan does not enter).
- **Memory is linear**, about 2.6 KB per file moved in one pass. Measured with `tracemalloc` at
  20 000 files: the outcomes of the pass hold 24 MB (1.2 KB each), and compacting the journal
  peaks at 37 MB while it parses every entry.

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
| First sort of a very large folder | ~200 000 files: 2 to 3 min | the lock is held for the whole pass; a manual `cubby run` meanwhile waits, then stops with "another cubby process is sorting" | none needed: a one-off, and a stop request ends the pass between two files |
| Memory of one pass | ~400 000 files at 2.6 KB each: 1 GB | a very large first sort on a small machine | count outcomes instead of keeping them in `watch` mode; stream compaction over lines instead of parsed entries |
| Reading a large journal | 200 000 undoable moves: 1.3 to 1.6 s for `history` or `undo` (was 6 s) | interactive commands wait a second | a per-run index, so a command reads only the lines it needs |
| Journal size | 150 to 400 bytes per move (it records both paths), never dropped while undoable | 30 to 80 MB after 200 000 moves nobody undid | an age limit on undo, if users ask for one |

A Downloads folder with a few thousand files is two orders of magnitude below every limit.

## Single writer

Cubby is one process per user sorting one folder, serialized by a lock (see
`docs/architecture.md`, "One writer"). Nothing here scales out and nothing needs to: the
capacity questions are the ones in the table above.
