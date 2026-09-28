# Usage

## Commands

| Command           | What it does                                              |
|-------------------|----------------------------------------------------------|
| `cubby plan`      | Preview the full mapping of the folder. Moves nothing and ignores the age delay, so you see every file. |
| `cubby run`       | Sort the folder once. Only files older than `--delay` are moved. |
| `cubby watch`     | Run the sort loop in the foreground. Ctrl-C to stop.     |
| `cubby undo`      | Reverse the most recent run, manual or agent, restoring files to where they were. `--run ID` picks an older run. |
| `cubby explain FILE...` | Say where each file would go, which rule decides (`name matches 'invoice'`, `extension .dmg`...), the rename, and why a run would leave it alone (ignored, in progress, too recent). Moves nothing. `--json`. |
| `cubby history`   | Recent runs, newest first: when, agent or manual, moved and failed counts, and how much was undone (`undoable`, `partly undone`, `undone`, or `unknown` once the journal no longer holds the run). Runs from cubby 0.1 are not listed; plain `cubby undo` still reverts them. `-n N`, `--json`. |
| `cubby log`       | The last lines the agent logged, oldest first, including the rotated file. `--run ID` keeps one pass (ids from `cubby history`), `--warnings` only what went wrong, `-n N` (20), `--json` the records as JSON lines. |
| `cubby init`      | Write a starter config (the defaults, annotated) to `~/.config/cubby/config.toml`. Never overwrites, and refuses to shadow a config cubby already reads (`CUBBY_CONFIG`, `~/.cubby.toml`), without `--force`; `--force` replaces the file atomically. |
| `cubby install`   | Register a background agent that runs `watch` and starts at login. |
| `cubby uninstall` | Stop and remove the background agent.                    |
| `cubby status`    | Whether the agent is really running (asked of launchd/systemd), when it last completed a pass, what its last run moved or failed, and the recent log. Exits 1 when an installed agent is not running or has stalled. `--json` for scripts. |
| `cubby pause`     | Stop the agent moving files, from the next file on (even mid-pass), without uninstalling it. `--for 2h` resumes by itself. The switch to pull when cubby does something unexpected. |
| `cubby resume`    | Let a paused agent sort again. |
| `cubby doctor`    | Print platform, service backend, config in use, extraction support and whether notifications are on. `--notify` sends a test notification. |

`cubby plan` also accepts `--json` for scripting.

## JSON output

`plan`, `status`, `history`, `explain` and `log` take `--json`. Each output has a JSON Schema
in [`docs/schemas/`](schemas/) (`plan`, `status`, `history`, `explain`, and `log-record` for
each line of `cubby log --json`), and the test suite checks the real outputs against them,
so a field is not renamed or dropped by accident. The top-level `version` changes when a
field is removed or changes meaning; new fields may be added within a version.

## Common flags

These apply to `plan`, `run`, `watch`, `install` and `doctor`:

| Flag           | Meaning                                            | Example          |
|----------------|----------------------------------------------------|------------------|
| `--source`     | Folder to sort                                     | `--source ~/Desktop` |
| `--delay`      | Minimum age before a file is moved                 | `--delay 30s`    |
| `--interval`   | Poll interval in watch mode                        | `--interval 1m`  |
| `--no-content` | Disable the content stage (faster, name+type only) | `--no-content`   |
| `--config`     | Use a specific config file                         | `--config ./my.toml` |
| `-v/--verbose` | Echo each move to stdout                            | `-v`             |

## Typical workflow

```sh
cubby plan            # eyeball the mapping, adjust your config if needed
cubby run             # do a one-off tidy of what is already there
cubby install         # let the agent keep it tidy from now on
cubby status          # is it running, and what did it do last?
```

## Safety

- Files are **moved**, never deleted. Name collisions get `(1)`, `(2)` suffixes.
- In-progress downloads (`.crdownload`, `.part`, ...) and files younger than
  `--delay` are skipped.
- Cubby never re-scans its own category folders, so sorting is idempotent.
- A move never replaces an existing file, even one that appears at the last
  moment.
- A file that cannot be moved (permissions, a file that vanished) is reported
  and left in place; the rest of the run continues. `cubby run` then exits 1.
- Only one cubby sorts or undoes at a time: a manual `run` waits for the
  agent's pass to finish.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | done |
| 1 | something could not be done: a file not sorted, a file not restored, the service manager refused, another cubby held the lock too long, an unhealthy agent (`status`) |
| 2 | the configuration is invalid; the message names the setting |
