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
- `app/` holds use cases; `cli/` only wires things together.

When adding a feature, put logic in the layer that owns it. If the engine needs
new data about a file, add it to `FileRef` and have the filesystem adapter
populate it, rather than reaching into the filesystem from the domain.

## Conventions

- **Commits**: [Conventional Commits](https://www.conventionalcommits.org)
  (`feat:`, `fix:`, `test:`, `docs:`, `refactor:`, `chore:`, `build:`, `ci:`).
- **Style**: `ruff format` and `ruff check`; type hints everywhere.
- **Tests**: every behaviour change ships with a test. Keep domain tests free of
  real files.

## Tests

- `make test` runs the suite, including property-based tests (Hypothesis) and an
  end-to-end journey that starts `cubby watch` as a real process.
- `make mutation` runs mutation testing on the modules where a silent bug would
  lose or misplace files (journal, containment, undo; see `[tool.mutmut]` in
  `pyproject.toml`). Record the score in `docs/audits/` when those modules change.

## Releasing

1. On a branch, bump `version` in `pyproject.toml` and `__version__` in
   `src/cubby/__init__.py`, move the `Unreleased` changelog entries under the new
   version, and merge through a pull request.
2. Tag the merge commit on `main` and push the tag:
   `git tag -a vX.Y.Z -m "cubby X.Y.Z" && git push origin vX.Y.Z`.
3. The Release workflow checks the tag against the package version, runs the
   tests, smoke-tests the wheel and publishes the GitHub release with
   `SHA256SUMS`.
4. Rolling back is installing the previous tag; see [docs/RUNBOOK.md](docs/RUNBOOK.md).

## Before opening a PR

```sh
make check
```

`main` is protected: every change goes through a pull request, the CI checks
must pass (lint, strict types, layering contracts, tests on macOS and Linux with
90 % coverage of the changed lines, dependency and secret scanning) and the
review conversation must be resolved. Administrators are not exempt.

