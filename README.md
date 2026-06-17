# Cubby

[![CI](https://github.com/DeharengOlivier/cubby/actions/workflows/ci.yml/badge.svg)](https://github.com/DeharengOlivier/cubby/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org)

> Tidy your Downloads folder automatically. Every file finds its cubby.

Cubby is a small, dependency-light CLI that watches a folder (your `~/Downloads`
by default) and files each new download into the right place using a
**three-stage cascade**:

1. **Filename** - fast and precise (`Invoice-2026.pdf` is obviously an invoice).
2. **Content** - when the name says nothing (`3c0fe3ad-....pdf`), cubby peeks
   inside and reads the text.
3. **File type** - a last-resort fallback by extension (`.png` -> Images).

Anything it cannot confidently place lands in a single `_Unsorted` folder, so
your root stays clean and nothing is ever lost.

```
~/Downloads/
├── Invoices/
├── Bank-Statements/
├── Legal/
├── Resumes/
├── Travel/
├── Images/
├── Music/
├── Installers/
└── _Unsorted/
```

## Install

```sh
git clone https://github.com/DeharengOlivier/cubby.git
cd cubby
./install.sh            # CLI only
./install.sh --service  # CLI + background agent (auto-starts at login)
```

The installer uses [pipx](https://pipx.pypa.io) when available, otherwise a
self-contained virtualenv. Requires Python 3.11+. Works on macOS (launchd) and
Linux (systemd).

## Use

```sh
cubby plan        # preview where everything would go (moves nothing)
cubby run         # sort the folder once
cubby undo        # revert the last run
cubby watch       # keep sorting in the foreground (Ctrl-C to stop)
cubby install     # register the background agent (sorts every minute)
cubby uninstall   # remove the agent
cubby status      # is the agent running? what did it do recently?
cubby doctor      # show environment and content-extraction support
```

Everything is configurable on the command line:

```sh
cubby run --source ~/Desktop --delay 30s --no-content
cubby install --delay 2m --interval 1m
```

By default a file is only moved once it has sat still for **1 minute** (`--delay`),
so in-progress downloads are never grabbed mid-write.

## Content extraction

The content stage uses whatever is available and degrades gracefully:

| Format        | Backend (first found wins)        |
|---------------|-----------------------------------|
| PDF           | `pdftotext`, then `pypdf`          |
| docx          | `python-docx`, then `textutil`    |
| doc / rtf     | `textutil` (macOS), `antiword`    |
| html          | `textutil`, then a stdlib stripper |
| xlsx          | `openpyxl`                        |
| txt/md/csv    | read directly                     |

Install the optional extractors with `pip install 'cubby-sort[extract]'`.
Run `cubby doctor` to see what is active.

## Configure

Cubby ships generic categories. To customise, copy
[`examples/personal.example.toml`](examples/personal.example.toml) to
`~/.config/cubby/config.toml` and edit it. See
[`docs/configuration.md`](docs/configuration.md) for the full reference.

## Docs

- [Usage](docs/usage.md)
- [Configuration](docs/configuration.md)
- [Architecture](docs/architecture.md)

## License

MIT - see [LICENSE](LICENSE).
