# Readiness register: cubby

Standards version: 2026-09-25.1 (`CODING-RULES.md`, `SECURITY-CHECKLIST.md`).
Last reviewed: 2026-09-28 by the maintainer (Olivier Dehareng), at release 0.3.0.

This file is the project's single record of its level, its baseline and the status of every
applicable control (CODING-RULES section 14, SECURITY-CHECKLIST SEC-GOV-01). Update the rows a
change affects in the same pull request, and review the whole file at each release.

Status: `PASS` (with evidence), `FAIL`, `NOT_VERIFIED` (no current evidence; blocks a release
like `FAIL` for a P0), `N/A` (with the reason the surface does not exist).

## 1. Profile

| Component | Purpose | Level | Why this level |
|---|---|---|---|
| `cubby` CLI and per-user agent | move the files of one folder (default `~/Downloads`) into category subfolders, reversibly | L2 | real use on a person's own files; every move is recoverable (undo journal), no money, no third-party data, no network |

| Question | Answer |
|---|---|
| Environments and where each runs | the user's own Mac or Linux session; CI on GitHub-hosted runners |
| Real data classes held | the user's downloaded documents (may be personal or financial), read in place, never copied off the machine; file paths in the journal, ledger and log (owner-only files) |
| Authentication and roles; tenancy | none: one local user, OS permissions |
| Internet exposure and inbound interfaces | none; the CLI, the config file and the files arriving in the folder |
| Outbound integrations and processors | none (the test suite runs with sockets disabled) |
| Side effects with external consequences | moving, renaming and (opt-in) deleting byte-identical duplicates of the user's files; desktop notifications |
| Persistent stores | state folder: `journal.jsonl`, `runs.jsonl`, `heartbeat.json`, `paused.json`, `cubby.lock`, log |
| Deployment model | `pipx`/venv install from a release tag; launchd (macOS) or systemd `--user` (Linux) agent |
| Owners | product, engineering and security: the maintainer (Olivier Dehareng); security reports via GitHub private advisories (`SECURITY.md`) |
| Check commands | `make check` (ruff format, ruff, mypy strict, import-linter, pytest with coverage), `make audit`, `make mutation` |

## 2. Engineering baseline (CODING-RULES section 1, L2)

