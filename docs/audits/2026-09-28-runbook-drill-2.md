# Runbook drill 2: cubby operations runbook executed literally

- Date: 2026-09-28
- Operator: first-time operator, no prior knowledge of cubby. Did not read `docs/audits/`.
- Runbook under test: `docs/RUNBOOK.md` at the commit below.
- `cubby --version`: `cubby 0.2.0`
- `git -C <repo> rev-parse --short HEAD`: `0af0171` (branch `chore/audit-2-remediation`, `git describe`: `v0.2.0-9-g0af0171`). Note: HEAD moved from `b630063` to `0af0171` (a CHANGELOG-only commit) while the drill was starting; `docs/RUNBOOK.md` and `src/` are identical in both.

## Environment

- Linux 6.8, Python 3.11.16 from the repository venv (`.venv/bin/cubby` first on PATH after the fake bin).
- Throwaway directory `scratchpad/drill2/`. Every command ran with `HOME=drill2/home`, `XDG_CONFIG_HOME=drill2/home/.config`, `XDG_STATE_HOME=drill2/home/.local/state`, `CUBBY_STATE_DIR` and `CUBBY_CONFIG` unset, `NO_COLOR=1`. Two extra homes (`home2`, `home3`) for the state-dir split and the pre-fix reproduction. Below, `~` means the drill home.
- Fake service manager `drill2/bin/systemctl` (logs every call to `drill2/service-calls.log`, 69 calls recorded):
  - `restart`/`start`/`enable --now` run the unit's `Environment=` and `ExecStart=` line in the background (the real agent, `cubby watch --wait-for-source --delay 0 --interval 1s`) and record its pid;
  - `is-active` answers `active`/`inactive` from a state file and the pid;
  - `disable --now` and `stop` send SIGTERM and wait;
  - switch `SURVIVE`: `disable --now`/`stop` exit 0, the agent keeps running and stays `active`;
  - switch `UNTRACKED`: `disable --now`/`stop` exit 0, `is-active` says `inactive`, the process keeps running (manager lost track of it);
  - switch `REFUSE`: `disable`/`stop` exit 1 with `Access denied`.
- Fake `notify-send` (logs only), so no notification reached a real desktop.
- v0.1.0 from `git archive v0.1.0 src`, run through a wrapper `drill2/bin01/cubby` (`PYTHONPATH=drill2/v01/src python3 -m cubby`) put first on PATH to stand in for `pipx install ...@v0.1.0`. v0.2.0 (tag) extracted the same way to `drill2/v02` to reproduce the pre-fix behaviour.
- No network, nothing installed. The real home was checked afterwards: no `~/.config/systemd/user/cubby.service`, no `~/.local/state/cubby`, no `~/Library/Logs`. All background agents stopped (`pgrep -fl 'cubby watch'` exit 1 at the end).

Harness caveat: `pgrep -f 'cubby watch'` also matched the tool's own wrapper shell whenever the command line I sent contained the string `cubby watch`; the checks were therefore run from a small script (`drill2/pg.sh`). A human typing `pgrep` at a prompt would not see this.

## Results

