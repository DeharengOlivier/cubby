# cubby runbook drill

- Date: 2026-09-28
- Runbook under test: `docs/RUNBOOK.md` (100 lines, sections 1 to 6)
- cubby version: `cubby --version` -> `cubby 0.2.0`
- Repo commit: `d7475a1` when the drill started. The repo moved to `1bea0e8` then `59981e0` during the
  drill (someone else committing: tests, a new `cubby log` command). `git diff d7475a1 HEAD -- docs/RUNBOOK.md`
  is empty, so the runbook tested is the same at all three commits.
- Rollback target: `v0.1.0`, taken from the local tag with `git archive v0.1.0 src` into the drill folder and run
  with `PYTHONPATH=drill/v01/src python -m cubby` (no network, nothing installed).
- Environment: Linux 6.8.0-139-generic x86_64, Python 3.11.16, cubby from `cubby-r2/.venv/bin`.
  Every command ran with `HOME=drill/home`, `XDG_CONFIG_HOME=drill/xdg-config`, `XDG_STATE_HOME=drill/xdg-state`
  (so the state folder is `drill/xdg-state/cubby`), and `PATH=drill/bin:.venv/bin:/usr/bin:/bin`.
  The rollback rehearsal also used `HOME=drill/rb-home` without `XDG_STATE_HOME` (the default layout) and
  `HOME=drill/rb2-home` with `XDG_STATE_HOME=drill/rb2-xdg`.
- Service manager: faked. `drill/bin/systemctl` logs its arguments to `drill/service-calls.log`, answers
  `is-active` from a state file, and on `disable --now` sends SIGTERM to the simulated agent. Two switches
  simulate failures: `FAKE_SYSTEMCTL_REFUSE=1` (disable exits 1, "Access denied") and `FAKE_SYSTEMCTL_SURVIVE=1`
  (disable exits 0 but the agent keeps running and `is-active` still says `active`). The agent itself was simulated
  by running the unit's own `ExecStart` line in the background:
  `CUBBY_STATE_DIR=drill/xdg-state/cubby cubby watch --wait-for-source --delay 0 --interval 1s`.
  pipx is not installed on this machine. No background process is left running (checked with `ps`).
- Real home `/home/agent`, real systemd and the repository were not touched (`git status` shows only changes
  made by the other committer).

## Procedures found in the runbook

1. Stop the agent now (pause / status / resume, or uninstall / status, with a manual fallback).
2. Keep the evidence (copy the state folder, and the macOS log).
3. Put the files back (history, undo, undo --run, pending entries, dedupe, `unknown` runs).
4. Roll back to a previous version (pipx or venv pip from GitHub, reinstall the agent, compatibility notes).
5. Remove cubby completely (uninstall, pipx uninstall or uninstall.sh, rm -rf).
6. Report it (status --json, history --json, SECURITY.md).

Not covered by the runbook at all: checking health outside an incident, recovering a damaged state file
(journal, ledger, heartbeat, `paused.json`), and finding the options the agent was installed with.

## Step record

