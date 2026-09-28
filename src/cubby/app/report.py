"""Result types and human-readable rendering for a sort run."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

from ..adapters.ui import Palette
from ..domain.file_ref import Stage

#: Version of the ``--json`` document. Bumped when a field changes meaning or
#: disappears; adding a field does not bump it.
JSON_VERSION = 1


@dataclass(frozen=True)
class SortOutcome:
    """What happened (or would happen) to a single entry."""

    source: Path
    category: str  # empty when the entry could not be sorted
    stage: Stage | None
    rule: str | None = None  # the rule that decided, in words
    moved_to: Path | None = None  # set when actually moved
    subdir: str = ""  # month/year subfolder inside the category, when any
    renamed_to: str | None = None  # new filename when the entry is renamed
    error: str | None = None  # why the entry could not be sorted
    journaled: bool = True  # False when the move happened but could not be journaled
    # The byte-identical file already filed: ``dedupe`` deletes (or, in a plan,
    # would delete) this entry instead of moving it. None for a move.
    duplicate_of: Path | None = None

    @classmethod
    def failed(cls, source: Path, error: str) -> SortOutcome:
        return cls(source=source, category="", stage=None, error=error)

    def moved(self, destination: Path, *, journaled: bool = True) -> SortOutcome:
        return replace(self, moved_to=destination, journaled=journaled)

    @property
    def needs_attention(self) -> bool:
        """Not sorted, or sorted with no way back."""
        return self.error is not None or not self.journaled

    @property
    def name(self) -> str:
        return self.source.name

    @property
    def dest(self) -> str:
        """The destination folder, category plus month subfolder when present."""
        return f"{self.category}/{self.subdir}" if self.subdir else self.category

    @property
    def display_name(self) -> str:
        """The name the entry ends up with (renamed when applicable)."""
        return self.renamed_to or self.name


#: A file left for later, or for good, and why (``Sorter.sort_once(on_waiting=...)``).
LeftAlone = tuple[str, str]

#: How many left-alone files are named before the rest is counted.
_LEFT_ALONE_SHOWN = 10


def group_by_category(outcomes: list[SortOutcome]) -> dict[str, list[SortOutcome]]:
    grouped: dict[str, list[SortOutcome]] = {}
    for outcome in outcomes:
        if outcome.error is None and outcome.duplicate_of is None:
            grouped.setdefault(outcome.dest, []).append(outcome)
    return grouped


def _near(path: Path, folder: Path) -> str:
    try:
        return path.relative_to(folder).as_posix()
    except ValueError:
        return str(path)


def render_plan(
    outcomes: list[SortOutcome],
    *,
    applied: bool,
    palette: Palette | None = None,
    left_alone: list[LeftAlone] | None = None,
    blocked: list[str] | None = None,
) -> str:
    """Render outcomes grouped by destination folder, then what is not moved.

    Non-name stages are annotated (``<- content``) so it is obvious why a file
    with an unhelpful name landed where it did. Duplicates deleted by
    ``dedupe``, files left alone and entries blocking a category folder each
    get their own section, so a preview shows everything a run would do. With
    ``palette`` the output is coloured; without it the plain layout is used.
    """
    p = palette or Palette(False)
    lines = [
        p.yellow(
            f"{name} is a file where the {name}/ folder goes: files for it cannot be "
            f"sorted; rename or move it."
        )
        for name in blocked or []
    ]
    if not outcomes:
        lines.append(p.dim("Nothing to sort."))
        lines.extend(_left_alone_lines(p, left_alone or []))
        return "\n".join(lines)

    grouped = group_by_category(outcomes)
    failures = [o for o in outcomes if o.error is not None]
    duplicates = [o for o in outcomes if o.error is None and o.duplicate_of is not None]
    for category in sorted(grouped):
        items = grouped[category]
        header = p.bold(p.accent(f"{category}/")) + p.dim(f"  ({len(items)})")
        lines.append(f"\n{header}")
        for outcome in sorted(items, key=lambda o: o.display_name.lower()):
            stage = outcome.stage
            tag = "" if stage is None or stage is Stage.NAME else p.dim(f"   <- {stage.value}")
            if outcome.renamed_to:
                tag += p.dim(f"   (was {outcome.name})")
            lines.append(f"    {outcome.display_name}{tag}")

    if duplicates:
        title = "Deleted as duplicates" if applied else "Would delete as duplicates"
        lines.append("\n" + p.bold(f"{title}  ({len(duplicates)})"))
        lines.extend(
            f"    {o.name}   {p.dim('duplicate of ' + _near(o.duplicate_of, o.source.parent))}"
            for o in duplicates
            if o.duplicate_of is not None
        )
    unjournaled = [o for o in outcomes if not o.journaled]
    if unjournaled:
        lines.append("\n" + p.bold(p.yellow(f"Moved but cannot be undone  ({len(unjournaled)})")))
        lines.extend(f"    {o.display_name}" for o in unjournaled)
    if failures:
        lines.append("\n" + p.bold(p.yellow(f"Could not sort  ({len(failures)})")))
        lines.extend(f"    {o.name}   {p.dim(o.error or '')}" for o in failures)

    lines.extend(_left_alone_lines(p, left_alone or []))

    moved = len(outcomes) - len(failures) - len(duplicates)
    if duplicates:
        verbs = ("Moved", "deleted") if applied else ("Would move", "delete")
        summary = f"{verbs[0]} {moved} item(s) and {verbs[1]} {len(duplicates)} duplicate(s)."
    else:
        summary = f"{'Moved' if applied else 'Would move'} {moved} item(s)."
    if failures:
        summary += f" {len(failures)} could not be sorted."
    lines.append("\n" + (p.green(summary) if applied and not failures else p.bold(summary)))
    return "\n".join(lines).lstrip("\n")


def _left_alone_lines(p: Palette, left_alone: list[LeftAlone]) -> list[str]:
    if not left_alone:
        return []
    lines = ["\n" + p.bold(f"Left alone  ({len(left_alone)})")]
    shown = sorted(left_alone, key=lambda item: item[0].lower())[:_LEFT_ALONE_SHOWN]
    lines.extend(f"    {name}   {p.dim(reason)}" for name, reason in shown)
    if len(left_alone) > _LEFT_ALONE_SHOWN:
        lines.append(p.dim(f"    and {len(left_alone) - _LEFT_ALONE_SHOWN} more"))
    return lines


def render_json(
    outcomes: list[SortOutcome],
    *,
    applied: bool,
    left_alone: list[LeftAlone] | None = None,
    blocked: list[str] | None = None,
) -> str:
    """Render outcomes as JSON, for scripting and integration."""
    payload = {
        "version": JSON_VERSION,
        "applied": applied,
        "count": len(outcomes),
        "failed": sum(1 for o in outcomes if o.error is not None),
        "left_alone": [{"name": name, "reason": reason} for name, reason in left_alone or []],
        "blocked": list(blocked or []),
        "items": [
            {
                "name": o.name,
                "source": str(o.source),
                "category": o.category or None,
                "subdir": o.subdir or None,
                "renamed_to": o.renamed_to,
                "stage": o.stage.value if o.stage else None,
                "rule": o.rule,
                "moved_to": str(o.moved_to) if o.moved_to else None,
                "error": o.error,
                "journaled": o.journaled,
                "duplicate_of": str(o.duplicate_of) if o.duplicate_of else None,
            }
            for o in outcomes
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
