# Architecture

Cubby follows a clean, layered design. Dependencies point **inward**: outer
layers know about inner ones, never the reverse.

```
        cli  (entry point: parse args, wire things up)
         |
         v
        app  (use cases: Sorter, Watcher)
       /   \
      v     v
  domain   adapters
 (pure)    (IO: extraction, filesystem, config, services, logging)
      ^_______|
   adapters depend on domain types, not vice versa
```

## Layers

### `domain/` - pure business logic

No IO, no third-party imports. Contains:

- `category.py` - `Category`, `Settings`, `Config` value objects.
- `file_ref.py` - `FileRef` (a lazy, IO-free view of a file) and `Decision`.
- `engine.py` - the classification cascade.
- `duration.py` - duration parsing/formatting.

The engine never touches the filesystem. It reads `FileRef.name/stem/ext` and,
only for the content stage, calls `FileRef.text()`. That method is backed by a
**port** (`read_text`) the adapter layer injects. This keeps the engine a pure
function of its inputs and trivially unit-testable in memory.

### `adapters/` - the outside world

Each adapter implements one IO concern behind a small surface:

- `extraction.py`, `parsers.py` - text extraction with graceful multi-backend
  fallback; the Python parsers run in a bounded child process.
- `filesystem.py` - candidate discovery, eligibility, collision-safe moves, and
  the `build_ref` factory that wires extraction into a `FileRef`.
- `config.py` - load, merge and strictly validate TOML into the domain `Config`.
- `state.py` - the state folder and its owner-only, append-only files.
- `journal.py`, `ledger.py` - the undo journal and the run ledger with heartbeat.
- `lock.py` - the pass lock (see "One writer" below).
- `pause.py`, `notify.py` - the pause switch and desktop notifications.
- `service/` - background-service backends (`launchd`, `systemd`) behind a
  common `Service` interface, chosen by `factory.detect_service`.
- `logging.py` - a JSON-lines file logger, rotated.

### `app/` - use cases

- `sorter.py` - `Sorter` orchestrates engine + filesystem for one pass.
- `watcher.py` - `Watcher` runs the poll loop; `sleep`, `stop`, the pause check
  and the alert channel are injected so it is unit-testable without real time.
- `undo.py`, `explain.py`, `history.py` - the other use cases.
- `report.py` - result types and human-readable rendering.

### `cli/` - entry point

`main.py` parses arguments; `sorting.py`, `inspect.py` and `agent.py` hold the
commands, each a thin call into a use case; `common.py` holds exit codes,
config loading and output helpers. No business logic lives here.

Layering is enforced by import-linter in CI (`[tool.importlinter]` in
`pyproject.toml`): cli over app over adapters over domain, and the domain never
imports `os`, `shutil` or `subprocess`.

## One writer

Cubby assumes it is the only process moving files in its folder. Every pass,
run and undo takes an exclusive lock (`cubby.lock` in the state folder, an
`fcntl` advisory lock) and waits up to 30 s (the agent) or 60 s (a command) for
it. So the agent and a manual `cubby run` never interleave.

- **Limit**: one folder per user, one pass at a time. A first pass over 20 000 files
  takes 11 to 15 s, and a pass over a sorted folder about 10 ms (see
  `docs/PERFORMANCE.md`); the agent polls every 30 s.
- **Not protected**: another program moving files in the same folder at the
  same moment. The no-clobber move still never overwrites a file, and a file
  that vanished mid-pass is reported, not lost.
- **Exit path** if one writer ever stops being enough (several folders, a
  shared network folder): one lock and one agent per folder, keyed by the
  folder's path, and a per-folder state folder. The journal already records
  absolute paths, so undo would not change.

## Why this shape

- **Testability**: the domain is tested in memory with no real files, because
  the engine has no IO; the adapters on temporary folders; the agent as a real
  process.
- **Portability**: swapping launchd for systemd is a new adapter, nothing else
  changes. The poll-based watcher avoids OS-specific file-event APIs.
- **Safety**: moves go through `adapters/filesystem.py` (containment check,
  no-clobber move); undo reverses them through the same functions. Cubby's own
  bookkeeping goes through `adapters/state.py`.