| # | Procedure | Step (as written) | Verdict | Evidence |
|---|---|---|---|---|
| 1 | 1 Stop | `cubby pause` | WORKS | exit 0, "The agent is paused since 2026-09-28T03:12:18...". Two files dropped afterwards stayed in `Downloads` for 3 s of 1 s passes. |
| 2 | 1 Stop | `cubby status` says `"paused ... no file is moved"` | WORKS | `paused  paused since 2026-09-28T03:12:18: no file is moved`, exit 0 |
| 3 | 1 Stop | "The agent stays running (heartbeat alive) but skips every pass until `cubby resume`" | WORKS | `last pass 1s ago` while paused; log `passes skipped until 'cubby resume'`; after `cubby resume` the 2 files were sorted within 3 s |
| 4 | 1 Stop | `cubby uninstall` (manager cooperates) "stops the agent, checks it stopped, removes the unit" | WORKS | exit 0 `Removed the systemd agent.`; agent log `cubby stopped`; unit file gone |
| 5 | 1 Stop | `cubby status` must say "not installed" with no pid | WORKS | `agent  not installed` |
| 6 | 1 Stop | `pgrep -fl 'cubby watch'` must print nothing | UNCLEAR | Prints nothing when stopped (exit 1). When the agent runs, Linux procps prints `625584 python3`: `-l` shows the process name, not the command line, so the operator cannot see that the match is cubby. `pgrep -fa` shows the full command. |
| 7 | 1 Stop | `cubby uninstall` exits non-zero if the manager refuses, keeps the unit | WORKS | REFUSE: `cubby: systemctl --user disable --now cubby.service failed: Failed to disable cubby.service: Access denied`, exit 1, `cubby.service` still present |
| 8 | 1 Stop | ... or if the agent is still running afterwards, keeps the unit | WORKS | SURVIVE: `cubby: cubby.service is still running after 'systemctl --user disable --now'`, exit 1, unit kept |
| 9 | 1 Stop | same claim, agent alive but the manager reports `inactive` | WRONG | UNTRACKED: `Removed the systemd agent.`, exit 0, unit deleted, pid 628353 still sorting. The claim holds only for what the manager reports. `cubby status` (row 11) is what catches it. |
| 10 | 1 Stop | "`cubby status` names any cubby process whose heartbeat is still fresh" (unit kept, agent survived) | WRONG | SURVIVE, after the failed uninstall: `agent  running (systemd)`, exit 0, no pid anywhere in the text output (the pid is only in `--json` `agent.live_pid`). The "kill -TERM <pid> with the pid cubby status names" step has no pid to use. |
| 11 | 1 Stop | same claim, unit removed, process alive | WORKS | UNTRACKED: `agent  not installed, but cubby (pid 628353) is still sorting`; `--json` `live_pid: 628353` |
| 12 | 1 Stop | macOS `launchctl bootout gui/$(id -u)/com.cubby.agent`, `launchctl list com.cubby.agent` | NOT EXECUTABLE HERE | Linux. Checked against `src/cubby/adapters/service/launchd.py`: label `com.cubby.agent` (`DEFAULT_LABEL`), plist `~/Library/LaunchAgents/com.cubby.agent.plist`, `is_running` uses `launchctl list <label>`. Consistent. |
| 13 | 1 Stop | `systemctl --user stop cubby.service` | WORKS | Cooperative: exit 0, agent exited (`cubby stopped`). REFUSE: exit 1 `Failed to stop cubby.service: Access denied`. |
| 14 | 1 Stop | what to do when `stop` exits 0 but the agent is still active / still sorting | UNCLEAR | SURVIVE: `stop` exit 0, `is-active` still `active`. UNTRACKED: `is-active` prints `inactive` (the check "passes") while pid 628353 keeps sorting. The runbook offers `kill -TERM <pid>` only for "a foreground cubby watch (no agent)"; I used it anyway with the pid from `cubby status --json` and it worked. |
| 15 | 1 Stop | `systemctl --user disable cubby.service` if the unit file exists | WORKS | exit 0 |
| 16 | 1 Stop | `systemctl --user is-active cubby.service` must print `inactive` | WORKS | `inactive`, exit 3 (but see row 14: not sufficient on its own) |
| 17 | 1 Stop | foreground `cubby watch`: `kill -TERM <pid>` with the pid status names | WORKS | status `not installed, but cubby (pid 630468) is still sorting`; `kill -TERM 630468`; next status `not installed` |
| 18 | 1 Stop | SIGTERM finishes the file, exits; units give 60 s (`TimeoutStopSec`, `ExitTimeOut`) | WORKS | log `cubby stopped` after each SIGTERM; unit has `TimeoutStopSec=60`; `launchd.py` writes `ExitTimeOut: STOP_TIMEOUT` (60) (code check) |
| 19 | 1 Stop | "Then run `cubby uninstall` again to remove the unit" | WORKS | exit 0 `Removed the systemd agent.` after the manual stop (SURVIVE and REFUSE cases) |
| 20 | 2 Evidence | the 4-line `EVIDENCE=... cp -a ... echo "$EVIDENCE"` block | WORKS | exit 0, `~/cubby-evidence-20260928-031341` with `cubby.lock cubby.log heartbeat.json journal.jsonl runs.jsonl`, mode 700; Darwin line skipped |
| 21 | 2 Evidence | after a rollback, 0.1 writes to `~/.local/state/cubby` and `~/Library/Logs/cubby.log*` on every platform | WORKS | 0.1 install printed `Logs: ~/Library/Logs/cubby.log` on Linux; 0.1 agent lines landed there |
| 22 | 3 Undo | `cubby history` | WORKS | runs listed with id, mode, moved, undo state |
| 23 | 3 Undo | `cubby log --run ID` "the files one run moved" | WORKS | 5 lines `[Documents] (type) alpha.txt` ... for run `20260928T031349-790b7485` |
| 24 | 3 Undo | `cubby undo --run ID` | WORKS | see rows 25 to 27 |
| 25 | 3 Undo | Restored with a suffix when the original name is taken | WORKS | `Downloads/gamma.txt` created by hand, undo printed `restored gamma (1).txt`, both present |
| 26 | 3 Undo | Skipped for good: prints `skip (no longer at ...)`, exits 0, run `undone` | WORKS | `beta.txt` moved away: `skip (no longer at ~/Downloads/Documents/beta.txt): beta.txt`. A run whose only problem was such a skip (epsilon.txt): exit 0, history `undone`. |
| 27 | 3 Undo | Pending: exit 1, `partly undone`; fix the cause and run `cubby undo --run ID` again; retry safe | WORKS | `chmod 555 Downloads/Images`: exit 1, history `partly undone`, `cat.png` still only in `Images/` (inode 11361203), nothing in `Downloads`. After `chmod 755`: `restored cat.png`, exit 0, history `undone`, exactly one `Downloads/cat.png` (same inode 11361203), no `cat (1).png`. A third `undo --run` says `nothing to undo`, exit 0. |
| 28 | 3 Undo | how a pending file shows in the output | UNCLEAR | The pending file prints `skip (cannot restore cat.png): [Errno 13] Permission denied: ...`, i.e. also a `skip (` line, while the runbook tells the reader to look for `skip (...)` lines to find files to move by hand. The CLI hint then says `run 'cubby undo' again`, the runbook says `cubby undo --run ID`. |
| 29 | 3 Undo | A deduplicated file is restored as a copy of the one kept | WORKS | `dedupe = true`: journal `"op": "dedupe"` for `dup.txt`; undo restored `Downloads/dup.txt` (inode 11361201) beside `Documents/dup.txt` (inode 11360440), same bytes |
| 30 | 3 Undo | Undo appends to the journal and never rewrites it | WORKS | first 6 lines byte-identical to the evidence copy, file grew to 30 lines |
| 31 | 3 Undo | `unknown` run: `cubby log --warnings` shows `could not write` lines | WORKS | journal made read-only: `cubby run` exit 1, 7 `WARNING could not write the undo journal ... 'cubby undo' will not be able to put back ...`; history `unknown` |
| 32 | 3 Undo | "move its files back by hand, from the lines of `cubby log --run ID`" | WRONG | The log line gives the top category and the name the file had before the move, not where it is now: `[Documents] (type) dup.txt` for a file that landed as `Documents/dup (1).txt` (the `Documents/dup.txt` there is another, older file); `[Invoices] (name) invoice-march.txt` for a file in `Invoices/2026-09/`. Moving "Documents/dup.txt" back would move the wrong file. The source folder is not in the line either. |
| 33 | 3 Undo | `cubby undo` "revert the most recent run that has something left to undo", after an `unknown` run | UNCLEAR | Latest run `20260928T031441` was `unknown` (journal lost). Plain `cubby undo` silently reverted an older run instead (`restored song.mp3`, `restored report.pdf.txt`, from 03:12:28), exit 0. Literally what the comment says, but an operator reading "undo the bad run" undoes a different, older one. |
| 34 | 3 Undo | Before 0.3.0: retry left `name (1).ext`; `ls -li` shows the same inode; delete the `(1)` one | WORKS | Reproduced on tag v0.2.0: failed undo, retry printed `restored cat (1).png`; `ls -li`: `11360588 ... 2 ... cat (1).png` and `11360588 ... 2 ... cat.png` (link count 2). Deleting one name leaves the file. |
| 35 | 3 Undo | "Before 0.3.0" as the way to tell affected builds | UNCLEAR | The fixed build under test also reports `cubby 0.2.0`, so `cubby --version` cannot tell a fixed 0.2.0 from the released, affected 0.2.0. In the released 0.2.0 a failed move during a run also leaves two names (CHANGELOG, "Fixed"), not only an undo retry; the runbook only mentions undo. |
| 36 | 4 Rollback | `grep ^ExecStart ~/.config/systemd/user/cubby.service` | WORKS | `ExecStart=".../cubby" "watch" "--wait-for-source" "--delay" "0" "--interval" "1s"` |
| 37 | 4 Rollback | `plutil -extract ProgramArguments json -o - ~/Library/LaunchAgents/com.cubby.agent.plist` | NOT EXECUTABLE HERE | macOS only. `launchd.py` writes key `ProgramArguments` in `~/Library/LaunchAgents/com.cubby.agent.plist`: path and key match. |
| 38 | 4 Rollback | `cubby uninstall` before replacing the package | WORKS | exit 0, agent stopped, `pgrep` exit 1 |
| 39 | 4 Rollback | `pipx install --force "git+https://github.com/DeharengOlivier/cubby.git@v0.1.0"` (and the venv `pip` fallback) | NOT EXECUTABLE HERE | No network, no pipx. Checked: `origin` is `https://github.com/DeharengOlivier/cubby.git`, tag `v0.1.0` exists locally, its `pyproject.toml` name is `cubby-sort` with script `cubby`, `install.sh` venv path is `~/.local/share/cubby/venv`. Simulated with `git archive`. |
| 40 | 4 Rollback | `cubby --version` | WORKS | `cubby 0.1.0` (simulated install) |
| 41 | 4 Rollback | `cubby install <the recorded options>`, dropping `--month-style`/`--month-lang` | WRONG | The recorded line contains `watch --wait-for-source --delay 0 --interval 1s`. Passing the recorded options: `cubby: error: unrecognized arguments: --wait-for-source`, exit 2. It worked only after also dropping `--wait-for-source` (and `watch`, which is the subcommand, not an option): `cubby install --delay 0 --interval 1s`, exit 0. |
| 42 | 4 Rollback | "0.1 rejects `--wait-for-source`" | WORKS | same error as row 41 |
| 43 | 4 Rollback | 0.1 has no pause, `history`, `log`, `undo --run` | WORKS | `invalid choice: 'history'`, `invalid choice: 'pause'`, `unrecognized arguments: --run x` |
| 44 | 4 Rollback | a pause set under 0.2 is ignored by a 0.1 agent | WORKS | `cubby pause` (0.2), file `eta01.txt` sorted by the 0.1 agent within 3 s |
| 45 | 4 Rollback | 0.1 logs to `~/Library/Logs/cubby.log` on every platform; its unit has no 60 s grace | WORKS | log there on Linux; 0.1 unit has no `TimeoutStopSec` and no `StandardOutput` |
| 46 | 4 Rollback | Cubby 0.2 reads journals written by 0.1 | WORKS | after roll forward, plain `cubby undo` reverted the 0.1 `cubby run` line (`restored nu01.txt`, `restored mu01.txt`), exit 0 |
| 47 | 4 Rollback | 0.1 `cubby undo` fails with `KeyError: 'moves'` while the last line is from 0.2; changes nothing | WORKS | traceback ending `KeyError: 'moves'`, exit 1; sha256 of `journal.jsonl` identical before and after |
| 48 | 4 Rollback | "runs made by 0.1 after the rollback undo normally" | WRONG | True for a manual 0.1 `cubby run` (`mu01.txt` restored, exit 0). False for the 0.1 agent: `cubby watch` in 0.1 journals nothing (`Sorter(... journal=Journal())` only in `cmd_run`). The 0.1 agent moved `zeta01.txt` and `eta01.txt`, `journal.jsonl` mtime did not change, and 0.1 `cubby undo` then failed with `KeyError: 'moves'`. No version can undo those moves; they are only in `~/Library/Logs/cubby.log`. |
| 49 | 4 Rollback | Rolling forward to 0.2 undoes the 0.2 runs as before | WORKS | `cubby undo --run 20260928T031519-f2524df1`: `restored pi.txt`, exit 0 |
| 50 | 4 Rollback | with `CUBBY_STATE_DIR` set, 0.1 sees none of the 0.2 runs; undo 0.1 runs later with `CUBBY_STATE_DIR=~/.local/state/cubby cubby undo` | WORKS | separate home: 0.1 `undo` `nothing to undo`; 0.2 with the alt dir restored `a.txt`; 0.2 with `CUBBY_STATE_DIR=~/.local/state/cubby` restored the 0.1 run's `b.txt` |
| 51 | 5 Remove | `cubby uninstall` | WORKS | clean case exit 0 |
| 52 | 5 Remove | `pipx uninstall cubby-sort` / `rm -rf ~/.local/share/cubby/venv ~/.local/bin/cubby` | NOT EXECUTABLE HERE | no pipx (exit 127 when pasted). Package name `cubby-sort` matches `pyproject.toml` and `uninstall.sh`. |
| 53 | 5 Remove | the `rm -rf` state/config/log line | WRONG | Runs, exit 0, but incomplete: cubby also reads `~/.cubby.toml` (`user_config_candidates()` in `adapters/config.py`, listed in `docs/usage.md`), which the line does not remove. It also leaves the empty `~/Library/Logs` created by 0.1 on Linux (cosmetic). |
| 54 | 5 Remove | `rm -rf ~/cubby-evidence-*` | WORKS | exit 0 |
| 55 | 5 Remove | the block when `cubby uninstall` fails (agent survives) | WRONG | Nothing says to stop. Pasted as written: `cubby uninstall` exit 1, `pipx` exit 127, `rm -rf` exit 0. The `rm -rf` deleted `paused.json`, so the still-running, paused agent resumed (`resumed` in a new `cubby.log`) and sorted 3 files into a new journal, while the old journal was gone. |
| 56 | 5 Remove | "`./uninstall.sh` does the first two lines" | WRONG | SURVIVE: `uninstall.sh` printed the uninstall error and `warning: could not remove the background agent`, then deleted `~/.local/bin/cubby` and the venv, printed `cubby removed.` and exited 0. The agent kept running with its unit, and the `cubby` CLI needed by section 1 was gone. |
| 57 | 6 Damaged | a damaged line in `journal.jsonl` or `runs.jsonl` is skipped | WORKS | garbage line and a truncated line without a newline appended to the journal, one `runs.jsonl` line overwritten: `history` exit 0 (only the damaged run missing), `status` exit 0, `run` + `undo` exit 0 |
| 58 | 6 Damaged | unreadable `paused.json` counts as a pause, status says so, `cubby resume` clears it | WORKS | `paused  paused (unreadable paused.json; run 'cubby resume' to clear it): no file is moved`; `pi.txt` not moved; after `cubby resume` it was sorted and `paused.json` was gone |
| 59 | 6 Damaged | damaged `heartbeat.json` shows `last pass never` until the next pass | WORKS | `last pass never`, 2.5 s later `last pass 1s ago` |
| 60 | 7 Report | `cubby status --json`, `cubby history --json`, `cubby log --run ID` | WORKS | all exit 0 |
| 61 | 7 Report | "All three contain full paths and file names" | UNCLEAR | `history --json` holds the source folder path but no file names (except failures); `log --run` holds file names but no paths (except warning lines). Harmless for privacy, but not what the text says. |