| Control | Status | Evidence | Next review |
|---|---|---|---|
| Formatter, linter, strict types enforced in CI | PASS | `ci.yml` job "Lint, types and layering": ruff format --check, ruff, mypy strict, import-linter, shellcheck; required check | each release |
| Protected branch enforced by the platform, required checks | PASS | branch protection on `main`: PR required, `enforce_admins: true`, linear history, conversation resolution, the 8 CI checks required (`gh api .../branches/main/protection`, 2026-09-28) | each release |
| Untrusted CI code isolated from production hosts and credentials | PASS | no production host or secret exists; default workflow token `read`; only the tag-triggered publish job has `contents: write` and runs no project code | each release |
| Independent review of every merged change | PASS | the reviewer's own record and the author's resolution on #1 to #5, #7 to #12 and #19 (PR comments); on #13 to #18 only the resolutions were posted before merge, and the reviewers' records, written before merge, were posted afterwards (found by audit 3); #6 (the 0.2.0 version bump) merged without one and was reviewed after the merge (comment on #6). Enforced since #20 by the required `review record` status (`.github/workflows/review-record.yml`): a pull request cannot merge until a comment starting with "## Independent review record" is on it. It checks that the record exists, not who wrote it: a single maintainer has no second account to approve | each PR |
| Coverage instrumented, changed-code threshold | PASS | `pytest --cov --cov-fail-under=90` on 6 OS/Python combinations; `diff-cover --fail-under=90` on PRs; 97% total at 0.3.0 measured locally, 96.5% to 96.8% in the six CI jobs (0.2.0 was 96.4%) | each release |
| Integration tests of critical contracts and failure paths | PASS | service boundary with a fake manager (`test_service_boundary.py`), parser child process (`test_review_pr2.py`), journal and undo failure paths (`test_undo_everything.py`, `test_file_safety_edges.py`) | each release |
| Critical-journey end-to-end tests in CI | PASS | `test_agent_journey.py` (agent as a real process: sort, history, SIGTERM, undo), `test_pause_and_alerts.py::test_the_real_agent_stops_at_a_pause_and_sorts_after_resume` | each release |
| Flaky tests measured against a budget | PASS | `make flaky-rate` (`scripts/flaky_rate.py`, tested in `test_flaky_rate.py`): commits of the last 90 days whose CI runs or rerun attempts both failed and passed; it exits 2 when it measured nothing. 0 of 84 CI commits on 2026-09-28, budget 2%. The four failed commits were real defects fixed by later commits: a manual judgement from their logs, which the script does not make | each release |
| Property or model-based tests where required | PASS | `test_properties.py` (Hypothesis): durations, names, destinations, journal round trip, dates, config, model-based sort then undo | each release |
| Mutation testing, targeted | PASS | `docs/audits/2026-09-28-mutation.md`: journal, filesystem, undo, naming at 97.7% (round 10 at 00ef152, after PRs 13, 14, 15, 17 and 18; PR 16 merged later and changed none of those modules), survivors reviewed | when those modules change |
| Structured logs, error tracking | PASS | JSON-lines log with levels, the cubby version on every line and the run id on every line of a pass (the id of the ledger and the journal, `test_run_correlation.py`), rotated at 1 MB; per-run ledger with failures and version, grouped by kind of error over 24 hours with the versions that hit them; `cubby status`, `cubby log --run ID` | each release |
| Metrics for service health | PASS | heartbeat per pass with the pass's duration (latency), moves and failures (errors) and files waiting to settle (saturation); runs, moves and failures over 24 hours (rate); read by `cubby status` (exit 1 when unhealthy), `test_status_metrics.py` | each release |
| Alert delivered to a named human; heartbeat checked | NOT_VERIFIED | desktop notification on unsortable file, failed pass, missing folder (`adapters/notify.py`, tested with fakes); delivery on the maintainer's Mac not yet observed: run `cubby doctor --notify` | at install on each machine |
| Reproducible build: frozen lockfiles, one toolchain | PASS | `uv.lock` honored with `--locked` everywhere; build backend pinned by hash (`build-constraints.txt`); `scripts/rebuild.sh` builds twice (other umask, time zone, locale, file dates) and fails on any difference, run by CI on every PR and by `release.yml` against the published artifacts; v0.2.0 rebuilt byte for byte from its tag (`docs/audits/2026-09-28-reproducible-build.md`) | each release |
| Versioned releases: tag and changelog | PASS | `CHANGELOG.md`, tags, `release.yml` (tag on main, version match, SHA256SUMS) | each release |
| Rollback rehearsed | PASS | 0.3 to 0.2 and back (`docs/audits/2026-09-28-rollback-0.3.md`) and 0.2 to 0.1.0 and back in a throwaway HOME, 2026-09-28, the latter repeated by two independent operator drills (`docs/audits/2026-09-28-runbook-drill*.md`); results in `docs/RUNBOOK.md` section 4 | each release |
| Backups 3-2-1; restore drilled | N/A | cubby stores no data of its own worth backing up; the user's files stay on their disk and in their own backups; the journal is recovery metadata, bounded and rebuilt by use | - |
| Load test of capacity-sensitive paths | PASS | `docs/PERFORMANCE.md`: 1 000 to 200 000 files (10x the previous ceiling), 3 to 5 repetitions in fresh processes, median and slowest, CPU and memory, journal read at 200 000 moves; limits and next steps listed; found and fixed the idle-pass compaction cost, 2026-09-28 | when the scan, move or journal path changes |
| Feature disable capability for high-impact features | PASS | `cubby pause` (tested against a real agent process), `dedupe` and `vendor_rename` off by config, `cubby uninstall` | each release |
| Incident response: owner, runbook, rehearsal | PASS | owner above, reachable through a private advisory (`SECURITY.md`); `docs/RUNBOOK.md` (stop, keep evidence, undo, roll back, remove, damaged state, report) executed end to end twice by operator agents in a throwaway home, 2026-09-28, every defect fixed (`docs/audits/2026-09-28-runbook-drill*.md`) | each release |
| Second person able to deploy, roll back and restore | N/A | recommended at L2; single-maintainer project, runbook written so anyone can follow it | - |
| Human pentest | N/A | not required at L2 without money or third-party data | - |

## 3. Security controls (SEC-GOV-01 evidence record)

Surfaces present: local files as untrusted input, a config file, a service unit, subprocesses,
the repository and its CI. Absent: accounts, sessions, network, API, browser, database,
containers, infrastructure, secrets.

