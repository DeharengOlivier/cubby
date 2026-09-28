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

Cubby is a small command-line tool and background agent that tidies one folder
(your `~/Downloads` by default). Each file is filed into a category folder by a
cascade of rules: its extension when that is decisive, then its name, then the
text inside it, then its type. Anything no rule can place goes to `_Unsorted`.
Every move is journaled and can be undone, nothing is ever overwritten, and
nothing leaves the folder you point it at.

It runs on macOS and Linux, needs Python 3.11 or later, and has no runtime
dependencies (three optional libraries read more document formats).

## Contents

- [Quick start](#quick-start)
- [How a file is sorted](#how-a-file-is-sorted)
- [Reading inside files](#reading-inside-files)
- [Invoices and statements: month folders and renaming](#invoices-and-statements-month-folders-and-renaming)
- [Duplicates](#duplicates)
- [Downloads in progress and the settle delay](#downloads-in-progress-and-the-settle-delay)
- [Preview first: plan and explain](#preview-first-plan-and-explain)
- [Sort: run and watch](#sort-run-and-watch)
- [The background agent](#the-background-agent)
- [What happened: status, history and log](#what-happened-status-history-and-log)
- [Undo](#undo)
- [Pause and resume](#pause-and-resume)
- [Notifications, colours and doctor](#notifications-colours-and-doctor)
- [JSON output for scripts](#json-output-for-scripts)
- [Configuration](#configuration)
- [Safety guarantees](#safety-guarantees)
- [Performance](#performance)
- [Install, upgrade and uninstall](#install-upgrade-and-uninstall)
- [Verifying a release](#verifying-a-release)
- [Quality](#quality)
- [Command reference](#command-reference)
- [Documentation](#documentation)

## Quick start

```sh
git clone https://github.com/DeharengOlivier/cubby.git && cd cubby
./install.sh            # the CLI (pipx when available, else a private virtualenv)

cubby plan              # preview where everything would go; moves nothing
cubby run               # sort the folder once
cubby undo              # changed your mind? put it all back
cubby install           # let a background agent keep the folder tidy
cubby status            # is the agent running, and what did it do?
```

`./install.sh --service` installs the CLI and the agent in one go. To sort
another folder, pass `--source ~/Desktop` or set `source` in the
[configuration](#configuration).

## How a file is sorted

For each entry at the top level of the watched folder, cubby walks a cascade
and stops at the first stage that names a category:

| Stage | Rule | Example |
|---|---|---|
| 0. strong extension | a category with `strong_ext = true` owns the extension | `setup.dmg` goes to `Installers` whatever its name |
| 1. name | `name_patterns` (regular expressions) match the file name | `Invoice-2026.pdf` goes to `Invoices` |
| 2. content | `content_patterns` match the text read inside the file, then `late_content_patterns` | `3c0fe3ad-....pdf` saying "Invoice number" goes to `Invoices` |
| 3. type | the extension is in a category's `extensions` | `random.pages` goes to `Documents` |
| otherwise | | `mystery.qzx` goes to `_Unsorted` |

- **Cryptic names skip the name stage.** A name that looks like a UUID, a long
  run of digits or a long hex id carries no human signal, so a stray substring in
  it cannot pick the category; content and type still get their turn.
- **Order matters.** Within a stage, categories are tried top to bottom, so the
  specific ones come first and the broad catch-all (`Documents`) last. For stage 0,
  the first category that owns an extension wins.
- **Late content patterns** are tried only when no category's `content_patterns`
  matched. The shipped Invoices title patterns live there, so a bank statement
  that mentions a "facture" stays a statement.
- Patterns are Python regular expressions, matched case-insensitively.
- **What cubby looks at**: only the top level of the folder. Hidden entries, the
  folders cubby files into and names matching an `ignore` pattern are never
  touched. A folder is sorted as one item, by its name, and keeps that name:
  a saved web page's `page_files` folder goes to `Documents`, and an
  `invoice-archive` folder goes to `Invoices/invoice-archive`, never renamed
  like an invoice nor filed by month (a folder that a finance rule matches
  and that is named like a month folder, such as `2026-09`, becomes
  `2026-09 (folder)`, so invoices are never filed into it). A folder no rule
  matches goes whole to `_Unsorted`. Its content is never sorted.

The shipped categories, in order: `Invoices`, `Bank-Statements`, `Legal`,
`Resumes` (name and content rules, in French and English), `Images`, `Video`,
`Music`, `Installers`, `Archives`, `Fonts` (decisive extensions), `Presentations`,
`Ebooks`, `Subtitles`, `Code`, `Design`, `Travel`, and `Documents` as the catch-all.
They are all in [`src/cubby/data/default.toml`](src/cubby/data/default.toml).
More in [docs/configuration.md](docs/configuration.md#the-cascade).

## Reading inside files

The content stage reads text with whatever converter is available, first found
wins:

| Format | Converters, in order |
|---|---|
| PDF | `pdftotext` (first two pages), then `pypdf` (first two pages) |
| docx | `python-docx`, then `textutil` (macOS) |
| doc, rtf | `textutil` (macOS), else `antiword` or `catdoc` (Linux) |
| html, htm | `textutil` (macOS), then a built-in tag stripper |
| xlsx | `openpyxl` (first 20 rows of the active sheet) |
| txt, md, csv, tsv, log | read directly |

`pypdf`, `python-docx` and `openpyxl` are the optional `extract` extra (see
[Install](#install-upgrade-and-uninstall)); `cubby doctor` shows which converters
are present. Only the first `content_max_bytes` characters (4000) are scanned,
and `--no-content` or `content_scan = false` turns the stage off.

Reading is bounded, because the agent runs unattended on files anyone can drop
in the folder:

- A file larger than 20 MB is never opened; its name and type still route it.
- Each converter reads a window, not the whole file.
- Every converter, a system tool or the Python parser, runs in a child process
  with a 15 second timeout, from `/` rather than the Downloads folder, by the
  absolute path found on `PATH`, as an argument list and never through a shell.
- On Linux each child also gets a 1 GB address-space ceiling (macOS does not
  enforce one; the timeout still applies).

A converter that is installed but breaks on a file (a timeout, a crash, an
exit with an error) does not stop the sort: the next converter is tried, and if
none gives text the file is routed by name and type. That case is not hidden: the
log gets a WARNING naming the file, each converter and how it failed, and
`cubby status` counts those files over 24 hours. A converter that is not
installed is not a failure; only `cubby doctor` mentions it.

## Invoices and statements: month folders and renaming

Categories with `date_folders = true` (the shipped `Invoices` and
`Bank-Statements`) file each document into a month folder. Those that also set
`vendor_rename = true` (the shipped `Invoices`) are renamed
`<vendor> facture <date>`, as in the [plan below](#preview-first-plan-and-explain)
(`Invoices/2026-07/spotify facture 2026-07-07.txt`).

**The date** is, in this order:

1. a date printed in the document, in French or English (`2026-07-07`,
   `07/07/2026`, `7 juillet 2026`, `July 7, 2026`); one just after a label
   (`Date`, `Facture du`, `Invoice date`...) wins, else the first one in the text;
2. a date in the file name (`2026-07-15`, `20260715`, `15.07.2026`, `2026-08`,
   `août 2026`), unless it is later than the file's own date (an invoice is not
   dated after it arrived);
3. the file's modification date, which files it but is never written into the
   name (`spotify facture.pdf`).

**The vendor** is the first entry of the `vendors` list that appears in the name
or the text (47 are shipped: `spotify`, `ovh`, `github`...), else the first real word of
the file name. Generic words (`invoices`, `your`, `bill`, `scan`, month names,
invoice-number prefixes such as `INV-` or `FA-`) are never a vendor. When no
vendor is certain, the original name is kept: cubby does not guess.

**The folder style** is `2026-07` by default. `--month-style letters` gives
`juillet 2026`, and with `--month-lang en`, `July 2026`; the settings are
`month_style` and `month_lang`. A rename never overwrites, and `cubby undo`
restores the original name.

## Duplicates

With `dedupe = true`, a file byte-identical to the one already filed under the
same name in its destination is deleted instead of becoming `report (1).pdf`.
It is off by default. Identity is checked by size and SHA-256; a symlink or an
unreadable file is never a duplicate. `plan` lists such a file apart, under
`Would delete as duplicates` (`report.txt   duplicate of Documents/report.txt`),
the run reports it, and `cubby undo` recreates it from the kept copy.

## Downloads in progress and the settle delay

A file moves only once its last change is older than the delay (`--delay`,
setting `delay`, default `1m`; units `s`, `m`, `h`, `d`), so a download still
being written is never grabbed. Files with an in-progress extension are always
left alone: `crdownload`, `part`, `download`, `tmp`, `partial` and `opdownload`
by default (setting `skip_ext` replaces that list; an empty list keeps the
defaults).

## Preview first: plan and explain

`cubby plan` shows the whole folder as it will be once everything has settled,
grouped by destination, with the stage that decided each file (no mark means
the name decided). It moves nothing and ignores the delay:

```
$ cubby plan
Bank-Statements/2026-08/  (1)
    releve-aout.txt

Documents/  (2)
    fresh.txt   <- type
    notes.md   <- type

Images/  (1)
    photo.png   <- strong-ext

Installers/  (1)
    installer.dmg   <- strong-ext

Invoices/2026-07/  (1)
    spotify facture 2026-07-07.txt   <- content   (was 3c0fe3ad-1b2c-4d5e-9f00-112233445566.txt)

Invoices/2026-08/  (1)
    ovh facture 2026-08.txt   (was Invoice-2026-08-ovh.txt)

Resumes/  (1)
    cv-jane.docx

_Unsorted/  (2)
    mystery.qzx   <- unsorted
    old stuff   <- unsorted

Left alone  (1)
    movie.mp4.part   download in progress (.part)

Would move 10 item(s).
```

It also lists the duplicates `dedupe` would delete, what would fail, and a file
standing where a category folder must go.

`cubby explain FILE...` answers for a run now: whether the file stays where it
is and why (ignored, in progress, too recent, outside the watched folder), where
it goes, the exact rule, the rename, and how much text it read when no content
pattern matched:

```
$ cubby explain ~/Downloads/3c0fe3ad-1b2c-4d5e-9f00-112233445566.txt ~/Downloads/fresh.txt
~/Downloads/3c0fe3ad-1b2c-4d5e-9f00-112233445566.txt
  goes to            Invoices/2026-07/spotify facture 2026-07-07.txt
  decided by         content matches 'invoice number'  (content stage)
  renamed            spotify facture 2026-07-07.txt
~/Downloads/fresh.txt
  stays where it is  too recent: moves once it is 1m old
  would go to        Documents/fresh.txt
  decided by         extension .txt  (type stage)
  content            read (6 characters), no content pattern matched
```

## Sort: run and watch

`cubby run` sorts the folder once, moving only settled files, and ends with the
same sections as `plan`. A file that cannot be moved (a permission, a vanished
file) is reported and left in place, the others are still sorted, and the run
exits 1. `-v` echoes each move.

`cubby watch` keeps sorting in the foreground, one pass every `--interval`
(default `30s`), until Ctrl-C or SIGTERM. A stop ends the pass between two
files, never in the middle of one. A pass that fails is logged and the next one
runs on schedule. With `--wait-for-source` (what the agent uses) a missing folder,
such as an unplugged drive, is waited for instead of being an error.

Only one cubby sorts or undoes at a time: each pass and each undo holds a lock.
A manual `run` or `undo` waits up to 60 seconds for it; the agent skips a pass
after 30 seconds and tries again at the next one.

## The background agent

```sh
cubby install                               # start now and at every login
cubby install --delay 2m --interval 1m      # with your own settings
cubby uninstall                             # stop it and remove it
```

| | macOS | Linux |
|---|---|---|
| Service manager | launchd, per-user LaunchAgent | systemd `--user` service |
| Unit file | `~/Library/LaunchAgents/com.cubby.agent.plist` | `~/.config/systemd/user/cubby.service` |
| Restart | `KeepAlive` | `Restart=on-failure` |
| Log | `~/Library/Logs/cubby.log` | `cubby.log` in the state folder |

The agent runs `cubby watch --wait-for-source` with the `--config`, `--source`,
`--delay`, `--interval`, `--month-style` and `--month-lang` you gave to
`install`, and `--no-content` when you gave it. It also receives `CUBBY_STATE_DIR`, and `CUBBY_CONFIG` or
`XDG_CONFIG_HOME` when set, so it reads the same files as your shell.

`install` checks what the service manager answers and that the agent is really
running afterwards. `uninstall` checks that it stopped, keeps the unit when it
did not, and exits 1 with the `kill` command when a cubby process is still
sorting. On stop, the agent gets 60 seconds to finish its file. Without launchd
or systemd, `install` says so; run `cubby watch` under tmux or nohup instead.
What to do when the agent misbehaves: [docs/RUNBOOK.md](docs/RUNBOOK.md).

## What happened: status, history and log

`cubby status` asks launchd or systemd whether the agent runs, reads its
heartbeat and the run ledger, and sums up the last 24 hours:

```
$ cubby status
agent           not installed
last watched    ~/Downloads
last pass       2s ago, took 0.643 s, moved 1, 2 failed, 2 waiting to settle
last run        2026-09-28T15:27:54  moved 1, 2 failed  (watch, run 20260928T152753-914d87710efb704f)
  broken.pdf  PermissionError: Permission denied: broken.pdf -> Documents/broken.pdf (check cubby's permissions there)
  notes2.md  PermissionError: Permission denied: notes2.md -> Documents/notes2.md (check cubby's permissions there)
last 24 h       2 runs, moved 10, 2 failed, content unreadable for 1 (see cubby log --warnings)
  2x PermissionError: Permission denied
     last seen 2026-09-28T15:27:54; files: broken.pdf, notes2.md; cubby 0.3.0
recent log      ~/.local/state/cubby/cubby.log
  2026-09-28T15:27:54  WARNING 2 item(s) could not be sorted
```

It shows the agent state and its pid, a pause, the last pass (duration, moves,
failures, files still settling), the last run and its failures, and the 24-hour
activity with failures grouped by kind of error and the cubby versions that hit
them. `content unreadable for N` counts files sorted by name and type because
every converter broke on them. `status` exits 1 when an installed agent is not
running, when its last pass is older than three intervals or two minutes,
whichever is longer, or
when it has completed no pass two minutes after install.

`cubby history` lists recent runs, newest first (`-n`, default 20): time, id,
agent (`watch`) or manual (`run`), counts, and the undo state (`undoable`,
`partly undone`, `undone`, or `unknown` once the journal no longer holds the run):

```
$ cubby history
2026-09-28T15:27:54  20260928T152753-914d87710efb704f  watch  moved 1, 2 failed  undone
2026-09-28T15:27:32  20260928T152732-20e11043476200a1  run    moved 9

Undo one with: cubby undo --run <id>
```

`cubby log` prints the agent's log, oldest first, the rotated file included:
`--run ID` keeps one pass, `--warnings` keeps what went wrong, `-n` sets the
number of lines (20), `--json` prints the records. Each record in the log file
(and in `--json`) carries the cubby version and, during a pass, the run id used
by `history` and `undo`; the human view shows the time, level and message:

```
$ cubby log --warnings
2026-09-28T15:27:54  WARNING content extraction failed for broken.pdf: pdftotext: exit: status 1; pdf parser: exit: status 3, invalid pdf header: b'\\xfb\\x13_\\x1e\\x92'
2026-09-28T15:27:54  WARNING could not sort notes2.md: PermissionError: Permission denied: notes2.md -> Documents/notes2.md (check cubby's permissions there)
```

## Undo

`cubby undo` reverts the most recent run, manual or agent, that has anything
left to undo; `cubby undo --run ID` picks a run from `history`. Files are put
back newest first:

```
$ cubby undo --run 20260928T152732-20e11043476200a1
restored releve-aout.txt
...
restored Invoice-2026-08-ovh.txt
skip (changed or replaced since the run: ~/Downloads/Invoices/2026-07/spotify facture 2026-07-07.txt is left in place; move it back to ~/Downloads/3c0fe3ad-1b2c-4d5e-9f00-112233445566.txt by hand if it is yours): 3c0fe3ad-1b2c-4d5e-9f00-112233445566.txt
Restored 8 file(s).
cubby: 1 changed or replaced since the run, left in place.
```

The rules:

- **Only what the run moved.** Undo works from the journal, never by guessing.
  Each move recorded the file's device, inode, size and modification time; a
  file found at that place that differs (replaced by another of the same name,
  or edited since) is left in place and named. A folder is recognised by one file
  it held when it was moved, still inside it and unchanged.
- A file moved or deleted since the run is skipped and counted.
- **Never overwrites.** If the original name is taken, the file comes back as
  `notes (1).txt`, and undo says so.
- A duplicate deleted by `dedupe` is recreated as a copy of the kept file.
- **Tidies up.** A category or month folder the undo emptied is removed; a
  folder with anything else in it stays.
- A file that cannot be restored (a permission, a full disk) stays pending; fix
  the cause and run `cubby undo --run ID` again. A failed attempt changes nothing.
- Undo exits 1 whenever a file was not put back.

## Pause and resume

`cubby pause` stops the agent from the next file on, even in the middle of a
pass, without uninstalling it. `--for 2h` ends the pause by itself (at most
`366d`); without it, the pause lasts until `cubby resume`. A pause file cubby cannot read counts as a pause. `cubby status`
shows the pause, and a manual `cubby run` still works (it says the agent is paused).

## Notifications, colours and doctor

With `notify = true` (the default) the agent raises a desktop notification once
per file it cannot sort (again only if that file recovers and fails later), once
when passes start failing, and once when the watched folder goes missing. It uses
`osascript` on macOS (allow notifications for Script Editor) and `notify-send`
on Linux, with the message as an argument, never inside a script, and file names
escaped. `cubby doctor --notify` sends a test and exits 1 if it fails.

Human output is coloured when it goes to a terminal, with a logo banner for
`plan` and `run`; colour is off when `NO_COLOR` is set, `TERM=dumb`, or the
output is piped. A file name can never drive the terminal: control characters
and bidi overrides in names are shown as escapes (`\x1b`, `\u202e`) in every
human output.

`cubby doctor` prints the version, platform, service manager, config file,
source, state folder, log, the converters and libraries found, the formats read,
and whether notifications can reach you.

## JSON output for scripts

`plan`, `explain`, `status` and `history` take `--json`, and `log --json` prints
one JSON record per line (nothing at all when there is no log yet). Each has a
published JSON Schema in [`docs/schemas/`](docs/schemas): `plan`, `explain`,
`status`, `history` and `log-record`. The test suite validates the real outputs
against them. The top-level `version` of `plan`, `explain`, `status` and `history` changes
when a field is removed or changes meaning (in a `log-record`, `version` is the
cubby version that wrote it); new fields may be added, so ignore the ones you do not know.

```sh
cubby plan --json | jq -r '.items[] | select(.category == "Invoices") | .renamed_to'
cubby status --json | jq '.healthy, .agent.last_pass, .activity.extraction_failures'
```

## Configuration

Cubby reads three layers, later winning: the packaged defaults, one user file,
then command-line flags. The user file is the first found of `$CUBBY_CONFIG`,
`$XDG_CONFIG_HOME/cubby/config.toml` (when set to an absolute path),
`~/.config/cubby/config.toml`, `~/.cubby.toml`; `--config FILE` names one
explicitly. `cubby init` writes an annotated copy of the defaults there; it never
overwrites or shadows a config cubby already reads without `--force`.

`[settings]` keys merge one by one; a `[[category]]` list in your file
**replaces** the default categories. The settings and their defaults:

| Setting | Default | Meaning |
|---|---|---|
| `source` | `"~/Downloads"` | folder to sort (not your home or a filesystem root) |
| `delay` | `"1m"` | minimum age before a file moves |
| `interval` | `"30s"` | time between passes in watch mode |
| `content_scan` | `true` | read inside files |
| `content_max_bytes` | `4000` | characters of text scanned |
| `unsorted_dir` | `"_Unsorted"` | where unplaced files go |
| `dedupe` | `false` | delete byte-identical duplicates |
| `skip_ext` | 6 in-progress extensions | never moved |
| `month_style`, `month_lang` | `"numeric"`, `"fr"` | month folder style |
| `vendors` | 47 names | known invoice vendors, matched first |
| `ignore` | `[]` | file-name globs never touched, any case (`"*.torrent"`) |
| `notify` | `true` | desktop notifications from the agent |

A category takes `name`, `name_patterns`, `content_patterns`,
`late_content_patterns`, `extensions`, `strong_ext`, `date_folders` and
`vendor_rename`. The file is read strictly: an unknown key is an error naming
the closest known one, a switch must be `true` or `false`, every pattern must
compile, and two categories cannot share a name. A bad config exits 2:

```
$ cubby plan
cubby: config error: unknown setting 'ignor' in [settings]; did you mean 'ignore'?
```

Full reference: [docs/configuration.md](docs/configuration.md). Ready-made
snippets: [docs/recipes.md](docs/recipes.md) and
[`examples/personal.example.toml`](examples/personal.example.toml).

**Where cubby keeps its own files.** The state folder is `$CUBBY_STATE_DIR`, else
`$XDG_STATE_HOME/cubby`, else `~/.local/state/cubby`. It holds `journal.jsonl`
(every move, for undo), `runs.jsonl` (the run ledger, trimmed to its last 2000
runs past 2 MB), `heartbeat.json`, `paused.json`, `cubby.lock` and, except on
macOS, `cubby.log` (JSON lines, rotated at 1 MB). Every file is created readable by you only, since
they name what you downloaded.

## Safety guarantees

- **Moves, never deletes**, except a byte-identical duplicate when you turn on
  `dedupe`, and that is journaled and undoable.
- **Never overwrites.** A file is hard-linked to its new name, which fails if the
  name is taken, even by a file that appears at the last moment, and only then
  unlinked (a folder, or a move across disks, is checked just before, under the
  lock). Collisions get `(1)`, `(2)` suffixes, and a failed move changes nothing.
- **Stays inside the folder.** Category names and `unsorted_dir` must be single
  folder names, every destination is checked against the watched root, and
  `--source` refuses a folder cubby files into (no `Documents/Documents`).
- **Journaled as it happens.** Each move is written to the undo journal the moment
  it is made, by the agent too, so a run killed half way can still be undone.
  If the journal cannot be written, cubby says so on stderr and in the log. A
  damaged journal line is skipped, never fatal; what can still be undone is never
  dropped when the journal is compacted.
- **Hostile names are inert**: escaped in the terminal, in JSON, in notifications
  and in the systemd unit, where a folder with a space, `%`, `$`, a quote or a
  newline reaches the agent as one argument.
- **No network.** Cubby makes no network call and uploads nothing.
- Your config is trusted input: its regular expressions run on your documents,
  so read a shared config before using it. See [SECURITY.md](SECURITY.md) and
  [docs/THREAT-MODEL.md](docs/THREAT-MODEL.md).

## Performance

Sorting is linear in the number of files, journal included: 0.6 to 0.9 ms
of CPU per file moved (on a busy machine), and nothing once the folder is sorted.

| Files | Apply, CPU | Idle pass | Memory of the agent's pass |
| ---: | ---: | ---: | ---: |
| 1 000 | 0.72 s (median) | 0.00 s | under 1 MB |
| 20 000 | 11.08 s (median) | 0.00 s | 4 MB |
| 200 000 | 124.57 s (median) | 0.01 s | 26 MB |
| 400 000 | 337 s (one run) | 0.01 s | 54 MB |

The CPU medians come from the 0.3.0 benchmark (the memory change since left
CPU per file unchanged within noise), and the 400 000 row is one run of the
current code. Measured, not estimated (method, limits and next steps in
[docs/PERFORMANCE.md](docs/PERFORMANCE.md)), and re-runnable on your own hardware:

```bash
python benchmarks/bench_sort.py
python benchmarks/bench_sort.py 500 5000 --repeat 7
```

Apply is dominated by the moves themselves. The agent's pass holds the sorted
names of the folder and nothing per file it sorts, about 135 bytes a file (it
was 2.6 KB, 522 MB at 200 000 files, in 0.3.0). `cubby plan` and `cubby run`
keep every outcome to print it, 1.3 to 1.4 KB a file. `cubby history` and
`cubby undo` read a 200,000-move journal in 1.3 to 1.6 s of CPU.

## Install, upgrade and uninstall

**From a checkout**, `./install.sh` uses [pipx](https://pipx.pypa.io) when it is
installed, else a virtualenv in `~/.local/share/cubby/venv` with a `cubby` link
in `~/.local/bin`. `./install.sh --service` also runs `cubby install`, passing on
`--delay`, `--source`, `--interval` and `--config`.

**With pipx, from a release tag** (cubby is not published on PyPI):

```sh
pipx install "git+https://github.com/DeharengOlivier/cubby.git@v0.3.0"
pipx inject cubby-sort pypdf python-docx openpyxl   # optional: the extract extra
```

**Upgrade** by installing the newer tag over it, then restart the agent on the
new code:

```sh
pipx install --force "git+https://github.com/DeharengOlivier/cubby.git@vX.Y.Z"
cubby doctor                  # checks the config still loads
cubby install                 # again, with the options you used before
```

Read the [CHANGELOG](CHANGELOG.md) first; rolling back is installing the
previous tag ([docs/RUNBOOK.md](docs/RUNBOOK.md), section 4).

**Uninstall** with `cubby uninstall`, then `pipx uninstall cubby-sort`, or
`./uninstall.sh` from a checkout, which stops when the agent could not be
stopped (`--force` removes cubby anyway). Your sorted folders and config stay;
removing the state folder too is in [docs/RUNBOOK.md](docs/RUNBOOK.md), section 5.

## Verifying a release

Each GitHub release carries `SHA256SUMS`, and its wheel and sdist rebuild byte
for byte from the tagged source. To check a release against its source (needs
git and [uv](https://docs.astral.sh/uv/)):

```sh
git clone https://github.com/DeharengOlivier/cubby.git && cd cubby
git checkout v0.3.0
gh release download v0.3.0 -p SHA256SUMS    # or download it from the release page
scripts/rebuild.sh --against SHA256SUMS
```

Without `--against`, the script builds HEAD twice under a different umask, time
zone, locale and file dates and fails unless both builds are identical. CI runs it
on every pull request, and the release workflow runs it against what it
publishes. Details: [SECURITY.md](SECURITY.md#checking-a-release).

## Quality

About 900 tests run on macOS and Linux with Python 3.11 to 3.13,
including property-based tests (Hypothesis) and an end-to-end journey that runs
the agent as a real process; CI requires 90% coverage overall and on changed
code. Mutation testing of the modules that could lose a file (journal,
filesystem, undo, naming) kills 98% of mutants. CI also runs ruff, strict mypy,
layering contracts, shellcheck, `pip-audit`, `bandit` and a secret scan. The
audits, mutation rounds and runbook drills are recorded in
[docs/audits/](docs/audits), and the status of every control in
[docs/READINESS.md](docs/READINESS.md).

## Command reference

| Command | What it does | Options |
|---|---|---|
| `cubby plan` | preview the sort of the whole folder; moves nothing | common, `--json` |
| `cubby run` | sort the folder once (settled files only) | common |
| `cubby explain FILE...` | where each file goes, which rule decides, or why it stays | common, `--json` |
| `cubby watch` | keep sorting in the foreground | common, `--wait-for-source` |
| `cubby install` | register and start the background agent (launchd or systemd) | common |
| `cubby uninstall` | stop and remove the agent, and check it stopped | |
| `cubby status` | agent health, last pass, last run, 24 h activity, recent log | `--json` |
| `cubby history` | recent runs and how much of each was undone | `-n/--limit N` (20), `--json` |
| `cubby log` | what cubby logged, oldest first | `-n/--lines N` (20), `--run ID`, `--warnings`, `--json` |
| `cubby undo` | revert the latest run with something left to undo | `--run ID` |
| `cubby pause` | stop the agent moving files, without uninstalling it | `--for DURATION` |
| `cubby resume` | lift a pause | |
| `cubby doctor` | environment, converters and notification check | common, `--notify` |
| `cubby init` | write a starter config | `--path PATH`, `--force` |
| `cubby --version` | print the version | |

The common options: `--config FILE`, `--source DIR`, `--delay 30s`,
`--interval 1m`, `--no-content`, `--month-style numeric|letters`,
`--month-lang fr|en` and `-v/--verbose`. Durations take `s`, `m`, `h` or `d`.

Exit codes: `0` done; `1` something could not be done (a file not sorted or
not restored, the service manager refused, another cubby held the lock, an
unhealthy agent in `status`); `2` invalid configuration or flag, with a message
naming it.

## Documentation

- [Usage](docs/usage.md), [configuration](docs/configuration.md),
  [recipes](docs/recipes.md) and the [FAQ](docs/faq.md)
- [Runbook](docs/RUNBOOK.md): stop the agent, keep evidence, undo, roll back, remove
- [Architecture](docs/architecture.md), [threat model](docs/THREAT-MODEL.md),
  [performance](docs/PERFORMANCE.md), [security policy](SECURITY.md),
  [changelog](CHANGELOG.md)

## License

MIT - see [LICENSE](LICENSE).