Totals: WORKS 43, WRONG 8, UNCLEAR 6, NOT EXECUTABLE HERE 4 (61 rows; row 16 counts as WORKS with the caveat of row 14).

Specific checks requested:

- After an undo that failed on a permission and was retried: exactly one file under the original name (`Downloads/cat.png`, inode unchanged), no `cat (1).png`. Yes (row 27). On released v0.2.0 the duplicate appears, as the runbook warns (row 34).
- `cubby uninstall` exits non-zero when the agent survives and keeps the unit file: yes when the manager still reports it `active` (row 8) or refuses (row 7). No when the manager reports `inactive` but the process lives: exit 0 and the unit is deleted (row 9).
- `cubby status` names the still-running process: yes when no unit is installed (rows 11, 17). No when the unit was kept because the agent survived: text says `running (systemd)` without a pid (row 10).

## Defects and proposed corrections

1. **Section 5 has no stop condition (row 55).** Pasting the block after a failed `cubby uninstall` deletes `paused.json` and un-pauses a surviving agent, and deletes the journal it would need. Replace the first line with:
   ```sh
   cubby uninstall || { echo "agent not removed: follow section 1, then start again"; exit 1; }
   cubby status             # must say "not installed", with no "still sorting (pid N)"
   ```
   and add before the block: "Run the rest only when `cubby status` says `not installed` with no pid. Deleting the state folder while an agent still runs lifts its pause."