| # | Procedure / step | Verdict | Evidence |
|---|---|---|---|
| 1.1 | `cubby pause` | WORKS | exit 0, "The agent is paused since 2026-09-28T02:56:28. It stops before the next file it would move." Two files dropped afterwards (`Invoice-late.pdf`, `pic2.jpg`) stayed at the root for 5 s with the agent passing every 1 s. |
| 1.2 | `cubby status` shows "paused ... no file is moved" | WORKS | exit 0, line `paused  paused since 2026-09-28T02:56:28: no file is moved`. |
| 1.3 | "The agent stays running (its heartbeat shows it alive) but skips every pass until `cubby resume`" | WORKS | During the pause `kill -0` on the agent succeeded and status showed `last pass 1s ago`; log: "passes skipped until 'cubby resume'". `cubby resume` (exit 0) and the two files were sorted within 4 s. |
| 1.4 | `cubby uninstall` (normal case) | WORKS | exit 0, "Removed the systemd agent."; calls: `systemctl --user disable --now cubby.service`, `daemon-reload`; agent logged "cubby stopped" and exited. |
| 1.5 | `cubby status` "not installed confirms it is stopped" | WRONG | Status only checks that the unit file exists. With `FAKE_SYSTEMCTL_SURVIVE=1`, after uninstall the agent process was alive and `is-active` said `active`, yet `cubby status` printed `agent  not installed` and exited 0. The surviving agent then moved `survivor.png` into `Images/`. |
| 1.6a | "`cubby uninstall` exits non-zero if the service manager refuses" | WORKS | `FAKE_SYSTEMCTL_REFUSE=1`: exit 1, "cubby: systemctl --user disable --now cubby.service failed: Failed to disable unit: Access denied"; unit file kept. |
| 1.6b | "... or the agent survives" | WRONG | `FAKE_SYSTEMCTL_SURVIVE=1`: exit 0, "Removed the systemd agent." with the agent still running. `SystemdService.uninstall` never checks `is_running` after `disable --now` (only the launchd backend does). |
| 1.7 | macOS fallback `launchctl unload -w ~/Library/LaunchAgents/com.cubby.agent.plist` | NOT EXECUTABLE HERE | Needs launchd. Path matches `LaunchdService.unit_path` (`~/Library/LaunchAgents/` + `com.cubby.agent.plist`). But `LaunchdService.uninstall` deletes the plist before it checks `is_running` and raises, so in the one case where the fallback is needed the plist named in the command no longer exists. See defect D4. |
| 1.8 | Linux fallback `systemctl --user disable --now cubby.service` | NOT EXECUTABLE HERE | Unit name matches `SystemdService._unit_name` (`cubby.service`). With the fake it exited 0 and stopped the agent. In the refused case the unit file is still there, so the command is meaningful. In the survive case `cubby uninstall` has already deleted the unit file and run `daemon-reload`; how real systemd answers `disable` for a unit whose file is gone could not be checked here. `stop` is the verb that needs only the loaded unit. |
| 1.9 | SIGTERM, 60 s grace (`TimeoutStopSec`, `ExitTimeOut`) | WORKS | Generated unit contains `TimeoutStopSec=60`; `STOP_TIMEOUT = 60` feeds the plist `ExitTimeOut`; on SIGTERM the agent logged "cubby stopped" and exited 0. (Not true after a rollback: the 0.1 unit has no `TimeoutStopSec`.) |
| 2.1 | `cp -a "${CUBBY_STATE_DIR:-...}/cubby" ~/cubby-evidence-$(date ...)` | WORKS | exit 0; copy holds `journal.jsonl`, `runs.jsonl`, `heartbeat.json`, `cubby.log`, `cubby.lock`; folder mode `drwx------`. |
| 2.2 | `cp -a ~/Library/Logs/cubby.log* ~/cubby-evidence-*/ 2>/dev/null` | WRONG | Simulated macOS layout with an older evidence folder from a previous incident: exit 0, and the older folder was copied into the new one (`cubby-evidence-20260928-025721/cubby-evidence-20260928-010000/journal.jsonl`), mixing incidents. On Linux it exits 1 (the glob stays literal), which aborts a `set -e` script. |
| 2.3 | "keep the copy private" | WORKS | `cp -a` keeps `0700`/`0600`. The folder is never removed later (see D11). |
| 3.1 | `cubby history` | WORKS | exit 0, one line per run: time, run id, mode, `moved N`, then `undone` / `partly undone`. It shows counts, not which files each run moved. |
| 3.2 | `cubby undo` | WORKS | exit 0, "restored survivor.png / Restored 1 file(s)." (the most recent run). |
| 3.3 | `cubby undo --run ID` | WORKS | `cubby undo --run 20260928T025638-5696b6dc`: exit 0, 2 files restored. |
| 3.4 | "An entry that cannot be restored (the file was moved again, or the original name is taken) stays pending and is reported" | WRONG | Moved again: `mv Music/song.mp3 ../moved-away.mp3`, then `cubby undo --run 20260928T025747-1e570ed8`: exit 0, "skip (no longer at ...)", history says `undone`; after putting the file back, the retry printed "nothing to undo", exit 0. The entry was settled as `gone`, not left pending. Name taken: with a new `notes.txt` at the root, undo exit 0, "restored notes (1).txt". Neither example stays pending. |
| 3.5 | "fix the cause and run `cubby undo --run ID` again" (a real failure) | WORKS | `chmod 555 Music`, undo: exit 1, "1 file(s) could not be restored and stay pending", history `partly undone`; after `chmod 755`, retry exit 0, history `undone`. Side effect: the retry restored "track2 (1).mp3", because the failed attempt had left a hard link `track2.mp3` at the root (same inode 8727225). The operator ends up with two names for one file (code defect, see D12). |
| 3.6 | "A deduplicated file is restored as a copy of the one that was kept" | WORKS | With `dedupe = true` a second `dup.png` was journaled as `op: dedupe`; undo exit 0, `dup.png` back at the root, `Images/dup.png` kept, different inodes. |
| 3.7 | "`unknown` ... dropped by compaction after 200 newer runs and 5 MB: its files have to be moved back by hand" | WRONG | Synthetic 14.1 MB journal, 250 runs, 125 with pending moves. After one `cubby run`: 12.5 MB, 225 runs kept, every pending run kept, only 26 fully-undone runs dropped. `Journal.compact` keeps "a run with anything left to undo ... whatever its age". A run dropped by compaction had nothing left to undo. |
| 4.1 | `pipx install --force "git+https://github.com/DeharengOlivier/cubby.git@v0.1.0"` | NOT EXECUTABLE HERE | No network, and pipx is not installed. Syntax is valid for pipx; tag `v0.1.0` exists in the repo; its `pyproject.toml` names `cubby-sort` 0.1.0 with no dependencies. |
| 4.2 | `~/.local/share/cubby/venv/bin/pip install --force-reinstall "git+...@v0.1.0"` | NOT EXECUTABLE HERE | No network. The venv path matches `install.sh` (`VENV_DIR="${PREFIX}/share/cubby/venv"`, `PREFIX=$HOME/.local`). |
| 4.3 | `cubby --version` | WORKS | Local 0.1 export prints `cubby 0.1.0`. |
| 4.4 | "Then reinstall the agent if it was running: `cubby install` with the same options" | UNCLEAR | The runbook does not say where the options are (the `ExecStart=` line of `~/.config/systemd/user/cubby.service`, or `ProgramArguments` in the plist). 0.1 rejects two of the options 0.2 accepts: `cubby install --delay 0 --month-style letters` -> exit 2 "unrecognized arguments: --month-style letters". Until the reinstall, the 0.2 unit still runs `cubby watch --wait-for-source ...`, which 0.1 rejects (exit 2, "unrecognized arguments: --wait-for-source"). So the agent restart-loops (`Restart=on-failure`, `RestartSec=5`; launchd `KeepAlive`). |
| 4.5 | "Cubby 0.2 reads journals written by 0.1" | WORKS | A run made by 0.1 (`{"ts": ..., "moves": [...]}` line) was undone by 0.2 `cubby undo`: "restored v1file.png". The 0.1 run does not appear in 0.2 `cubby history` (0.1 writes no ledger), so it can only be reached with plain `cubby undo`. |
| 4.6 | "0.1 ... `cubby undo` fails with `KeyError: 'moves'`, changes nothing, runs made by 0.1 after the rollback undo normally" | WRONG (true only in the default layout) | Default layout (`rb-home`, no `XDG_STATE_HOME`): confirmed: traceback `KeyError: 'moves'`, exit 1, journal sha256 identical; a 0.1 run was undone by 0.1. With `XDG_STATE_HOME` or `CUBBY_STATE_DIR` set (`rb2-home`): 0.1 reads `~/.local/state/cubby/journal.jsonl` (hard-coded in 0.1) and prints "nothing to undo", exit 0. There is no KeyError, and the 0.2 runs are not in sight. Also the agent installed by 0.2 carries `CUBBY_STATE_DIR` in its unit, which 0.1 ignores. |
| 4.7 | "Rolling forward to 0.2 again undoes the 0.2 runs as before" | WORKS | After rollback and roll forward, 0.2 `cubby undo` restored the 0.2 run (`a.png`, `x.png`). |
| 4.8 | "Nothing is lost either way" | WRONG | (a) With `XDG_STATE_HOME` set, the run 0.1 made during the rollback (`y.png`) went to `~/.local/state/cubby/journal.jsonl`. After roll forward, 0.2 `cubby undo` said "nothing to undo" and `y.png` stayed in `Images/`. (b) 0.1 has no pause: with `paused.json` written by 0.2 (`cubby status`: "paused since 02:59:33"), 0.1 `cubby watch --delay 0 --interval 1s` moved `c.png` into `Images/` within 4 s. `cubby pause` and `cubby history` do not exist in 0.1 (exit 2, "invalid choice"). (c) 0.1 writes its log to `~/Library/Logs/cubby.log` even on Linux (seen in the 0.1 install message), outside the folder that section 2 copies. |
| 5.1 | `cubby uninstall` | WORKS | exit 0, "Removed the systemd agent.", agent stopped. |
| 5.2 | `pipx uninstall cubby-sort  # or: ./uninstall.sh from a checkout` | NOT EXECUTABLE HERE (pipx part) | `pipx: command not found`, exit 127. The alternative `./uninstall.sh` (run from the checkout, drill HOME) exited 0: "No cubby agent was installed." then "cubby removed. Your sorted folders and config (~/.config/cubby) are untouched." Someone who installed with pipx straight from GitHub has no checkout for the alternative. |
| 5.3 | `rm -rf "<state>" ~/.config/cubby ~/Library/Logs/cubby.log*` | WORKS | exit 0; state folder and config removed. Still on disk: `~/cubby-evidence-*` from section 2 (names of downloads), and after a rollback the 0.1-era `~/.local/state/cubby` when `XDG_STATE_HOME` or `CUBBY_STATE_DIR` is set. A `CUBBY_CONFIG` file would also be missed. |
| 5.4 | "Sorted files stay where they are" | WORKS | `home/Downloads/{Bank-Statements,Documents,Images,...}` intact after removal. |
| 6.1 | `cubby status --json`, `cubby history --json` | WORKS | Both exit 0, valid JSON (`version`, `agent`, `paused`, `last_run`, `log` / `runs[...]` with `undo`). Both contain the full source path, and `failures` would list file names: the instruction to remove file names mentions only the log lines. |
| 6.2 | "Owner: the maintainer (see `SECURITY.md` for the contact)" | UNCLEAR | `SECURITY.md` says "open a private security advisory on GitHub, or email the maintainer" and gives no address or handle. |

