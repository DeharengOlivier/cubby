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


def group_by_category(outcomes: list[SortOutcome]) -> dict[str, list[SortOutcome]]:
    grouped: dict[str, list[SortOutcome]] = {}
    for outcome in outcomes:
        if outcome.error is None:
            grouped.setdefault(outcome.dest, []).append(outcome)
    return grouped


def render_plan(
    outcomes: list[SortOutcome], *, applied: bool, palette: Palette | None = None
) -> str:
    """Render outcomes grouped by destination folder.

    Non-name stages are annotated (``<- content``) so it is obvious why a file
    with an unhelpful name landed where it did. With ``palette`` the output is
    coloured; without it (the default) the plain layout is unchanged.
    """
    p = palette or Palette(False)
    if not outcomes:
        return p.dim("Nothing to sort.")

    grouped = group_by_category(outcomes)
    failures = [o for o in outcomes if o.error is not None]
    lines: list[str] = []
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

    unjournaled = [o for o in outcomes if not o.journaled]
    if unjournaled:
        lines.append("\n" + p.bold(p.yellow(f"Moved but cannot be undone  ({len(unjournaled)})")))
        lines.extend(f"    {o.display_name}" for o in unjournaled)
    if failures:
        lines.append("\n" + p.bold(p.yellow(f"Could not sort  ({len(failures)})")))
        lines.extend(f"    {o.name}   {p.dim(o.error or '')}" for o in failures)

    verb = "Moved" if applied else "Would move"
    summary = f"{verb} {len(outcomes) - len(failures)} item(s)."
    if failures:
        summary += f" {len(failures)} could not be sorted."
    lines.append("\n" + (p.green(summary) if applied and not failures else p.bold(summary)))
    return "\n".join(lines).lstrip("\n")


def render_json(outcomes: list[SortOutcome], *, applied: bool) -> str:
    """Render outcomes as JSON, for scripting and integration."""
    payload = {
        "version": JSON_VERSION,
        "applied": applied,
        "count": len(outcomes),
        "failed": sum(1 for o in outcomes if o.error is not None),
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
            }
            for o in outcomes
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
