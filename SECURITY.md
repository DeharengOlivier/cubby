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
- **Move files with no way back, quietly.** If the undo journal cannot be
  written, the run says so on stderr rather than proceeding in silence.

## The config file is trusted input

`config.toml` is treated as yours. Its `name_patterns` and `content_patterns`
are regular expressions compiled and run against your documents, and a
pathological pattern can take a very long time on a short input: `(a+)+$` needs
about 3.7 seconds on 27 characters. Patterns are checked for validity when the
config loads, but their cost is not bounded, and a shared or copied config
should be read before it is used, exactly like a shell script.

## Reporting a vulnerability

Please open a private security advisory on GitHub, or email the maintainer.
Do not file public issues for security reports. We aim to respond within a week.
