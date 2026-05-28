# Contributing

Thanks for your interest in cubby.

## Development setup

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev,extract]"
pytest
```

## Project layout

See [docs/architecture.md](docs/architecture.md). In short:

- `domain/` is pure: no IO, no third-party imports, fully unit-testable.
- `adapters/` is where every IO concern lives.
- `app/` holds use cases; `cli.py` only wires things together.

When adding a feature, put logic in the layer that owns it. If the engine needs
new data about a file, add it to `FileRef` and have the filesystem adapter
populate it, rather than reaching into the filesystem from the domain.

## Conventions

- **Commits**: [Conventional Commits](https://www.conventionalcommits.org)
  (`feat:`, `fix:`, `test:`, `docs:`, `refactor:`, `chore:`, `build:`, `ci:`).
- **Style**: `ruff format` and `ruff check`; type hints everywhere.
- **Tests**: every behaviour change ships with a test. Keep domain tests free of
  real files.

## Before opening a PR

```sh
ruff check src tests
ruff format --check src tests
pytest
```
