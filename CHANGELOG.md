# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com) and this project adheres to
[Semantic Versioning](https://semver.org).

## [Unreleased]

### Added
- `benchmarks/latency.py` measures how long you wait: p50, p90, p95, p99 and
  the slowest, with the sample size, for each file the agent moves, a whole
  pass, and `status`, `history`, `explain`, `plan`, `run` and `undo` run
  fresh against a full ledger and journal, each against a stated budget.
  Results in `docs/PERFORMANCE.md`, "Latency percentiles";
  `benchmarks/run_journal_cost.py` shows what a large journal adds to `cubby run`.

### Changed
- The required `review record` check passes only when the review record names
  the pull request's current head commit, on a line
  `Reviewed head: <full 40-digit SHA>` as plain text (not in a code block or
  an HTML comment). A push after the review, from a branch or a fork, turns
  it back to failure until a re-review record names the new head; a record
  posted for an older commit no longer counts. The check now runs the
  default branch's copy of its workflow, so editing that workflow in a pull
  request no longer changes how the pull request is judged.

- Output escaping is enforced, not just tested: a new message that would
  print a file name unescaped fails the type check or
  `tests/test_output_escaping.py` in CI. What cubby prints is unchanged.

### Fixed
- The test suite could write to the real state folder of whoever ran it: a
  run of the suite put two "a line from the test suite" lines in the
  maintainer's `~/.local/state/cubby/cubby.log` (most likely a mutated
  `log_path()` ignoring the per-test `CUBBY_STATE_DIR`, which reproduces them;
  the attribution is inferred), and the check meant to catch it only looked
  for temp paths in those files. The suite now points `HOME` and the
  XDG folders at a session folder before any test runs, and fails the whole
  run, naming the files, when anything under the real home's cubby state,
  config, log or agent unit appeared, changed or disappeared, and says how to
  get past a cubby agent running on the same machine. `make test` and `make
  mutation` run under a throwaway `HOME`, and `make mutation` fails when a
  mutant touched the real state (mutmut alone counts it as killed). Tests
  only: the installed cubby is unchanged.

## [0.4.0] - 2026-09-28

### Added
- `make flaky-rate` measures how often CI fails on a commit that later passes
  unchanged, against the 2% budget in `docs/READINESS.md`.
- A pull request cannot merge until its independent review record is posted
  on it (the required `review record` check).
- The run ledger counts, per run, the files whose content was lost to broken
  converters (`extraction_failures`), apart from failed moves: such a file
  was sorted, by name and type. A file another converter rescued (pypdf after
  pdftotext, for example) is not counted.
  `cubby status` shows the 24-hour total when it is not zero (`content
  unreadable for N`), and `status --json` and `history --json` carry it
  (`last_run.extraction_failures`, `activity.extraction_failures`,
  `runs[].extraction_failures`). Older ledger records read as 0.
- The `status` and `history` JSON Schemas require `extraction_failures`
  (`last_run`, `activity`, `runs[]`): output from cubby 0.3 does not validate
  against them.

### Changed
- One pass of the agent holds about 135 bytes per file instead of 2.6 KB:
  54 MB at 400 000 files, measured (522 MB at 200 000 before). The pass
  counts its outcomes instead of keeping them, lists the folder as names,
  and compacts the journal by streaming it instead of reading it whole,
  which cost three times the journal's size. Moves, journal and ledger
  lines, alerts, `status`, `run` and `plan` output and exit codes are
  unchanged. `cubby plan` and `cubby run` still keep every outcome to print
  it, 1.3 to 1.4 KB per file (`run` over 200 000 files went from 515 MB to
  275 MB); `docs/PERFORMANCE.md`, "Memory of one pass".
- `benchmarks/bench_sort.py` measures the plan and the agent's pass in
  separate processes, and `benchmarks/profile_pass.py` shows where the
  memory of a pass goes.
- The README describes every feature, each with a real example and a link to
  its detailed doc, and ends with a reference of every command and flag.