Counts: WORKS 20, WRONG 7, UNCLEAR 2, NOT EXECUTABLE HERE 5 (34 steps).

Other checks (not runbook steps): a damaged `journal.jsonl`, `runs.jsonl`, `heartbeat.json` and `paused.json` did not
crash `history`, `status` or `undo`. A damaged `paused.json` is shown as
"paused (unreadable paused.json; run 'cubby resume' to clear it)", and `cubby resume` clears it. The runbook says
nothing about any of this.

## Defects in the runbook, with proposed corrections

**D1 (section 1, WRONG): `cubby status` does not confirm the agent is stopped.**
Current: `cubby status             # "not installed" confirms it is stopped`
Proposed:
```sh
cubby status             # "not installed" means the unit file is gone, not that the process stopped
cubby status             # run it again a minute later: "last pass" must keep growing (the heartbeat stopped)
pgrep -fl 'cubby watch'  # must print nothing
```

**D2 (section 1, WRONG): `cubby uninstall` does not exit non-zero when the agent survives on Linux.**
Current: "`cubby uninstall` exits non-zero if the service manager refuses or the agent survives."
Proposed: "`cubby uninstall` exits non-zero if the service manager refuses. On macOS it also fails if the agent
still runs after unload; on Linux it does not check, so always confirm with the checks above (D1)." Also fix the
code: `SystemdService.uninstall` should check `is_running` after `disable --now`, as the launchd backend does.

