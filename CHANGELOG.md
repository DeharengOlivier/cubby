# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com) and this project adheres to
[Semantic Versioning](https://semver.org).

## [Unreleased]

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
- `cubby pause [--for 2h]` and `cubby resume`: stop the agent moving files at its
  next pass without uninstalling it. A damaged pause file counts as a pause
  (fail closed). `cubby status` shows the pause; a manual `cubby run` still
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

### Security
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

[Unreleased]: https://github.com/DeharengOlivier/cubby/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/DeharengOlivier/cubby/releases/tag/v0.1.0