### Fixed
- On macOS the background agent wrote its log to `~/.local/state/cubby/cubby.log`
  while `cubby log` and `cubby status`, run from a shell, read
  `~/Library/Logs/cubby.log`, where launchd only puts the agent's own output:
  `install` names the state folder to the agent, and naming it was taken to
  mean a state folder chosen elsewhere. The log now follows the folder: the
  default one (`~/.local/state/cubby`, or `$XDG_STATE_HOME/cubby`, which
  `install` now passes on to the agent) logs to `~/Library/Logs` whether or not
  it is named; a folder chosen elsewhere keeps its log beside it. The log of an
  agent from 0.2 or 0.3 stays in its state folder, and an agent rolled back to
  0.3 logs there again. With `XDG_STATE_HOME` set, run `cubby install` again
  after upgrading so the agent is given it.
- A folder whose name matches an invoice rule was renamed and filed like an
  invoice: `invoice-archive` became `Invoices/2026-09/archive facture`. A
  folder matched by its name is now moved whole under its own name, at the
  category's root (`2026-09 (folder)` when its name is a month folder's, so
  later invoices are never filed into it). `docs/usage.md` wrongly said every folder goes to
  `_Unsorted`; only a folder no rule names does.
- `cubby install --no-content` was accepted but not passed on to the agent,
  which went on reading file contents. The agent now runs with it.
- `cubby doctor` lists `catdoc`, which content extraction already used.
- A content converter that broke on a file (it timed out, crashed, ran out of
  memory or exited non-zero, `pdftotext` or the parser child for PDF, docx
  and xlsx alike) was indistinguishable from a file with no text: the file
  fell back to its name and type rules, as designed, with no trace. The
  fallback is unchanged, and a file left with no text this way now writes a
  WARNING log line with the run id, the file, each converter that broke and
  the kind of failure (`timeout`, `exit`, `crash`, `error`). A file whose
  text a later converter still read is sorted by its content and not
  reported. The parser child exits with status 3 and names the
  exception instead of printing nothing. A converter that is not installed is
  still not a failure (`cubby doctor` reports it).
- A ledger record whose `moved`, `failed` or `extraction_failures` is not a
  non-negative integer is skipped as damaged, like any other damaged line;
  `status --json` printed it as it was, outside its own schema.

### Security
- A file name can no longer drive the terminal. Control characters (C0,
  DEL, C1) and bidi overrides or isolates in a name reached the output of
  `plan`, `run`, `undo`, `explain`, `log`, `status` and `doctor` raw, so a
  file planted in Downloads could clear the screen or forge those reports.
  Every human output now shows them as visible escapes (`\x1b`, `\u202e`)
  and doubles a backslash, so each shown name stands for one real name;
  ordinary names (accented, CJK, emoji, no-break and other spaces,
  subdivision flags) read as they are.
- `--json` outputs (`plan`, `explain`, `history`, `status`) wrote DEL, C1,
  bidi and other format characters, line separators and lone surrogates
  raw (the JSON encoder escaped C0 only). They are now written as `\uXXXX`
  escapes: the output decodes to the same values, and accents, CJK and
  emoji stay literal. `log --json` and the log file were already fully
  escaped.
- An `OSError` message naming a hostile file shows each escape once
  (`\x1b`), no longer doubled by Python's own quoting (`\\x1b`).
- The `notify-send` body is markup: `&`, `<` and `>` in a file name are
  now escaped there, and control characters are escaped in notifications
  on every platform.

## [0.3.0] - 2026-09-28

### Added
- `cubby status` measures the agent's last pass: how long it took, what it
  moved or failed, and how many files still wait to settle. It also sums up
  the last 24 hours: runs, moves, failures, and the failures grouped by kind
  of error (the file name blanked out), with the files and cubby versions
  that hit each. `--json` has them as `agent.last_pass` and `activity`.
  When the ledger holds its full 2000 runs, older ones may have been trimmed,
  and `status` says the 24 hours may be incomplete (`activity.complete`).
- `scripts/rebuild.sh` checks that the wheel and sdist are reproducible, and
  with `--against SHA256SUMS` that a release matches its tagged source. CI
  runs it on every pull request, the release workflow against the artifacts
  it publishes (`SECURITY.md`, "Checking a release").
- JSON Schemas for every `--json` output in `docs/schemas/` (`plan`, `status`,
  `history`, `explain`, `log-record`), checked against the real outputs by
  the test suite. The schemas allow fields added later within a version.
- `cubby log --json` prints nothing when there is no log yet (it printed a
  text line), and turns a line cubby did not write into `{"msg": "<line>"}`.
