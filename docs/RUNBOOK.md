# Runbook

What to do, in order, when cubby does something it should not. Owner: the maintainer,
[@DeharengOlivier](https://github.com/DeharengOlivier); security reports go through a
[private advisory](https://github.com/DeharengOlivier/cubby/security/advisories/new) (see
`SECURITY.md`). Every command below runs as the user who installed cubby; none needs root.

This runbook was executed end to end twice on 2026-09-28, by operators working from it
alone, in a throwaway home with a faked service manager; each defect they found was fixed:
`docs/audits/2026-09-28-runbook-drill.md` and `docs/audits/2026-09-28-runbook-drill-2.md`.

`STATE` below is the state folder: `${CUBBY_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/cubby}`
(`cubby doctor` prints it).

## 1. Stop the agent now

The fastest switch, with no service manager involved:

```sh
cubby pause              # the agent moves nothing more, from the next file on
cubby status             # "paused ... no file is moved"
```

The agent stays running (its heartbeat shows it alive) but skips every pass until
`cubby resume`. To take it off the machine instead:

```sh
cubby uninstall          # stops the agent, checks it stopped, removes its launchd/systemd unit
cubby status             # must say "not installed", not "... cubby (pid N) is still sorting"
pgrep -fa 'cubby watch'  # must print nothing (on macOS: pgrep -fl)
```

`cubby uninstall` exits non-zero if the service manager refuses, if the manager still reports
the agent as active (it then keeps the unit file), or if a cubby process still sends heartbeats
after the manager said it stopped (it prints that pid). `cubby status` shows the pid of the
cubby that is sorting: "running (systemd, pid N)", or "not installed, but cubby (pid N) is
still sorting"; `cubby status --json` has it as `agent.live_pid`. Stop it by hand:

- macOS:
  ```sh
  launchctl bootout gui/$(id -u)/com.cubby.agent   # by label, works even without the plist
  launchctl list com.cubby.agent                   # must fail: "Could not find service"
  ```
- Linux:
  ```sh
  systemctl --user stop cubby.service              # works on the loaded unit
  systemctl --user disable cubby.service           # if ~/.config/systemd/user/cubby.service exists
  systemctl --user is-active cubby.service         # "inactive" (or "unknown"), and then:
  cubby status                                     # no pid: "inactive" alone is not enough
  ```
- An agent its manager lost track of, or a foreground `cubby watch`: `kill -TERM <pid>` with the
  pid `cubby uninstall` or `cubby status` printed, then `cubby status` again.

Stopping sends SIGTERM: the agent finishes the file in progress, stops the pass between two
files and exits, so no move is left without its journal line. The launchd and systemd units
give it 60 seconds for that (`ExitTimeOut`, `TimeoutStopSec`) before killing it. Then run
`cubby uninstall` again to remove the unit.

## 2. Keep the evidence

Before undoing anything, copy the state folder. It holds the undo journal, the run ledger,
the heartbeat and (on Linux) the log:

```sh
EVIDENCE=~/cubby-evidence-$(date +%Y%m%d-%H%M%S)
cp -a "${CUBBY_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/cubby}" "$EVIDENCE"
if [ "$(uname)" = Darwin ]; then cp -a ~/Library/Logs/cubby.log* "$EVIDENCE"/; fi
echo "$EVIDENCE"
```

After a rollback to 0.1 (section 4), also copy `~/.local/state/cubby` and, on every platform,
`~/Library/Logs/cubby.log*`: that is where 0.1 writes. These files list the names of downloaded
files: keep the copy private, and delete it once the incident is closed (section 5).

## 3. Put the files back

```sh
cubby history            # recent runs: when, how many moved, how much was undone
cubby log --run ID       # the files one run moved, and any warning it logged
cubby undo               # revert the most recent run that has something left to undo
cubby undo --run ID      # revert a specific run from the history
```

What undo does with each file of the run:

- **Restored**: put back under its original name. If that name is taken now, it is restored
  next to it with a suffix, for example `notes (1).txt`.
- **Skipped for good**: the file is no longer where the run put it (moved again, deleted).
  Undo prints `skip (no longer at ...)`. Look for those lines and move such files back by
  hand, if they still exist somewhere.
- **Left in place**: a file is at that place, but not the one the run put there: it was
  replaced by another file of the same name, or changed since (its size or modification time
  differs). Moving it could take a file cubby never moved, so undo prints
  `skip (changed or replaced since the run: PATH is left in place; move it back to ORIGINAL
  by hand if it is yours)`. A folder counts as the same folder while one file that was
  in it when it was moved (the first by name) is still in it, unchanged: files added,
  renamed or removed around it do not stop undo, a folder deleted and made again does. An
  empty folder counts as the same while nothing was added to it. Runs made by cubby 0.2.0
  and older did not record this, and undo moves what it finds there.
- **Duplicate not recreated**: for a duplicate that `dedupe` deleted, the kept copy changed or
  was replaced since the run. Undo prints `skip (the copy kept at PATH changed or was replaced
  since the run, so the duplicate is not recreated from it)`. Nothing is to be moved back:
  the kept copy is yours, as it is now.
- After a skip of any kind, undo still prints `Restored N file(s).` for the others, then
  `cubby: N no longer where the run put it` or `cubby: N changed or replaced since the run`,
  and exits 1 (0.2.0 exited 0). These entries are settled for good: the run shows as
  `undone`, and running undo again does not retry them.
- **Pending**: restoring failed with an error (a permission, a full disk), or undo could not
  even look at the place the run put the file (a folder on the way is unreadable). Undo prints
  `pending (cannot restore NAME): <error>` (`skip (cannot restore ...)` in 0.2.0), exits 1,
  and the run shows as `partly undone` (or still `undoable` if nothing of it was restored).
  Fix the cause and run `cubby undo --run ID` again; a failed attempt changes nothing, so the
  retry is safe.
- A deduplicated file is restored as a copy of the one that was kept.

Undo appends to the journal and never rewrites it; do not edit the journal by hand.

A run shown as `unknown` is not in the journal. Compaction (once the journal passes 5 MB)
drops only runs that are older than the 200 most recent **and** have nothing left to undo,
so such a run needs nothing. A run that is `unknown` while its files are still misplaced
means the journal was lost or could not be written (`cubby log --warnings` shows
`could not write` lines). Then:

- Do not run plain `cubby undo`: it skips the `unknown` run and reverts the most recent older
  run that still has something to undo. Always name the run: `cubby undo --run ID`.
- Move its files back by hand. Each line of `cubby log --run ID` reads
  `[Category] (reason) old-name -> where/it/went`, relative to the sorted folder (`watching`
  in `cubby status`); move each file from the right-hand path back to that folder under its
  old name. A line `old-name deleted, duplicate of where/it/is` is a duplicate `dedupe`
  removed: copy that file back under the old name only if you want the duplicate again. (cubby 0.2.0 and older log only the old name: look for the file in the category
  folder and its month subfolders, possibly renamed `name (1).ext`, and check its content.)

**In cubby 0.2.0 and 0.1**, a move that failed after linking the file (a read-only folder, during
a run or an undo) left the file under two names, and a retried undo restored `name (1).ext`
beside `name.ext`. `ls -li` shows both names with the same inode number: delete the `(1)` one.
Later versions leave one name.

## 4. Roll back to a previous version

Record the agent's options, and remove the agent **before** replacing the package:

```sh
grep ^ExecStart ~/.config/systemd/user/cubby.service                                     # Linux
plutil -extract ProgramArguments json -o - ~/Library/LaunchAgents/com.cubby.agent.plist  # macOS
cubby uninstall
pipx install --force "git+https://github.com/DeharengOlivier/cubby.git@v0.1.0"
# without pipx: ~/.local/share/cubby/venv/bin/pip install --force-reinstall "git+https://github.com/DeharengOlivier/cubby.git@v0.1.0"
cubby --version
cubby install <the recorded options>   # only when you want the old version to sort
```

Uninstall first because a 0.1 agent cannot start from a 0.2 unit: it rejects
`--wait-for-source` and restarts in a loop. The options to pass to `cubby install` are the
flags after `watch --wait-for-source`, without `--month-style` and `--month-lang`, which 0.1
also rejects. For example `ExecStart=... "watch" "--wait-for-source" "--delay" "0" "--interval" "1s"`
becomes `cubby install --delay 0 --interval 1s`.

**The 0.1 agent journals nothing**: what it moves can be undone by no version, and is listed only
in `~/Library/Logs/cubby.log`. If undo matters, run 0.1 by hand (`cubby run`, which journals)
instead of reinstalling its agent.

**0.1 has no pause, no `history`, no `log` and no `undo --run`.** A `cubby pause` set under 0.2
is ignored by a 0.1 agent, which starts moving files again at once: that is why the agent is
uninstalled, not paused, before rolling back. 0.1 writes its log to `~/Library/Logs/cubby.log`
on every platform, and its unit has no 60-second stop grace.

Compatibility, measured in the rollback rehearsal and the runbook drill of 2026-09-28 (0.2 to
0.1 and back, in a throwaway home):

- Cubby 0.2 reads journals written by 0.1.
- Cubby 0.1 cannot read the 0.2 journal format: while the last journal line comes from 0.2,
  `cubby undo` in 0.1 fails with `KeyError: 'moves'`. It changes nothing (the journal file was
  byte-identical afterwards). Runs made by a manual 0.1 `cubby run` after the rollback undo
  normally once the last journal line is theirs; while it is still from 0.2, 0.1 `cubby undo`
  keeps failing with that error.
- Rolling forward to 0.2 again undoes the 0.2 runs as before.
- These hold only when neither `XDG_STATE_HOME` nor `CUBBY_STATE_DIR` is set. 0.1 always uses
  `~/.local/state/cubby`, including when the 0.2 unit sets `CUBBY_STATE_DIR`. Then 0.1 sees none
  of the 0.2 runs (its `cubby undo` says `nothing to undo`), and the runs 0.1 makes go to a
  journal 0.2 never reads: undo them in 0.1 before rolling forward, or afterwards with
  `CUBBY_STATE_DIR=~/.local/state/cubby cubby undo`.

So **undo the 0.2 runs you want reverted before rolling back**, or roll forward to undo them
later.

## 5. Remove cubby completely

First make sure the agent is gone. Deleting the state folder while an agent still runs lifts
its pause and takes away its journal.

```sh
cubby uninstall              # must exit 0; if not, follow section 1 and start again
cubby status                 # must say "not installed", not "... cubby (pid N) is still sorting"
```

Only then:

```sh
pipx uninstall cubby-sort    # pipx install; otherwise: rm -rf ~/.local/share/cubby/venv ~/.local/bin/cubby
rm -rf "${CUBBY_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/cubby}" ~/.local/state/cubby \
       "${XDG_CONFIG_HOME:-$HOME/.config}/cubby" ~/.config/cubby ~/.cubby.toml ${CUBBY_CONFIG:+"$CUBBY_CONFIG"} ~/Library/Logs/cubby.log*
rm -rf ~/cubby-evidence-*    # once the incident is closed: section 2's copies list your downloads
```

From a checkout, `./uninstall.sh` runs `cubby uninstall` and removes the program. From 0.3.0
it stops without removing anything when `cubby uninstall` reports that the agent could not be
stopped (exit 1), and removes a broken install whose `cubby` cannot even start, with a
warning; `./uninstall.sh --force` removes cubby in every case. Earlier scripts removed the CLI
even when the agent was still running. Sorted files stay where they are; cubby never deletes a file except
an opt-in, byte-identical duplicate (`dedupe = true`), and that is journaled.

## 6. A state file is damaged

Copy the state folder first (section 2), then:

- A damaged line in `journal.jsonl` or `runs.jsonl` is skipped: `history`, `status` and `undo`
  keep working with the other lines. Do not repair the journal by hand.
- An unreadable `paused.json` counts as a pause (cubby moves nothing when it cannot tell);
  `cubby status` says so, and `cubby resume` clears it.
- A damaged `heartbeat.json` shows `last pass never` until the agent's next pass rewrites it.

## 7. Report it

Open an issue with the output of `cubby status --json`, `cubby history --json` and the
relevant `cubby log --run ID` lines. Together they contain folder paths and file names:
remove the ones you do not want to share. For a security problem, open a
[private advisory](https://github.com/DeharengOlivier/cubby/security/advisories/new) instead
of a public issue.