**D3 (section 1, Linux fallback): the manual stop must work when the unit file is already gone.**
Current: `- Linux: systemctl --user disable --now cubby.service`
Proposed:
```sh
systemctl --user stop cubby.service        # works on the loaded unit even if its file was removed
systemctl --user disable cubby.service     # only if ~/.config/systemd/user/cubby.service still exists
systemctl --user is-active cubby.service   # must print "inactive" (or "unknown")
```

**D4 (section 1, macOS fallback): the plist named in the fallback has already been deleted.**
`cubby uninstall` unlinks `~/Library/LaunchAgents/com.cubby.agent.plist` before it detects a survivor, so
`launchctl unload -w <that plist>` fails on a missing file.
Proposed:
```sh
launchctl bootout gui/$(id -u)/com.cubby.agent   # by label, no plist needed
launchctl list com.cubby.agent                   # must fail with "Could not find service"
```

**D5 (section 2, WRONG): the macOS log copy uses a glob for the destination.**
Current:
```sh
cp -a "${CUBBY_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/cubby}" ~/cubby-evidence-$(date +%Y%m%d-%H%M%S)
cp -a ~/Library/Logs/cubby.log* ~/cubby-evidence-*/ 2>/dev/null   # macOS log
```
Proposed:
```sh
EVIDENCE=~/cubby-evidence-$(date +%Y%m%d-%H%M%S)
cp -a "${CUBBY_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/cubby}" "$EVIDENCE"
if [ "$(uname)" = Darwin ]; then cp -a ~/Library/Logs/cubby.log* "$EVIDENCE"/; fi   # macOS log
echo "$EVIDENCE"
```
After a rollback to 0.1, also copy `~/Library/Logs/cubby.log*` on Linux and `~/.local/state/cubby`, where 0.1 writes.

