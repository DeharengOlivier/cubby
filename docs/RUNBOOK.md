# Runbook

What to do, in order, when cubby does something it should not. Owner: the maintainer,
[@DeharengOlivier](https://github.com/DeharengOlivier); security reports go through a
[private advisory](https://github.com/DeharengOlivier/cubby/security/advisories/new) (see
`SECURITY.md`). Every command below runs as the user who installed cubby; none needs root.

This runbook was last executed end to end on 2026-09-28, in a throwaway home with a faked
service manager: `docs/audits/2026-09-28-runbook-drill.md`.

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
cubby status             # must say "not installed", with no "still sorting (pid N)"
pgrep -fl 'cubby watch'  # must print nothing
```

`cubby uninstall` exits non-zero if the service manager refuses or the agent is still running
afterwards; in both cases it keeps the unit file. `cubby status` names any cubby process whose
heartbeat is still fresh ("not installed, but cubby (pid N) is still sorting"). Stop it by hand:

- macOS:
  ```sh
  launchctl bootout gui/$(id -u)/com.cubby.agent   # by label, works even without the plist
  launchctl list com.cubby.agent                   # must fail: "Could not find service"
  ```
- Linux:
  ```sh
  systemctl --user stop cubby.service              # works on the loaded unit
  systemctl --user disable cubby.service           # if ~/.config/systemd/user/cubby.service exists
  systemctl --user is-active cubby.service         # must print "inactive" (or "unknown")
  ```
- A foreground `cubby watch` (no agent): `kill -TERM <pid>` with the pid `cubby status` names.

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
  Undo prints `skip (no longer at ...)`, still exits 0, and the run shows as `undone`. Look
  for those lines and move such files back by hand, if they still exist somewhere.
- **Pending**: restoring failed with an error (a permission, a full disk). Undo exits 1 and
  the run shows as `partly undone`. Fix the cause and run `cubby undo --run ID` again; a
  failed attempt changes nothing, so the retry is safe.
- A deduplicated file is restored as a copy of the one that was kept.

Undo appends to the journal and never rewrites it; do not edit the journal by hand.

A run shown as `unknown` is not in the journal. Compaction (once the journal passes 5 MB)
drops only runs that are older than the 200 most recent **and** have nothing left to undo,
so such a run needs nothing. A run that is `unknown` while its files are still misplaced
means the journal was lost or could not be written (`cubby log --warnings` shows
`could not write` lines): move its files back by hand, from the lines of `cubby log --run ID`.

**Before 0.3.0**, a failed undo attempt could leave a second name for the file, and the retry
then restored `name (1).ext` beside `name.ext`. If you retried an undo with an older cubby,
`ls -li` shows both names with the same inode number: delete the `(1)` one.

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
`--wait-for-source` and restarts in a loop. 0.1 also rejects `--month-style` and
`--month-lang`: drop them from the recorded options.

**0.1 has no pause, no `history`, no `log` and no `undo --run`.** A `cubby pause` set under 0.2
is ignored by a 0.1 agent, which starts moving files again at once: that is why the agent is
uninstalled, not paused, before rolling back. 0.1 writes its log to `~/Library/Logs/cubby.log`
on every platform, and its unit has no 60-second stop grace.

Compatibility, measured in the rollback rehearsal and the runbook drill of 2026-09-28 (0.2 to
0.1 and back, in a throwaway home):

- Cubby 0.2 reads journals written by 0.1.
- Cubby 0.1 cannot read the 0.2 journal format: while the last journal line comes from 0.2,
  `cubby undo` in 0.1 fails with `KeyError: 'moves'`. It changes nothing (the journal file was
  byte-identical afterwards), and runs made by 0.1 after the rollback undo normally.
- Rolling forward to 0.2 again undoes the 0.2 runs as before.
- These hold only when neither `XDG_STATE_HOME` nor `CUBBY_STATE_DIR` is set. 0.1 always uses
  `~/.local/state/cubby`, including when the 0.2 unit sets `CUBBY_STATE_DIR`. Then 0.1 sees none
  of the 0.2 runs (its `cubby undo` says `nothing to undo`), and the runs 0.1 makes go to a
  journal 0.2 never reads: undo them in 0.1 before rolling forward, or afterwards with
  `CUBBY_STATE_DIR=~/.local/state/cubby cubby undo`.

So **undo the 0.2 runs you want reverted before rolling back**, or roll forward to undo them
later.

## 5. Remove cubby completely

```sh
cubby uninstall
pipx uninstall cubby-sort    # pipx install; otherwise: rm -rf ~/.local/share/cubby/venv ~/.local/bin/cubby
rm -rf "${CUBBY_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/cubby}" ~/.local/state/cubby \
       ~/.config/cubby ${CUBBY_CONFIG:+"$CUBBY_CONFIG"} ~/Library/Logs/cubby.log*
rm -rf ~/cubby-evidence-*    # once the incident is closed: section 2's copies list your downloads
```

`./uninstall.sh` does the first two lines from a checkout. Sorted files stay where they are;
cubby never deletes a file except an opt-in, byte-identical duplicate (`dedupe = true`), and
that is journaled.

## 6. A state file is damaged

Copy the state folder first (section 2), then:

- A damaged line in `journal.jsonl` or `runs.jsonl` is skipped: `history`, `status` and `undo`
  keep working with the other lines. Do not repair the journal by hand.
- An unreadable `paused.json` counts as a pause (cubby moves nothing when it cannot tell);
  `cubby status` says so, and `cubby resume` clears it.
- A damaged `heartbeat.json` shows `last pass never` until the agent's next pass rewrites it.

## 7. Report it

Open an issue with the output of `cubby status --json`, `cubby history --json` and the
relevant `cubby log --run ID` lines. All three contain full paths and file names: remove the
ones you do not want to share. For a security problem, open a
[private advisory](https://github.com/DeharengOlivier/cubby/security/advisories/new) instead
of a public issue.
