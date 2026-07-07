# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com) and this project adheres to
[Semantic Versioning](https://semver.org).

## [Unreleased]

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
