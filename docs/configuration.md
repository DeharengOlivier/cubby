# Configuration

Cubby reads TOML from three layers, later winning:

1. the packaged `src/cubby/data/default.toml` (generic categories)
2. a user file: `~/.config/cubby/config.toml`, `~/.cubby.toml`, or `$CUBBY_CONFIG`
3. command-line flags

`[settings]` keys merge individually. A `[[category]]` list in the user file
**replaces** the default categories entirely, so you can either tweak settings
while keeping the defaults, or define your own taxonomy from scratch.

## Settings

```toml
[settings]
source = "~/Downloads"     # folder to watch
delay = "1m"               # min age before a file is moved (s/m/h/d)
interval = "30s"           # watch-mode poll interval
content_scan = true        # enable the content stage
content_max_bytes = 4000   # how much extracted text to scan
unsorted_dir = "_Unsorted" # where unclassifiable files go
dedupe = false             # drop byte-identical duplicates instead of (1) copies
skip_ext = ["crdownload", "part"]  # in-progress download extensions to ignore
month_style = "numeric"    # invoice subfolder style: "numeric" (2026-07) or "letters"
month_lang = "fr"          # letters language: "fr" (juillet 2026) or "en" (July 2026)
vendors = ["spotify", "ovh"]  # known vendors, matched first when renaming invoices
```

`month_style` and `month_lang` can also be set per run with `--month-style`
and `--month-lang`; `cubby install` bakes them into the background agent.

## Where cubby keeps its own files

Every move, whether made by `cubby run` or by the background agent, is written
to the undo journal the moment it happens, so `cubby undo` can reverse any run,
including one that failed part way. The files cubby keeps for itself live in
its state folder:

| File | Purpose |
|---|---|
| `journal.jsonl` | every move, for `cubby undo` (bounded to the last 200 runs past 5 MB) |
| `runs.jsonl` | one line per run that moved or failed something, for `cubby status` |
| `heartbeat.json` | when the agent last completed a pass |
| `cubby.log` | the activity log, JSON lines, rotated at 1 MB (on macOS: `~/Library/Logs/cubby.log`) |
| `cubby.lock` | makes each pass and each undo exclusive |

The state folder is `$CUBBY_STATE_DIR` when set, else `$XDG_STATE_HOME/cubby`,
else `~/.local/state/cubby`. Every file is created readable by you only: they
name what you downloaded.

## Categories

```toml
[[category]]
name = "Invoices"                       # destination folder name
name_patterns = ["invoice", "facture"]  # stage 1: regex on the filename
content_patterns = ["amount due"]       # stage 2: regex on extracted text
extensions = ["pdf"]                    # stage 3: fallback by extension
strong_ext = false                      # stage 0: make extensions decisive
date_folders = false                    # file into a month/year subfolder
vendor_rename = false                   # rename to "<vendor> facture <date>.<ext>"
```

### Monthly foldering and renaming

A category with `date_folders = true` files its documents into a month/year
subfolder (`Invoices/2026-07/…`). The month comes from the date printed on the
document (parsed in French and English), or the file's modification date when
none is readable.

Add `vendor_rename = true` (invoices) to also rename the file to
`<vendor> facture <date>`, e.g. `spotify facture 2026-07-07.pdf`. The vendor is
matched against the `vendors` list first, then guessed from the filename; when
nothing is certain the original name is kept. Renaming never overwrites and
`cubby undo` still restores the original path.

### The cascade

For each file, the first stage to match wins:

- **stage 0 - strong extension**: if any category sets `strong_ext = true` and
  owns the file's extension, it wins immediately. Use it for unambiguous types
  (`dmg`, `png`, `mp4`): a disk image is an installer whatever it is named.
- **stage 1 - filename**: `name_patterns` are matched against the whole name.
  Skipped for cryptic UUID / long-digit names that carry no signal.
- **stage 2 - content**: for parsable files, `content_patterns` are matched
  against extracted text. Only runs when stages 0-1 found nothing.
- **stage 3 - type**: the file's extension is matched against `extensions`.
- otherwise the file goes to `unsorted_dir`.

### Ordering

Within a stage, categories are tried **top to bottom**, so:

- put specific categories first,
- put a broad catch-all (e.g. `Documents` owning `pdf`, `docx`, ...) last.

### Patterns

Patterns are Python regular expressions, matched case-insensitively. In TOML
basic strings, escape backslashes: write `"\\binvoice\\b"` for the word-boundary
regex `\binvoice\b`.

## Tips

- Run `cubby plan` after every change: it shows the full mapping and annotates
  which stage decided each file (`<- content`, `<- type`).
- Keep personal keywords (names, account hints) in your user file, never in a
  shared/committed config.