- `cubby log`: the agent's log, oldest first and including the rotated file,
  with `--run ID` (one pass), `--warnings`, `-n N` and `--json`.
- Every log line names the cubby `version` that wrote it, and each line written
  during a pass names its `run` id; a move's line ends with where the file
  went (`notes.txt -> Documents/notes (1).txt`), or that it was a deleted
  duplicate; the summary and failure lines of an agent pass carry its run id
  too. The run id is the one
  `cubby history`, `cubby undo --run` and the ledger use. Ledger records carry
  the version too; older records read as `unknown`.

### Fixed
- `cubby undo` left behind every category and month folder the run had
  filled, empty; it now removes a folder it emptied (an empty one only).
  A file that could not be moved was reported as a raw Python exception with
  two absolute paths; it now reads `PermissionError: Permission denied:
  fresh.pdf -> Documents/fresh.pdf (check cubby's permissions there)`, paths
  relative to the sorted folder. The docs say that a folder in the watched
  folder is moved whole into `_Unsorted`, and how to keep it. Found by an
  exploratory session.
- Small CLI defects found by an exploratory session: `watch -v` now echoes
  when its output is not a terminal; `--source` pointing at a folder cubby
  files into (`~/Downloads/Documents`) is refused instead of nesting
  `Documents/Documents`; `status` says `last watched` once nothing is
  sorting; `doctor` says when no notification tool can show an alert; a
  missing source says whether it came from `--source` or a config file;
  `log --run` with an unknown id says so and exits 1; a bad `--delay` or
  `--interval` names the flag (exit 2); two categories with one name are
  refused. The config file is also looked for in
  `$XDG_CONFIG_HOME/cubby/config.toml`, where `cubby init` then writes it;
  `cubby install` passes that variable to the agent, which launchd and
  systemd would not, so the agent reads the same file. A relative value is
  ignored, as the XDG spec says. A `--source` that is a folder cubby files
  into inside the watched folder is refused (exit 2), also when a part of
  its path differs only by case on a case-insensitive disk (not verified on
  macOS by a test).
- `cubby undo` moved whatever file stood where the run had put one: a file the
  user put under the same name afterwards was taken out of its folder, and a
  duplicate was recreated from whatever replaced the kept copy. Each move now
  records the file's device, inode, size and modification time, and undo
  leaves a file that differs in place and says where it would have gone. A
  file edited since the run is left in place too. A folder, whose size and
  date change with its content, is recognised by one file it held when it
  was moved, still inside it and unchanged. Found by an
  exploratory session.
- `cubby undo` settled a file as gone for good when a folder on the way to it
  was unreadable; it now stays pending and is retried once the folder can be
  read.
- With `dedupe = true`, two unreadable files of the same size counted as
  identical, and a file already filed as a symlink counted as a copy of the
  file it points to: in both cases the only copy could be deleted. Neither
  counts as a duplicate any more.
- `cubby undo` exited 0 when some files could not be put back because they
  had been moved or deleted since the run; it now counts them and exits 1,
  as the exit codes say. A file restored under another name because its name
  was taken says so (`restored notes.txt as notes (1).txt`).
- The previews now show what a run does. With `dedupe = true`, `plan`,
  `explain` and the run summary announced a move for a file the run then
  deleted as a duplicate; they now list it under "Would delete as duplicates"
  / "Deleted as duplicates" (`duplicate_of` in `--json`). `plan` no longer
  lists downloads still in progress as going to `_Unsorted`. `plan` and `run`
  list what they left alone and why (`left_alone` in `--json`), and name a
  file standing where a category folder goes (`blocked`). `explain` says
  first whether a file stays where it is, then where it would go. A duplicate
  made within the same pass (two identical files renamed to the same invoice
  name) is predicted too, and `plan` counts a file bound for a folder blocked
  by a file (a category or a month folder) under "Would fail", not as a move.
  In `plan --json`, `count` no longer includes downloads in progress. Found by
  an exploratory session.
