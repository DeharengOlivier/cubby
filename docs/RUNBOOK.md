# Runbook

What to do, in order, when cubby does something it should not. Owner: the maintainer
(see `SECURITY.md` for the contact). Every command below runs as the user who installed
cubby; none needs root.

## 1. Stop the agent now

```sh
cubby uninstall          # stops the agent and removes its launchd/systemd unit
cubby status             # "not installed" confirms it is stopped
```

`cubby uninstall` exits non-zero if the service manager refuses or the agent survives. In
that case stop it by hand:

- macOS: `launchctl unload -w ~/Library/LaunchAgents/com.cubby.agent.plist`
- Linux: `systemctl --user disable --now cubby.service`

Stopping sends SIGTERM: the agent finishes the file in progress, stops the pass between two
files and exits, so no move is left without its journal line. The launchd and systemd units
give it 60 seconds for that (`ExitTimeOut`, `TimeoutStopSec`) before killing it. Nothing else
runs cubby in the background.

## 2. Keep the evidence

Before undoing anything, copy the state folder. It holds the undo journal, the run ledger,
the heartbeat and (on Linux) the log:

```sh
cp -a "${CUBBY_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/cubby}" ~/cubby-evidence-$(date +%Y%m%d-%H%M%S)
cp -a ~/Library/Logs/cubby.log* ~/cubby-evidence-*/ 2>/dev/null   # macOS log
```

These files list the names of downloaded files: keep the copy private.

## 3. Put the files back

```sh
cubby history            # recent runs, what each moved, how much was undone
cubby undo               # revert the most recent run that has something left to undo
cubby undo --run ID      # revert a specific run from the history
```

- Undo appends to the journal and never rewrites it. An entry that cannot be restored (the
  file was moved again, or the original name is taken) stays pending and is reported; fix the
  cause and run `cubby undo --run ID` again.
- A deduplicated file is restored as a copy of the one that was kept.
- A run shown as `unknown` in the history is no longer in the journal (dropped by compaction
  after 200 newer runs and 5 MB): its files have to be moved back by hand, using the ledger
  entry and the log.

## 4. Roll back to a previous version

```sh
pipx install --force "git+https://github.com/DeharengOlivier/cubby.git@v0.1.0"
# without pipx: ~/.local/share/cubby/venv/bin/pip install --force-reinstall "git+https://github.com/DeharengOlivier/cubby.git@v0.1.0"
cubby --version
```

Then reinstall the agent if it was running: `cubby install` with the same options.

Compatibility, measured in the rollback rehearsal of 2026-09-28 (0.2 to 0.1 and back, in a
throwaway HOME):

- Cubby 0.2 reads journals written by 0.1.
- Cubby 0.1 cannot read the 0.2 journal format: while the last journal line comes from 0.2,
  `cubby undo` in 0.1 fails with `KeyError: 'moves'`. It changes nothing (the journal file was
  byte-identical afterwards), and runs made by 0.1 after the rollback undo normally.
- Rolling forward to 0.2 again undoes the 0.2 runs as before.

So **undo the 0.2 runs you want reverted before rolling back**, or roll forward to undo them
later. Nothing is lost either way.

## 5. Remove cubby completely

```sh
cubby uninstall
pipx uninstall cubby-sort        # or: ./uninstall.sh from a checkout
rm -rf "${CUBBY_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/cubby}" ~/.config/cubby ~/Library/Logs/cubby.log*
```

Sorted files stay where they are; cubby never deletes a file except an opt-in, byte-identical
duplicate (`dedupe = true`), and that is journaled.

## 6. Report it

Open an issue with the output of `cubby status --json`, `cubby history --json` and the
relevant log lines (remove file names you do not want to share). For a security problem,
follow `SECURITY.md` instead of opening a public issue.
