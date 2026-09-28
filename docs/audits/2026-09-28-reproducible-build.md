# Reproducible build: v0.2.0 rebuilt from its tag

2026-09-28. Does the published v0.2.0 release match its source, and would a
build that stopped being reproducible be noticed?

## The rebuild

The artifacts attached to the
[v0.2.0 release](https://github.com/DeharengOlivier/cubby/releases/tag/v0.2.0)
were built by `release.yml` on a GitHub runner. They were rebuilt here from a
checkout of the tag (`db85fec`) on another machine (Linux 6.8, uv 0.12.13), with
`uv build --build-constraint build-constraints.txt --require-hashes`, three times:

| Build | Environment | Wheel SHA-256 | Sdist SHA-256 |
|---|---|---|---|
| published | GitHub runner | `c29fa7de...8549d281` | `684f690a...8f637966` |
| 1 | umask 022, local time zone | `c29fa7de...8549d281` | `684f690a...8f637966` |
| 2 | umask 077, `TZ=Pacific/Auckland`, `LC_ALL=C` | `c29fa7de...8549d281` | `684f690a...8f637966` |
| 3 | umask 002, sources dated 2001-01-01 (`touch -d 2001-01-01 src/cubby/*.py`) | `c29fa7de...8549d281` | `684f690a...8f637966` |

All four are identical, byte for byte. The backend (hatchling 1.32.4, pinned by
hash) normalizes file dates and permissions in both archives, so neither the
machine, the umask, the time zone nor the file dates reach the artifacts.

`scripts/rebuild.sh --against SHA256SUMS`, run in a checkout of v0.2.0 with the
release's own `SHA256SUMS`, printed `OK` for both files and exited 0. With one
sum altered it printed `1 computed checksum did NOT match` and exited 1.

## Keeping it true

A claim checked once decays. `scripts/rebuild.sh` builds the committed tree twice
from fresh exports, the second one extracted and built under umask 077 (so the
source files carry other permissions), with another time zone and locale, and
with every file dated 2001-01-01. It fails when the two differ, when a build
does not make exactly one wheel and one sdist, and, with `--against`, when the
sums file does not list exactly the rebuilt files or one of them differs:

- CI runs it in the required "Lint, types and layering" job on every pull
  request and every push to main.
- `release.yml` runs it with `--against dist/SHA256SUMS` before publishing, so a
  release whose artifacts cannot be rebuilt from the tag is not published.
- `tests/test_rebuild_script.py` runs the script against a fake `uv` that leaks
  the source files' permissions, the time zone, the locale or the file dates
  into its output, and checks that each leak fails the script. It also checks
  that matching sums pass, and that altered sums, sums covering only one of the
  files, and a build missing an artifact fail.

## Not verified

- A real rebuild on macOS. The macOS CI jobs run the script's tests (with the
  fake `uv`), but which of `sha256sum` or the `shasum -a 256` fallback they use
  was not checked; the real rebuild runs on Ubuntu only.
- Python versions other than the build's: the wheel is pure Python and the
  build runs no project code, so none is expected to matter.
