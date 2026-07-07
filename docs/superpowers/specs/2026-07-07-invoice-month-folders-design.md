# Monthly foldering and vendor renaming for invoices

Date: 2026-07-07

## Problem

As a freelancer, Olivier wants every downloaded invoice to land in a
month/year subfolder so he can declare taxes month by month, and to be renamed
so the vendor and date are obvious at a glance. Invoices arrive in **French and
English** and both must work.

Today cubby files an invoice into a flat `Invoices/` (in Olivier's personal
config: `Factures-et-Recus/`) with its original, often cryptic, filename.

## Desired behaviour

For invoice and bank categories, a sorted file goes into a **month/year
subfolder** derived from the invoice's own date:

```
Factures-et-Recus/
  2026-07/
    spotify facture 2026-07-07.pdf
  2026-06/
    ovh facture 2026-06-30.pdf
```

- **Date source**: the issue date read from the file's text; if unreadable, the
  file's modification time.
- **Folder name**: default numeric `AAAA-MM` (`2026-07`). A CLI switch offers a
  letters mode: French `juillet 2026` (default language) or English
  `July 2026`.
- **Rename** (invoices only): `<vendor> facture <ISO date>.<ext>`, e.g.
  `spotify facture 2026-07-07.pdf`. Vendor is lowercased.
- **Scope**: month foldering applies to invoices **and** bank statements;
  vendor renaming applies to invoices only.

## Key constraint: no hardcoded category names

Olivier's personal config renames categories to French (`Factures-et-Recus`,
`Finance`), while the shipped default uses English (`Invoices`,
`Bank-Statements`). The feature therefore keys off **per-category flags**, not
category names:

- `date_folders = true` -> this category gets month/year subfolders.
- `vendor_rename = true` -> this category also gets vendor renaming.

Defaults (`default.toml`): `Invoices` gets both, `Bank-Statements` gets
`date_folders`. Olivier's config: `Factures-et-Recus` gets both, `Finance` gets
`date_folders`.

## Architecture

Chosen approach: a new **pure domain module** plus a single placement step in
the `Sorter`. The `Engine` keeps doing only routing (which category); a
separate, fully testable function computes the subfolder and new name. This
keeps the cascade untouched and the new logic isolated and unit-testable.

### `domain/invoices.py` (pure, no IO)

- `parse_invoice_date(text: str, fallback: date) -> date`
  Scans extracted text for a date, preferring matches near a label
  (`date`, `facture du`, `date d'émission`, `invoice date`, `issued`). Formats:
  ISO `YYYY-MM-DD`, `DD/MM/YYYY`, `DD.MM.YYYY`, `DD-MM-YYYY`,
  `D MMMM YYYY` (FR + EN month names), `MMMM D, YYYY`. Rejects implausible
  years. Returns `fallback` when nothing plausible is found.
- `detect_vendor(name: str, text: str, known: Sequence[str]) -> str | None`
  1. Match `known` vendor patterns against filename + text (first wins).
  2. Else heuristic on the filename stem: split on separators, drop invoice
     words (`invoice/facture/receipt/reçu/recu/order/commande/payment/
     confirmation/bill/devis/no/num/n°`), pure numbers, date-like tokens, and
     UUID/hex; take the leading remaining token.
  3. Else `None`.
- `month_folder(d: date, style: str, lang: str) -> str`
  `numeric` -> `2026-07`; `letters`+`fr` -> `juillet 2026`; `letters`+`en` ->
  `July 2026`.
- `invoice_filename(vendor: str, d: date, ext: str) -> str`
  `f"{vendor} facture {d.isoformat()}.{ext}"` (ext without a leading dot).
- `Placement` dataclass: `subdir: str` (may be `""`), `new_name: str | None`.
- `plan_placement(ref, category_flags, settings_view, fallback_date) -> Placement`
  Orchestrates the above. **Safety**: when the vendor is unknown, `new_name`
  stays `None` (keep the original filename) but the file is still filed into
  the month/year subfolder.

### `domain/category.py`

- `Category` gains `date_folders: bool = False`, `vendor_rename: bool = False`.
- `Settings` gains `month_style: str = "numeric"`, `month_lang: str = "fr"`,
  `vendors: tuple[str, ...] = ()`.

### `adapters/config.py`

Parse the two new category flags and the three new settings keys.

### `adapters/filesystem.py`

`move_into(..., rename_to: str | None = None)`: when set, the destination
filename is `rename_to`, still collision-safe via `unique_destination`.

### `app/sorter.py`

After `classify`, if the category has `date_folders`:
compute `fallback_date` from the path's mtime, build the extracted text via the
existing `ref.text()` (respecting `content_scan`), call `plan_placement`, then
file into `source / category / placement.subdir` passing `placement.new_name`
to `move_into`. Otherwise unchanged. The plan renderer shows the subfolder and
new name so `cubby plan` previews everything with zero risk.

### `cli.py`

`--month-style {numeric,letters}` and `--month-lang {fr,en}` on
`plan/run/watch/install`, wired through `_build_overrides` and baked into the
agent via `_program_args`.

## Safety

- Renaming never loses data: original name kept when the vendor is uncertain;
  `unique_destination` prevents any overwrite.
- `undo` is unaffected: the journal stores the original source path, so restore
  works after renaming and subfoldering.
- With `--no-content`: date falls back to mtime and vendor detection uses the
  filename only.
- Already-sorted files are safe: `iter_candidates` only scans top-level entries
  and skips managed category folders, so `Factures-et-Recus/2026-07/...` is
  never re-picked.

## Testing

- Unit tests for `invoices.py`: FR/EN/ISO date parsing (labelled and bare),
  known and heuristic vendor detection, the three folder formats, filename
  building, and the unknown-vendor safety path.
- Integration test on `Sorter`: an invoice fixture lands in
  `Factures/2026-07/spotify facture 2026-07-07.pdf`.
- `make` (ruff + mypy + pytest) must pass.

## Delivery

- Update `default.toml`, README and docs; commit and push to
  `DeharengOlivier/cubby`.
- Set the flags in `~/.config/cubby/config.toml` (`Factures-et-Recus`:
  both flags; `Finance`: `date_folders`).
- Reinstall cubby, restart the `com.cubby.agent` launchd agent, verify with
  `cubby plan`.