**D6 (section 3, WRONG): the two examples of a pending entry are the two cases that are not pending.**
Current: "An entry that cannot be restored (the file was moved again, or the original name is taken) stays pending
and is reported; fix the cause and run `cubby undo --run ID` again."
Proposed:
- "A file that is no longer where the run put it (moved again, deleted) is skipped for good: undo prints
  `skip (no longer at ...)`, still exits 0, and the run shows as `undone`. Look for those `skip` lines and move
  such files back by hand."
- "If the original name is taken, the file is restored next to it with a suffix, e.g. `notes (1).txt`."
- "An entry that fails with an error (permission, full disk) stays pending: undo exits 1 and the run shows
  `partly undone`. Fix the cause and run `cubby undo --run ID` again. Then check for a duplicate `name (1).ext`
  (see D12)."

**D7 (section 3, WRONG): `unknown` runs and compaction.**
Current: "A run shown as `unknown` ... (dropped by compaction after 200 newer runs and 5 MB): its files have to be
moved back by hand, using the ledger entry and the log."
Proposed: "A run shown as `unknown` is not in the journal. Compaction (once the journal passes 5 MB) only drops
runs that are older than the 200 most recent and have nothing left to undo, so such a run needs nothing. A run
that is `unknown` with files still misplaced means the journal was lost or could not be written (look for
`could not write` warnings in the log): move its files back by hand from the log lines of that run
(`jq 'select(.run == "<id>")' <state>/cubby.log`)."

**D8 (section 4, UNCLEAR): "reinstall the agent with the same options", and the gap before it.**
Proposed: record the options and stop the agent before replacing the package:
```sh
grep ^ExecStart ~/.config/systemd/user/cubby.service                               # Linux: the options in use
plutil -extract ProgramArguments json -o - ~/Library/LaunchAgents/com.cubby.agent.plist   # macOS
cubby uninstall                        # before the downgrade: 0.1 rejects the 0.2 unit's --wait-for-source
<pipx or pip command>
cubby install <options, without --month-style / --month-lang, which 0.1 does not have>
```