2. **`./uninstall.sh` is not equivalent to the first two lines (row 56).** It removes the CLI and exits 0 even when the agent could not be removed. Runbook text: "`./uninstall.sh` removes the CLI even when the agent could not be removed. Run `cubby uninstall` first and use `./uninstall.sh` only after it exits 0." (Code fix to suggest to the maintainer: make `uninstall.sh` exit 1 before touching the package when `cubby uninstall` fails.)

3. **0.1 agent runs cannot be undone (row 48).** Replace "and runs made by 0.1 after the rollback undo normally" with: "Only a manual `cubby run` in 0.1 writes the journal; the 0.1 agent (`cubby watch`) journals nothing, so what it moves can be undone by no version and is listed only in `~/Library/Logs/cubby.log`. While a 0.1 agent sorts, `cubby undo` in 0.1 keeps failing with `KeyError: 'moves'` (the last journal line is still from 0.2)." Consider recommending: after a rollback, run 0.1 by hand (`cubby run`) rather than reinstalling the agent, if undo matters.

4. **Recorded options include `--wait-for-source` (row 41).** Replace "0.1 also rejects `--month-style` and `--month-lang`: drop them from the recorded options." with: "The recorded options are the flags after `watch --wait-for-source`. Pass only those to `cubby install`, and drop `--month-style` and `--month-lang`, which 0.1 also rejects. Example: `ExecStart=... "watch" "--wait-for-source" "--delay" "0" "--interval" "1s"` becomes `cubby install --delay 0 --interval 1s`."

