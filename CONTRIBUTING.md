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
  end-to-end journey that starts `cubby watch` as a real process. It runs under a
  throwaway `HOME`, with no `XDG_*` or `CUBBY_*` variable.
- The suite never touches your own cubby: it points `HOME` and the XDG folders at a
  session folder, and fails the run, listing the files, when anything in your real
  cubby state, config, log or agent unit changed (`tests/real_state_guard.py`). A
  cubby agent running on your machine rewrites its heartbeat every 30 seconds and
  trips that check when you run `pytest` directly: use `make test`, run
  `env HOME=$(mktemp -d) uv run pytest`, or stop the agent (`cubby uninstall`).
- `make mutation` runs mutation testing on the modules where a silent bug would
  lose or misplace files (journal, containment, undo; see `[tool.mutmut]` in
  `pyproject.toml`). Record the score in `docs/audits/` when those modules change.
  Always run it through `make mutation`, never `mutmut run` directly: mutmut counts
  a mutant that wrote to your real cubby state as killed, and only the target,
  which runs under a throwaway `HOME` and reads the guard's report, fails on it.

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

The required `review record` status also waits for the reviewer's record, a
PR comment that starts with `## Independent review record` and names the
commit the reviewer read, with its full SHA on a line of its own (as plain
text: a line inside a code block or an HTML comment does not count):

```text
## Independent review record

Reviewed head: 0123456789abcdef0123456789abcdef01234567
```

The record counts only for that commit. A push after the review, from a
branch or a fork, turns the status back to failure: post a short re-review record (the same heading,
`(re-review)` after it if you like, and `Reviewed head:` naming the new
head). Get the SHA with `gh pr view N --json headRefOid -q .headRefOid`.
Only comments from the owner, members and collaborators count.

