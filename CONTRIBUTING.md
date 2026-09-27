# Contributing

Thanks for your interest in cubby.

## Development setup

Cubby uses [uv](https://docs.astral.sh/uv/). Every tool version is pinned in
`uv.lock`, and CI installs exactly that lock.

```sh
uv sync --locked --all-extras   # or: make install
make check                      # format, lint, strict types, layering, tests
```

Clone to green tests takes about two minutes.

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
make check
```

`main` is protected: every change goes through a pull request, the CI checks
must pass (lint, strict types, layering contracts, tests on macOS and Linux with
90 % coverage of the changed lines, dependency and secret scanning) and the
review conversation must be resolved. Administrators are not exempt.

## Releasing

1. Move the `Unreleased` entries of `CHANGELOG.md` under the new version and bump
   `version` in `pyproject.toml` and `src/cubby/__init__.py`.
2. Update `docs/READINESS.md` (release log and any status the release changes).
3. Merge, then tag `vX.Y.Z` on `main`. The release workflow builds the wheel and
   sdist from the tag and attaches them to a GitHub release.
