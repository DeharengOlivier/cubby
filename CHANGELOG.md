# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com) and this project adheres to
[Semantic Versioning](https://semver.org).

## [Unreleased]

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