5. **Manual restore from `cubby log --run ID` points at the wrong file (row 32).** Replace "move its files back by hand, from the lines of `cubby log --run ID`" with: "move its files back by hand. A log line `[Category] (reason) name` gives the category folder and the name before the move; the file may be in a subfolder (`Invoices/2026-09/`) or renamed with a suffix (`name (1).ext`) if the name was taken. Find it with `find <source>/<Category> -newermt '<run start>' -type f` and check the content before moving it back to the folder `cubby status` shows as `watching`."

6. **`cubby uninstall` exit code claim is too strong (row 9), and `is-active` is not a sufficient check (row 14).** Replace "`cubby uninstall` exits non-zero if the service manager refuses or the agent is still running afterwards" with "...or the service manager still reports the agent as active afterwards. An agent the manager lost track of is not seen by `uninstall`: that is why `cubby status` and `pgrep` follow it." In the Linux list add after `is-active`: "`inactive` is not enough: `cubby status` must also show no pid."

7. **`cubby status` does not name a surviving pid when the unit is kept (row 10), and there is no escalation when `systemctl stop` does not stop it (row 14).** Replace the sentence about status with: "`cubby status` names a live cubby process only when no unit is installed (`not installed, but cubby (pid N) is still sorting`). With the unit still installed, get the pid from `cubby status --json` (`agent.live_pid`) or `pgrep -fa 'cubby watch'`." Change the foreground bullet to: "An agent that does not stop through its manager, or a foreground `cubby watch`: `kill -TERM <pid>`, then check again with `cubby status`."