| Control | Priority | Status | Evidence | Next review |
|---|---|---|---|---|
| SEC-GOV-01 | P0 | PASS | this register, 2026-09-28 | each release |
| SEC-GOV-02 | P0 | PASS | tests use temp folders and a per-test `CUBBY_STATE_DIR` (`conftest.py`), no network, fake service managers; mutation and property runs local only | each release |
| SEC-GATE-01 | P0 | PASS | `pip-audit --strict` over the exported lock including the `extract` extras, bandit, gitleaks: CI job "Dependency and code scanning" green on the release 0.3.0 pull request (#19) | each release |
| SEC-01-001 to 007 | P0 | PASS | `docs/THREAT-MODEL.md` | release adding an entry point, parser or side effect |
| SEC-04, 05, 06 (authn, authz, sessions) | P0 | N/A | no accounts, roles or sessions; runs as the local user with OS permissions | - |
| SEC-05-006 least privilege | P0 | PASS | user agent (launchd `LaunchAgents`, systemd `--user`), no root, no sudo in `install.sh` | each release |
| SEC-07-001, 002, 003 | P0 | PASS | settings validated centrally (`Settings.__post_init__`, `_check_patterns`, `safe_component`); `test_settings_validation.py`, `test_config.py` | each release |
| SEC-07-004 unknown fields rejected | P0 | PASS | unknown keys and non-boolean switches are config errors (`test_config_strict.py`) | each release |
| SEC-07-005 parsers fuzzed | P0 | PASS | Hypothesis over config tables, journal lines (with noise and surrogates), dates, durations; seeded engine fuzz (`test_fuzz.py`) | each release |
| SEC-08-003 no command built from user data | P0 | PASS | argument lists only; systemd quoting tested (`test_service_boundary.py`); notification text passed as an argument (`test_macos_passes_the_message_as_an_argument_not_as_script`) | each release |
| SEC-08-006 no eval | P0 | PASS | bandit in CI; a planted `.py` is never imported by a parser child (`test_review_pr2.py`) | each release |
| SEC-08-001, 002, 004, 005 | P0 | N/A | no SQL, NoSQL, HTML or templates | - |
| SEC-09 (API) | P0 | N/A | no API, no outbound URL | - |
| SEC-10-001 size limited | P0 | PASS | files past `MAX_SOURCE_BYTES` are not read (`test_extraction_bounds.py`) | each release |
| SEC-10-002 types, extension not proof | P0 | PASS | the extension picks a parser, which runs in a child process with a memory ceiling and timeout; a mislabeled file only fails to parse | each release |
| SEC-10-003, 006 names and paths | P0 | PASS | renamed names are single components (`safe_component`), every destination re-checked inside the root (`resolve_inside`), `test_containment.py` | each release |
| SEC-10-005 no automatic execution | P0 | PASS | nothing is executed or imported from the folder (`test_review_pr2.py`) | each release |
| SEC-10-004, 007, 008 | P0 | N/A | files stay in the user's own folder; no download service; antimalware is the OS's | - |
| SEC-11-002, 007 | P0 | PASS | `hashlib.sha256` for duplicates, `secrets.token_hex` for run ids; no custom cryptography | each release |
| SEC-11-001, 003 to 006 | P0 | N/A | no transport, passwords or keys | - |
| SEC-12-001, 002, 008, 009, 011 | P0 | PASS | gitleaks 8.30.1 (checksum-verified) over the full history in CI, no allowlist file; GitHub secret scanning and push protection enabled | each release |
| SEC-12-003 to 007, 010 | P0 | N/A | cubby holds no secret | - |
| SEC-13-001 lockfile honored | P0 | PASS | `uv sync --locked`, `uv run --locked` in CI and Makefile | each release |
| SEC-13-002 scanning every ecosystem | P0 | PASS | Python (lock export incl. extras), GitHub Actions via Dependabot | each release |
| SEC-13-003 unused dependencies | P0 | PASS | no runtime dependency; `extract` extras each imported by `adapters/parsers.py`; dev group tools each run by CI or `make` | each release |
| SEC-13-004, 005 | P0 | PASS | PyPI only; Dependabot for uv and actions | each release |
| SEC-13-006 no abandoned critical dependency | P0 | PASS | `pypdf` 6.19.0 (2026-09), `python-docx` 1.2.0 (2025-06); `openpyxl` 3.1.5 (2024-06) is the oldest, optional and sandboxed: see section 4 | each release |
| SEC-13-007 new dependency reviewed | P0 | PASS | `hatchling` pin reviewed in PR #4; `fastjsonschema` 2.22.2 (dev group only, tests): BSD-3-Clause, no runtime dependencies (unlike `jsonschema`, which brings a Rust extension through `rpds-py`), released 2026-08-15, reviewed in PR #8 | each PR |
| SEC-13-008, 009 | P0 | PASS | `permissions: contents: read` default; every action pinned by commit SHA | each release |
| SEC-13-010 | P0 | N/A | no container | - |
| SEC-14-001, 002 | P0 | PASS | branch protection (section 2); review records on every PR, #6 reviewed after its merge (section 2) | each release |
| SEC-14-003 MFA on every account with access | P0 | NOT_VERIFIED | one account; the CI token cannot read the 2FA flag. Confirm in GitHub settings, or run `gh auth refresh -s user` and `gh api user -q .two_factor_authentication` | each release |
| SEC-14-004 minimal repository permissions | P0 | PASS | one collaborator (the owner), 2026-09-28 | each release |
| SEC-14-005, 006, 007, 008, 010 | P0 | PASS | scanning enabled; PR and release history; read-only token; the only write job publishes a release from a tag on `main` | each release |
| SEC-14-009 | P0 | N/A | no staging or production environment | - |
| SEC-15, 16, 17-001 to 009 | P0 | N/A | no infrastructure, browser surface or database | - |
| SEC-17-010, 011, 013, 014 personal data | P0 | PASS | only paths are recorded, classified in the threat model; files 0600 in a 0700 folder; journal compacted past 5 MB to the 200 most recent runs plus whatever can still be undone, log rotated at 1 MB | each release |
| SEC-17-018 third-party processors | P0 | N/A | no data leaves the machine | - |
| SEC-18-002, 004, 005 bounded operations | P0 | PASS | size ceiling, text window, parser child timeout (15 s) and memory limit (1 GB on Linux; macOS does not enforce `RLIMIT_AS`, the timeout still bounds it), service calls time out (30 s) | each release |
| SEC-18 application-level DoS | P0 | PASS | a crafted document cannot stall the agent (`test_review_pr2.py`, `test_extraction_bounds.py`); a failing file never stops a pass | each release |
| SEC-18-001, 003 | P0 | N/A | no login, no email or SMS | - |
| SEC-18-006 | P1 | N/A | no security events (no authentication or authorization) | - |
| SEC-18-007, 008 alerts | P1 | NOT_VERIFIED | alerts exist and are tested with fakes; delivery on a real desktop not yet observed (`cubby doctor --notify`) | at install |
| SEC-19-001 fail closed | P0 | PASS | a refused destination moves nothing; a damaged pause file counts as a pause (`test_a_damaged_pause_file_still_pauses`) | each release |
| SEC-19-002, 003 | P0 | PASS | errors are one-line messages with context; details in the log and the ledger | each release |
| SEC-19-004 no partially valid state | P0 | PASS | each move journaled as it happens, the run continues past a failing file, a stop ends a pass between two files (`test_undo_everything.py`, `test_review_pr4.py`) | each release |
| SEC-19-005 timeouts | P0 | PASS | service manager 30 s, parsers 15 s, notifications 5 s, lock 30 to 60 s | each release |
| SEC-19-006 idempotency | P0 | PASS | a second run finds nothing to move; undo settles each entry once and retries only pending ones | each release |
| SEC-20-001 to 004 backups | P0 | N/A | no data of cubby's own (see section 2) | - |
| SEC-20-005, 006, 007, 014 | P0 | PASS | owner and contact above; `docs/RUNBOOK.md`; logs and ledger readable by `cubby status` and on disk | each release |
| SEC-20-008 to 010 | P0 | N/A | no accounts, tokens or secrets | - |
| SEC-20-011 isolate a system | P0 | PASS | `cubby pause`, `cubby uninstall` (runbook section 1) | each release |
| SEC-20-012 disable a high-impact feature | P0 | PASS | `cubby pause` against a real agent process (`test_the_real_agent_stops_at_a_pause_and_sorts_after_resume`); `dedupe`, `vendor_rename` switches | each release |
| SEC-20-013 roll back | P0 | PASS | 0.3 to 0.2 and 0.2 to 0.1 rehearsals recorded in `docs/RUNBOOK.md` section 4 | each release |
| SEC-20-015 evidence preservation | P0 | PASS | runbook section 2 | each release |

## 4. Accepted constraints and known debt

| Constraint | Consequence | Accepted by | Ends when |
|---|---|---|---|
| `openpyxl` last released 2024-06 | a parser bug in an unmaintained library could go unfixed | maintainer, 2026-09-28: optional extra, runs in a child with memory and time limits, only reads the first 20 rows | a security advisory without a fix, or a maintained replacement |
| macOS agent behavior (launchd, notifications) verified by tests with fakes and the CI macOS runners, not on a real login session | an OS-specific surprise would show at install | maintainer | `cubby status` and `cubby doctor --notify` observed on the maintainer's Mac |
| Heartbeat read on the same machine as the agent | a dead machine alerts nobody; nothing to alert about either, since cubby only acts on that machine | maintainer | - |

## 5. Release log

| Date | Release tag | Gate status | Open blockers |
|---|---|---|---|
| 2026-09-28 | v0.3.0 | BLOCKED | SEC-14-003 NOT_VERIFIED (maintainer to confirm 2FA). Released for the maintainer's own use; every other applicable P0 PASS or N/A |
| 2026-09-28 | v0.2.0 | BLOCKED | SEC-14-003 NOT_VERIFIED (maintainer to confirm 2FA). Released for the maintainer's own use; every other applicable P0 PASS or N/A |
