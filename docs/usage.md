# Usage

## Commands

| Command           | What it does                                              |
|-------------------|----------------------------------------------------------|
| `cubby plan`      | Preview the full mapping of the folder. Moves nothing and ignores the age delay, so you see every file. |
| `cubby run`       | Sort the folder once. Only files older than `--delay` are moved. |
| `cubby watch`     | Run the sort loop in the foreground. Ctrl-C to stop.     |
| `cubby install`   | Register a background agent that runs `watch` and starts at login. |
| `cubby uninstall` | Stop and remove the background agent.                    |
| `cubby doctor`    | Print platform, service backend, config in use and extraction support. |

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
tail -f ~/Library/Logs/cubby.log   # watch it work (macOS path)
```

## Safety

- Files are **moved**, never deleted. Name collisions get `(1)`, `(2)` suffixes.
- In-progress downloads (`.crdownload`, `.part`, ...) and files younger than
  `--delay` are skipped.
- Cubby never re-scans its own category folders, so sorting is idempotent.
