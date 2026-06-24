"""Result types and human-readable rendering for a sort run."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ..adapters.ui import Palette
from ..domain.file_ref import Stage


@dataclass(frozen=True)
class SortOutcome:
    """What happened (or would happen) to a single entry."""

    source: Path
    category: str
    stage: Stage
    moved_to: Path | None = None  # set when actually moved

    @property
    def name(self) -> str:
        return self.source.name


def group_by_category(outcomes: list[SortOutcome]) -> dict[str, list[SortOutcome]]:
    grouped: dict[str, list[SortOutcome]] = {}
    for outcome in outcomes:
        grouped.setdefault(outcome.category, []).append(outcome)
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
    lines: list[str] = []
    for category in sorted(grouped):
        items = grouped[category]
        header = p.bold(p.accent(f"{category}/")) + p.dim(f"  ({len(items)})")
        lines.append(f"\n{header}")
        for outcome in sorted(items, key=lambda o: o.name.lower()):
            tag = "" if outcome.stage is Stage.NAME else p.dim(f"   <- {outcome.stage.value}")
            lines.append(f"    {outcome.name}{tag}")

    verb = "Moved" if applied else "Would move"
    summary = f"{verb} {len(outcomes)} item(s)."
    lines.append("\n" + (p.green(summary) if applied else p.bold(summary)))
    return "\n".join(lines).lstrip("\n")


def render_json(outcomes: list[SortOutcome], *, applied: bool) -> str:
    """Render outcomes as JSON, for scripting and integration."""
    payload = {
        "applied": applied,
        "count": len(outcomes),
        "items": [
            {
                "name": o.name,
                "source": str(o.source),
                "category": o.category,
                "stage": o.stage.value,
                "moved_to": str(o.moved_to) if o.moved_to else None,
            }
            for o in outcomes
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)
