# Security Policy

## Scope

Cubby runs locally and only **moves files within a folder you point it at**. It
never deletes files (except, opt-in, a byte-identical duplicate), never uploads
anything, and makes no network calls.

The content stage may invoke local extractors (`pdftotext`, `textutil`, ...) on
files in the watched folder. Only run cubby on folders whose contents you trust,
as you would any tool that reads your files. Extractors are run by the absolute
path `shutil.which` resolved, as a command list and never through a shell, with
a timeout on every call. A file larger than 20 MB is not read at all, and each
backend reads a window rather than the whole file.

## What cubby will not do

- **Write outside the folder you point it at.** Category names and the unsorted
  folder are validated as single folder components, and the move itself refuses
  a destination outside the watched root. Both barriers exist because either one
  could be bypassed by a path nobody anticipated.
- **Sort your home directory or a filesystem root.** `source` refuses both:
  cubby creates category folders inside it and moves what it finds there.
- **Move files with no way back, quietly.** Every move is journaled as it
  happens, by the agent as well as by `cubby run`. If the journal cannot be
  written, the run says so on stderr and in the log rather than proceeding in
  silence.
- **Replace a file.** Moves are no-clobber, even against a file that appears
  between choosing a name and moving.
- **Let a document stall it.** PDF, docx and xlsx parsers run in a child
  process with a timeout and, on Linux, a memory ceiling.
- **Pass your folder to the service manager as anything but one argument.** The
  systemd command line is quoted and escaped (`%`, `$`, quotes, newlines).
- **Leave its records readable by others.** The journal, ledger and log are
  created with mode 0600: they name what you downloaded.

## The config file is trusted input

`config.toml` is treated as yours. Its `name_patterns` and `content_patterns`
are regular expressions compiled and run against your documents, and a
pathological pattern can take a very long time on a short input: `(a+)+$` needs
about 3.7 seconds on 27 characters. Patterns are checked for validity when the
config loads, but their cost is not bounded, and a shared or copied config
should be read before it is used, exactly like a shell script.

## Checking a release

Each GitHub release carries a `SHA256SUMS` file, and the wheel and sdist rebuild
byte for byte from the tagged source. To check that a release was built from
that source and nothing else (needs git and [uv](https://docs.astral.sh/uv/)):

```sh
git clone https://github.com/DeharengOlivier/cubby.git && cd cubby
git checkout v0.3.0
gh release download v0.3.0 -p SHA256SUMS    # or download it from the release page
scripts/rebuild.sh --against SHA256SUMS
```

Releases before 0.3.0 have no `scripts/rebuild.sh`: check out the tag, then run
the script from main, `git show main:scripts/rebuild.sh | sh -s -- --against SHA256SUMS`.

Then check the files you downloaded against the same sums, in the folder that
holds them: `sha256sum -c --ignore-missing SHA256SUMS` (on macOS,
`shasum -a 256 -c --ignore-missing SHA256SUMS`), which checks the ones you have.

CI runs the same script on every pull request (two builds in different
environments must be identical), and the release workflow runs it against the
artifacts it publishes. The rebuild of v0.2.0 is recorded in
`docs/audits/2026-09-28-reproducible-build.md`.

## Reporting a vulnerability

Open a private security advisory at
<https://github.com/DeharengOlivier/cubby/security/advisories/new>; it reaches the
maintainer, [@DeharengOlivier](https://github.com/DeharengOlivier), and nobody else.
Do not file public issues for security reports. We aim to respond within a week.