- Invoice renaming made a vendor up from generic words (`Invoices.pdf` became
  `invoices facture ...`, `Your bill.pdf` became `your facture ...`); such
  words are no longer a vendor, and the original name is kept. A date in the
  file name (`Invoice-2026-08-spotify.pdf`, `Facture EDF août 2026.pdf`) is
  now used when the document has none, instead of the download date, and the
  download date is no longer written into the new name as if it were the
  invoice's; a name date later than the download is not taken. The content
  stage recognises an invoice by its title: "facture" or "invoice" opening
  one of the first two lines with a date or an amount on that line
  (`Facture Free Mobile du 05/08/2026 montant 19,99 EUR`), or standing alone
  as a heading (`Facture n° 1042`) with one below; a statement, a contract
  or an email that only mentions an invoice keeps its own category (these
  title patterns are a new `late_content_patterns` key, tried only when no
  category's `content_patterns` match), an invoice number prefix
  (`INV-2026-0815`, `FA-102938`) is neither a vendor nor a month,
  and `explain` says when it read a file's content and nothing matched
  (`content_chars` in `--json`). Found by an exploratory session.
  A config written by `cubby init` keeps its own copy of the categories: to
  get the new Invoices `late_content_patterns`, copy them from the packaged
  defaults (`cubby init --force --path /tmp/cubby-defaults.toml` writes them
  there and leaves your own config untouched).
- A regular file named like a folder cubby sorts into (`_Unsorted`, a
  category, or a month folder inside one) made every move into that folder
  fail with `[Errno 17] File exists`. The file is still left alone, and the
  error now names it: `a file named _Unsorted is in the way of the folder
  cubby sorts into; rename or move it`.
- A line of the journal, the ledger, the heartbeat, the log or the pause file
  nested a few thousand levels deep raised `RecursionError` and stopped every
  read of that file, undo and compaction included. It now counts as a damaged
  line, like any other.
- A config file nested a few thousand levels deep ended in a `RecursionError`
  traceback; it is now reported as invalid TOML (exit 2).
- Journal compaction dropped a run whose id was not a string (a hand-edited
  or foreign line such as `"run": 7`) while `cubby undo` could still revert
  it: the reads took such an id as `"7"`, compaction ignored it.
- A stop request (SIGTERM from launchd or systemd) that arrived while the
  agent slept between passes started one more pass before the agent exited.
  That pass moved nothing, but it took a new run id and overwrote the
  heartbeat. The agent now exits without it.
- Run ids carry 64 random bits instead of 32. Two runs started in the same
  second could draw the same id (CI saw it once in 5 000 draws), and their
  moves then shared one journal entry list: undoing one reverted both. Older
  ids stay valid.
- A move whose source could not be removed (a read-only folder) left the file
  under both names. After a failed undo, the retry then restored a second copy,
  `name (1).ext`, beside `name.ext`. A failed move now changes nothing.
- `cubby uninstall` on Linux reported success while the agent kept running. Both
  backends now check that the agent stopped, and keep the unit when it did not.
- `cubby status` said "not installed" about a cubby that was still sorting; it
  now names the live process (`live_pid` in `--json`).
- After a large sort, every pass of the agent re-read the whole undo journal
  to compact it and dropped nothing: 12 s per idle pass after 200 000 files.
  Compaction now waits until the journal has doubled. Measured in
  `docs/PERFORMANCE.md`.
- `cubby uninstall` exits 1, with the `kill` command, when a `cubby watch`
  process still beats after the service manager said it stopped (a pid reused
  by another program is not mistaken for it). `uninstall.sh` no longer removes
  the CLI when the agent could not be stopped, still removes a broken install
  whose `cubby` cannot start, and takes `--force`.
- The commands that stop the agent (`systemctl disable --now` and `restart`,
  `launchctl unload`) wait 90 s, longer than the 60 s the agent is given to
  finish its file, instead of 30 s.
- An undo entry that could not be restored is printed as `pending`, with the
  `cubby undo --run ID` that retries it, instead of `skip`.
- The runbook, executed by an operator in a throwaway home, had seven false
  claims (stop checks, fallback commands, evidence copy, what undo leaves
  pending, rollback order); rewritten, then executed again by a second
  operator. Both drills are in `docs/audits/`.

### Changed
- `cubby history` and `cubby undo` read a large undo journal about four times
  faster (200 000 moves: 6.4 s to 1.6 s of CPU for `history`, 6.2 s to 1.5 s
  for `undo`): only the run being undone is built into entries.
- `cli.py` became the `cubby.cli` package (sorting, inspection, agent commands),
  and cyclomatic complexity is now capped at 10 by the linter.
- The benchmark measures each size several times in fresh processes, with the
  journal and ledger the agent really writes, and reports the median, the
  slowest run and the idle pass; results in `docs/PERFORMANCE.md`.
- A test fails when a subprocess call has no timeout.

## [0.2.0] - 2026-09-28

Upgrading: the config is now read strictly (see Changed); run `cubby doctor`
after upgrading, then `cubby install` again to refresh the agent unit.

### Added (features)
- `cubby explain FILE...`: where each file would go, the rule that decides it,
  the rename, and why a run would leave it alone. Moves nothing. `--json`.
- `cubby history`: recent runs with their counts and how much of each was
  undone (undoable, partly undone, undone, unknown), so `cubby undo --run ID`
  has something to point at. `--json`.
- `cubby init`: writes a starter config that loads cleanly; never overwrites,
  nor shadows a config cubby already reads, without `--force`, which replaces
  the file atomically.
- `ignore` setting: glob patterns (case-insensitive) of file names cubby never
  touches.
- `cubby plan --json` items carry the deciding rule.
- `cubby pause [--for 2h]` (at most 366 days) and `cubby resume`: the agent
  moves nothing more from the next file on, without uninstalling it. Any pause
  file cubby cannot read counts as a pause (fail closed). `cubby status` shows the pause; a manual `cubby run` still
  works and says the agent is paused.
- Desktop notifications when the agent cannot sort a file, a pass fails, or the
  watched folder goes missing: once per file or per problem, not at every pass.
  `osascript` on macOS, `notify-send` on Linux, the message passed as an
  argument (never inside a script). `notify = false` turns them off;
  `cubby doctor --notify` sends a test.

### Fixed (found by property tests)
- A file whose name contains a Unicode line separator (U+2028, U+2029, U+0085)
  could not be undone: its journal line was read back as two broken lines.
  Journal, ledger and log lines are now split on `\n` only.
- On Linux, a file whose name is not valid UTF-8 was moved, then the journal
  refused its name: the move could not be undone and the CLI reported a
  "config error". State files are written as ASCII JSON and the terminal
  output escapes such names.

### Fixed (audit 1, docs/audits/2026-09-28-audit-1.md)
- Files sorted by the background agent can be undone. `cubby watch` never
  wrote to the undo journal, so everything the agent moved was out of reach of
  `cubby undo`.
- A file that cannot be moved no longer stops the run: it is reported, left in
  place, and the others are sorted. Each move is journaled as it happens, so a
  run that fails or is killed part way can still be undone. `cubby run` exits 1
  when a file could not be sorted.
- Undoing a deduplicated file recreates the duplicate instead of moving the
  copy that was already filed out of its folder.
- A folder with a space, `%`, `$`, a quote or a newline in its name reaches
  systemd as one argument.
- `cubby install` checks what launchd or systemd answer, and that the agent is
  running afterwards, instead of reporting success regardless. Every call to
  the service manager has a timeout.
- `cubby watch` and `cubby install` refuse a source folder that does not exist
  instead of watching nothing forever.
- `cubby status` asks the service manager whether the agent runs, instead of
  reading "running" off the unit file's existence.
- A move can no longer replace a file that appears at the destination at the
  last moment.
- The log goes to `~/.local/state/cubby/cubby.log` on Linux, not
  `~/Library/Logs`.

### Added
- `cubby status` shows when the agent last completed a pass (and flags a stalled
  one), what its last run moved or failed, and exits 1 when an installed agent is
  unhealthy. `--json` for scripts.
- A run ledger (`runs.jsonl`) and an agent heartbeat in the state folder.
- `cubby undo --run ID` undoes a specific run. An entry that fails to restore
  stays pending and is retried by the next `cubby undo`.
- `CUBBY_STATE_DIR` redirects every file cubby keeps for itself.
- `cubby watch` stops cleanly on SIGTERM (what launchd and systemd send): the
  pass ends between two files, so a move is never cut off from its journal
  line, and the units allow 60 s for it.
- Tagged releases are built with a hash-pinned build backend, checked against
  the package version and the main branch, smoke-tested and published on
  GitHub with SHA-256 sums.
- Property-based tests (Hypothesis), an end-to-end agent journey in real
  processes, and targeted mutation testing of the journal, containment and undo.
- `docs/THREAT-MODEL.md`.

### Changed
- The config file is read strictly: an unknown key (a typo such as `ignor`) is
  a config error naming the closest known key, and a switch must be `true` or
  `false` (`dedupe = "false"` used to turn deduplication on). Run `cubby doctor`
  after upgrading to check an existing config.
- The undo journal is append-only and one line per move (format version 2).
  Journals written by 0.1 are still read and undone.
- The log is JSON lines, rotated at 1 MB.
- Only one cubby sorts or undoes at a time (an advisory lock per pass).
- The watch loop survives a failing pass: it logs the error and runs the next
  pass on schedule, and reports an unplugged source folder once.

### Security
- PDF, docx and xlsx parsing runs in a child process with a timeout and a memory
  ceiling, so a hostile document cannot stall or exhaust the agent.
- The journal, ledger, heartbeat and log are created with mode 0600.
- Category names and `unsorted_dir` are validated as single folder components,
  and every move is checked against the watched root. A category named
  `../../escaped` previously moved files two levels above the watched folder.
- Extraction tools are executed by the absolute path resolved from `PATH`,
  rather than by bare name, so an unattended agent cannot be steered to a
  different binary.
- `source` refuses a filesystem root or the home directory itself.

### Fixed
- The undo journal survives an interrupted run: a half-written last line is
  skipped instead of crashing `cubby undo`, rewriting it is staged and swapped,
  and a journal that cannot be written warns instead of failing silently.
- `cubby undo` uses the same move primitive as the sort, so a file that crossed
  a filesystem can now be put back.
- Content extraction is bounded: reading 4000 bytes from a 315 MB file cost
  896 MB of memory and 15 seconds, and now costs neither.
- Every setting is validated when the config loads. `content_max_bytes = -1`
  read whole files, `interval = 0` spun, `delay = -5` moved downloads still
  being written, and unknown `month_style` / `month_lang` values were ignored
  without a word.
- A bad configuration is reported as one line naming the setting and the file,
  with exit code 2, instead of a traceback.
- The test suite no longer writes to the real `~/.local/state/cubby/journal.jsonl`
  and `~/Library/Logs/cubby.log`.

### Added
- Colored terminal output for `plan`, `run`, `doctor` and `status`, and a logo
  banner; color is off when the output is not a terminal or `NO_COLOR` is set.
- Invoice and bank categories (`date_folders`) file documents into a month/year
  subfolder, read from the document's own date (FR/EN) with a fallback to the
  file's modification date.
- Invoices (`vendor_rename`) are renamed to `<vendor> facture <date>`, using a
  configurable known-vendor list plus a filename heuristic; the original name is
  kept whenever the vendor is uncertain.
- `--month-style {numeric,letters}` and `--month-lang {fr,en}` to choose the
  subfolder naming (e.g. `2026-07`, `juillet 2026`, `July 2026`), baked into the
  background agent by `cubby install`.
- `cubby plan` (and `--json`) now previews the month subfolder and any rename.

## [0.1.0] - 2026-06-29

### Added
- Three-stage classification cascade: filename, content, then file type.
- Content extraction with graceful multi-backend fallback (pdftotext, textutil,
  pypdf, python-docx, openpyxl, plain text).
- Generic default categories shipped inside the package (`cubby/data/default.toml`).
- TOML configuration with default + user-file + CLI-override merging.
- CLI: `plan`, `run`, `undo`, `watch`, `install`, `uninstall`, `status`, `doctor`.
- Undo journal: every run is recorded so `cubby undo` can revert it.
- Opt-in `dedupe` to drop byte-identical duplicate downloads.
- `cubby plan --json` for scripting.
- Background agent support via launchd (macOS) and systemd (Linux).
- Collision-safe moves, age delay, and skipping of in-progress downloads.
- Portable `install.sh` / `uninstall.sh`.
- Test suite covering the engine, adapters, use cases and CLI, plus a fuzz test.

[Unreleased]: https://github.com/DeharengOlivier/cubby/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/DeharengOlivier/cubby/releases/tag/v0.4.0
[0.3.0]: https://github.com/DeharengOlivier/cubby/releases/tag/v0.3.0
[0.2.0]: https://github.com/DeharengOlivier/cubby/releases/tag/v0.2.0
[0.1.0]: https://github.com/DeharengOlivier/cubby/releases/tag/v0.1.0
