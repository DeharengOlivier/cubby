# Security Policy

## Scope

Cubby runs locally and only **moves files within a folder you point it at**. It
never deletes files (except, opt-in, a byte-identical duplicate), never uploads
anything, and makes no network calls.

The content stage may invoke local extractors (`pdftotext`, `textutil`, ...) on
files in the watched folder. Only run cubby on folders whose contents you trust,
as you would any tool that reads your files.

## Reporting a vulnerability

Please open a private security advisory on GitHub, or email the maintainer.
Do not file public issues for security reports. We aim to respond within a week.