8. **`pgrep -fl` hides the command line on Linux (row 6).** Use `pgrep -fa 'cubby watch'   # Linux; on macOS pgrep -fl` (procps `-l` prints only the process name, `python3`).

9. **Section 5 misses `~/.cubby.toml` (row 53).** Add `~/.cubby.toml` to the `rm -rf` line (and optionally `rmdir ~/Library/Logs 2>/dev/null` on Linux after a 0.1 rollback).

10. **Plain `cubby undo` after an `unknown` run reverts an older run (row 33).** Add to the `unknown` paragraph: "Do not run plain `cubby undo` then: it skips the `unknown` run and reverts the most recent older run that still has something to undo. Always name the run with `cubby undo --run ID`."

11. **Pending files print as `skip (cannot restore ...)` (row 28).** In "Pending" add: "Undo prints `skip (cannot restore NAME): <error>` for such a file; unlike `skip (no longer at ...)`, it is retried by the next undo." Optionally use `cubby undo --run ID` in the CLI hint too, to match the runbook.

12. **"Before 0.3.0" cannot be checked with `cubby --version` (row 35).** State which builds are affected in terms the operator can check ("cubby 0.2.0 as released, and 0.1") or bump the development version, and add: "The same double name can come from a failed move during a run in those versions, not only from an undo retry: `ls -li` shows it the same way."

13. **Section 7 wording (row 61).** Replace "All three contain full paths and file names" with "Together they contain folder paths and file names".

Non-defect notes: pause, damaged-state handling, taken names, dedupe, append-only journal, the permission retry (one file, same inode), refusal and `active`-survivor handling of `cubby uninstall`, and the whole 0.2 to 0.1 to 0.2 journal compatibility (except defect 3) behaved exactly as written.