**D9 (section 4, WRONG): the compatibility notes hold only when no state folder is configured.**
Add: "Cubby 0.1 always uses `~/.local/state/cubby`. It ignores `XDG_STATE_HOME` and `CUBBY_STATE_DIR`, including the
`CUBBY_STATE_DIR` that 0.2 writes into the agent unit. If either is set, 0.1 sees none of the 0.2 runs (its
`cubby undo` says `nothing to undo`, not `KeyError`), and the runs 0.1 makes go to a journal 0.2 never reads: after
rolling forward, undo them with `CUBBY_STATE_DIR=~/.local/state/cubby cubby undo`, or undo them in 0.1 before
rolling forward."

**D10 (section 4, WRONG): "Nothing is lost either way" ignores the pause.**
Add, in bold next to the existing warning: "**0.1 has no pause, history or `undo --run`.** A `cubby pause` set under
0.2 is ignored by a 0.1 agent, which starts moving files again at once. Uninstall the agent (not just pause) before
rolling back, and reinstall it only when you want 0.1 to sort. 0.1 writes its log to `~/Library/Logs/cubby.log` on
every platform, and its unit has no 60 s stop grace (section 1 does not apply to it)."

**D11 (section 5, incomplete): "Remove cubby completely" leaves copies of download names.**
Proposed third line:
```sh
rm -rf "${CUBBY_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/cubby}" ~/.local/state/cubby \
       ~/.config/cubby ${CUBBY_CONFIG:+"$CUBBY_CONFIG"} ~/Library/Logs/cubby.log*
rm -rf ~/cubby-evidence-*    # only once the incident is closed: section 2's copies list your downloads
```
And make the uninstall line unambiguous: "`pipx uninstall cubby-sort` if you installed with pipx; otherwise
`rm -rf ~/.local/share/cubby/venv ~/.local/bin/cubby` (what `./uninstall.sh` does)", since a pipx-from-GitHub
install has no checkout for `./uninstall.sh`.

**D12 (code, surfaced by section 3): a failed undo leaves a hard link, and the retry creates a duplicate.**
`move_no_clobber` links the file to its original name and then unlinks the filed copy. When the unlink fails
(read-only category folder), the link stays at the original name although its docstring says "source is still in
place". The next `cubby undo --run ID` then restores to `track2 (1).mp3` beside `track2.mp3`, both the same inode.
Fix in code: remove the new link when `source.unlink()` fails. Until then, add to section 3: "after a retried undo,
delete any `name (1).ext` that has the same inode as `name.ext` (`ls -li`)."

**D13 (header, UNCLEAR): no reachable owner.**
Current: "Owner: the maintainer (see `SECURITY.md` for the contact)". SECURITY.md gives no address. Proposed: put
the maintainer's GitHub handle and the security advisory URL
(`https://github.com/DeharengOlivier/cubby/security/advisories/new`) in both files.

**D14 (gaps, no procedure):** add a short "Damaged state file" section: a damaged line in `journal.jsonl` or
`runs.jsonl` is skipped (verified: `history`, `status` and `undo` still exit 0), an unreadable `paused.json`
counts as a pause and `cubby resume` clears it, and a damaged `heartbeat.json` shows `last pass never`. Copy the
folder first (section 2) and do not edit the journal by hand. Also mention in section 3 that `cubby history` shows
counts only: the files of a run are in the log (`jq 'select(.run == "<id>")'`), and in section 6 that
the JSON outputs contain full paths and failure file names.

## Resolution (same day)

The drill was run by an operator agent working from the runbook alone, independent of the
author of the changes. Its findings were handled as follows:

- D1, D2 (code): `cubby uninstall` now checks on both backends that the agent stopped, and
  keeps the unit when it did not; `cubby status` names a live cubby process behind a fresh
  heartbeat (`live_pid`). Reproducers in `tests/test_review_runbook_drill.py`.
- D12 (code): a move whose source cannot be removed now removes the new link, so a failed
  undo changes nothing and the retry restores one file under one name. Reproducers in the
  same file.
- D3 to D11, D13, D14 (runbook): `docs/RUNBOOK.md` rewritten with the proposed corrections,
  a "damaged state file" section, `cubby log --run` for the files of a run, and the security
  contact (`SECURITY.md` now names the private advisory URL).

The corrected runbook was then executed again: see "Second drill" below.
