<p align="center">
  <img src="assets/logo.svg" alt="cubby" width="520">
</p>

<p align="center">
  <a href="https://github.com/DeharengOlivier/cubby/actions/workflows/ci.yml"><img src="https://github.com/DeharengOlivier/cubby/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT"></a>
  <a href="https://www.python.org"><img src="https://img.shields.io/badge/python-3.11%2B-blue.svg" alt="Python 3.11+"></a>
</p>

<p align="center"><em>Tidy your Downloads folder automatically. Every file finds its cubby.</em></p>

<p align="center">
  <img src="assets/demo.svg" alt="cubby plan demo" width="640">
</p>

# Cubby

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
cubby init        # write a starter config to ~/.config/cubby/config.toml
cubby plan        # preview where everything would go (moves nothing)
cubby explain F   # where would file F go, and which rule decides? (moves nothing)
cubby run         # sort the folder once
cubby history     # recent runs, their counts, which were undone
cubby undo        # revert the last run (or --run ID from history)
cubby watch       # keep sorting in the foreground (Ctrl-C to stop)
cubby install     # register the background agent (sorts every minute)
cubby uninstall   # remove the agent
cubby pause       # stop the agent moving files (--for 2h), without uninstalling
cubby resume      # let it sort again
cubby status      # is the agent really running? paused? when did it last pass? what failed?
cubby doctor      # show environment and content-extraction support (--notify to test alerts)
```

Everything is configurable on the command line:

```sh
cubby run --source ~/Desktop --delay 30s --no-content
cubby install --delay 2m --interval 1m
```

By default a file is only moved once it has sat still for **1 minute** (`--delay`),
so in-progress downloads are never grabbed mid-write.

## Invoices, filed by month and renamed

Categories flagged with `date_folders` (the shipped `Invoices` and
`Bank-Statements`) file each document into a **month/year subfolder** taken from
the date printed on the document, falling back to the download date when none is
readable. Invoices additionally get a clean name, `<vendor> facture <date>`:

```
Invoices/
  2026-07/
    spotify facture 2026-07-07.pdf
  2026-06/
    ovh facture 2026-06-30.pdf
```

It reads dates and vendors in **French and English**. Pick the folder style on
the command line:

```sh
cubby run --month-style numeric          # 2026-07 (default)
cubby run --month-style letters          # juillet 2026
cubby run --month-style letters --month-lang en   # July 2026
```

When cubby cannot confidently identify the vendor it keeps the original filename
(it never guesses), and `cubby plan` previews every subfolder and rename before
anything moves.

## How it works

For each file, cubby walks a cascade and stops at the first stage that produces
a category:

```
.dmg / .png / .mp4   ──▶  strong extension   (an installer is an installer)
Invoice-2026.pdf     ──▶  filename match
3c0fe3ad-….pdf       ──▶  content match       (reads the text inside)
random.pages         ──▶  type fallback        (by extension)
mystery.qzx          ──▶  _Unsorted
```

Specific categories are tried before broad ones, so `Invoices` wins over the
catch-all `Documents`. See [docs/architecture.md](docs/architecture.md).

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

Reading is bounded, because cubby runs unattended. A file over 20 MB is not
opened at all (its name and type still route it), each backend reads a window
rather than the whole file, and every external converter runs with a timeout.
Asking for 4 KB of text out of a 315 MB page costs neither time nor memory.

Sorting itself is linear in the number of files, and cheap:

| Files | Plan | Apply | Memory |
| --- | --- | --- | --- |
| 1 000 | 0.02 s | 0.16 s | ~5 MB |
| 5 000 | 0.07 s | 0.80 s | ~23 MB |
| 20 000 | 0.33 s | 3.62 s | ~78 MB |

Measured, not estimated, and re-runnable on your own hardware:

```bash
python benchmarks/bench_sort.py
python benchmarks/bench_sort.py 500 5000
```

Apply is dominated by the moves themselves. The ceiling of this design is that
one pass holds the whole folder listing and its outcomes in memory, which is a
few tens of megabytes at twenty thousand files.

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
