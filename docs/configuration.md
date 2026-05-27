# Configuration

Cubby reads TOML from three layers, later winning:

1. the packaged `config/default.toml` (generic categories)
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
skip_ext = ["crdownload", "part"]  # in-progress download extensions to ignore
```

## Categories

```toml
[[category]]
name = "Invoices"                       # destination folder name
name_patterns = ["invoice", "facture"]  # stage 1: regex on the filename
content_patterns = ["amount due"]       # stage 2: regex on extracted text
extensions = ["pdf"]                    # stage 3: fallback by extension
strong_ext = false                      # stage 0: make extensions decisive
```

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
