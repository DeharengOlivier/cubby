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

- `extraction.py` - text extraction with graceful multi-backend fallback.
- `filesystem.py` - candidate discovery, eligibility, collision-safe moves, and
  the `build_ref` factory that wires extraction into a `FileRef`.
- `config.py` - load and merge TOML into the domain `Config`.
- `service/` - background-service backends (`launchd`, `systemd`) behind a
  common `Service` interface, chosen by `factory.detect_service`.
- `logging.py` - an append-only file logger.

### `app/` - use cases

- `sorter.py` - `Sorter` orchestrates engine + filesystem for one pass.
- `watcher.py` - `Watcher` runs the poll loop; `sleep` and `stop` are injected
  so it is unit-testable without real time.
- `report.py` - result types and human-readable rendering.

### `cli.py` - entry point

Parses arguments, builds overrides, loads config, and calls a use case. It holds
no business logic.

## Why this shape

- **Testability**: 55 tests run in well under a second with no real files for
  the domain, because the engine has no IO.
- **Portability**: swapping launchd for systemd is a new adapter, nothing else
  changes. The poll-based watcher avoids OS-specific file-event APIs.
- **Safety**: all filesystem mutation is confined to `adapters/filesystem.py`.
