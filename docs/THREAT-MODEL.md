# Threat model

Reviewed 2026-09-28 against `SECURITY-CHECKLIST.md` section 1 (standards 2026-09-25.1).
Revisit at each release that adds an entry point, a dependency that parses files, or a new
kind of side effect.

## What cubby is

A command-line tool and a per-user background agent (launchd on macOS, systemd on Linux) that
moves the files of one folder (default `~/Downloads`) into category subfolders of that same
folder. It runs as the logged-in user, holds no credentials and makes no network calls.

## SEC-01-001: sensitive data it handles

| Data | Where | Why it matters |
|---|---|---|
| The user's downloaded documents (invoices, bank statements, contracts, CVs) | the watched folder | personal and financial content; read for classification, never copied elsewhere |
| File names and paths of past moves | undo journal and run ledger under `~/.local/state/cubby/` | reveals what the user downloaded; created with owner-only permissions |
| Activity log | platform log folder (see `docs/configuration.md`) | same as above |

## SEC-01-002: users and privileges

One actor: the local user who installs cubby. Cubby never elevates privileges, never runs as
root by design and refuses a filesystem root or the home directory as its source.

## SEC-01-003: exposed interfaces

None on a network. The interfaces are the command line, the config file and the files that
arrive in the watched folder.

## SEC-01-004: external systems trusted

| System | Trust placed in it |
|---|---|
| `launchctl`, `systemctl --user` | register and run the agent; failures are reported, not assumed away |
| `pdftotext`, `textutil`, `antiword`, `catdoc` (optional) | convert an untrusted file to text; resolved to an absolute path, run as an argument list with a timeout |
| `pypdf`, `python-docx`, `openpyxl` (optional) | parse untrusted files; version-locked in `uv.lock`, scanned by `pip-audit` in CI |
| PyPI and GitHub Actions | supply the dev toolchain; locked by hash and pinned by commit SHA |

## SEC-01-005: secrets

None. Cubby has no credential, token or key. The CI uses only the ephemeral `GITHUB_TOKEN`,
restricted to `contents: read` except in the release job, which needs `contents: write` to
publish a release.

## SEC-01-006: where untrusted data enters

| Entry | Untrusted part | Control |
|---|---|---|
| Files in the watched folder | name, extension, bytes | names are only ever used as one path component inside the watched root; content is read through bounded extractors (size ceiling, byte window, timeout, child process for in-process parsers); nothing is executed |
| Config file (`~/.config/cubby/config.toml`) | trusted as the user's own, like a shell script (see `SECURITY.md`) | every value validated at load; category names are single path components; regex compiled at load |
| Command-line flags | the user's own input | parsed by the same validation as the config |
| Undo journal and run ledger | written by cubby, readable by the user | versioned schema; a damaged line is skipped, never executed; undo only moves inside recorded paths |

## SEC-01-007: operations with major impact if hijacked

| Operation | Impact | Barrier |
|---|---|---|
| Moving a file | a file ends up somewhere unexpected | destination re-checked inside the watched root before every write (`resolve_inside`), no-clobber move, journal for undo |
| Deleting a duplicate (opt-in `dedupe`) | loss of a copy | only byte-identical files (SHA-256), journaled so undo restores it |
| Writing the service unit | a command runs at every login | arguments quoted for the service manager, absolute paths, written only on explicit `cubby install` |
| Running an extractor | code execution through a converter bug | optional, absolute path, no shell, timeout, file size ceiling |

## Security invariants (tested)

- Cubby never writes outside the watched folder, apart from its own state folder and log
  (`tests/test_containment.py`, property tests in `tests/test_properties.py`).
- Every move of every run, manual or agent, can be undone (`tests/test_undo_everything.py`,
  `tests/test_agent_journey.py`).
- A path from the config reaches the service manager as one argument, whatever it contains
  (`tests/test_service_boundary.py`: spaces, `%`, `$`, backslashes, newlines).
- A crafted document cannot stall or exhaust the agent: parsers run in a child process with a
  memory ceiling and a timeout, files past a size ceiling are not read
  (`tests/test_extraction_bounds.py`, `tests/test_review_pr2.py`).
- A downloaded `.py` file is never imported by a parser child (`tests/test_review_pr2.py`).
- A file name with any character the filesystem allows, including Unicode line separators and
  (on Linux) bytes that are not valid UTF-8, keeps an undoable journal entry and is reported
  without crashing (`tests/test_properties.py`, `tests/test_undo_everything.py`).
- No network access: the test suite runs with sockets disabled (`tests/conftest.py`).
