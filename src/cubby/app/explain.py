"""Explain use case: where would this file go, which rule decided, or why not."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..adapters.filesystem import (
    build_ref,
    candidate_skip_reason,
    file_in_the_way,
    in_the_way,
    not_yet_reason,
)
from ..domain.category import Config
from ..domain.engine import Engine
from ..domain.file_ref import Stage
from .report import SortOutcome
from .sorter import Sorter


@dataclass(frozen=True)
class Explanation:
    """Everything cubby would decide about one file, without moving it."""

    path: Path
    category: str
    stage: Stage
    rule: str | None
    destination: Path  # where a run would put it (before any (1) suffix)
    renamed_to: str | None
    skipped: str | None  # why a run would leave it alone, if it would
    outside: bool  # the file is not in the watched folder
    duplicate_of: Path | None = None  # with dedupe: the filed copy it would be deleted for
    # False when no run ever sorts it (hidden, ignored, a folder cubby files
    # into): then ``destination`` is only what the rules would say.
    sortable: bool = True
    error: str | None = None  # why a run would fail on it (a file where its folder goes)
    # How much text was read from the file when its content was looked at and
    # did not decide (0: nothing readable); None when the content was not read.
    content_chars: int | None = None


class PlannedPass:
    """One plan pass over the watched folder, made on first use and kept.

    ``explain`` needs it to see a duplicate made within the pass; explaining
    many files shares it, so a command makes one pass, not one per file.
    """

    def __init__(self, config: Config, engine: Engine | None = None) -> None:
        self._sorter = Sorter(config, engine)
        self._outcomes: dict[Path, SortOutcome] | None = None

    def outcome(self, entry: Path) -> SortOutcome | None:
        """What the pass does with ``entry`` (a path in the watched folder), if it sees it."""
        if self._outcomes is None:
            self._outcomes = {o.source: o for o in self._sorter.sort_once(apply=False)}
        return self._outcomes.get(entry)


def explain(path: Path, config: Config, planned: PlannedPass | None = None) -> Explanation:
    """Classify ``path`` exactly as a run would, and say whether a run would move it.

    ``planned`` is the plan pass to look duplicates up in; pass the same one
    when explaining several files.

    Raises:
        FileNotFoundError: ``path`` does not exist.
    """
    if not path.exists() and not path.is_symlink():
        raise FileNotFoundError(2, "no such file", str(path))
    settings = config.settings
    engine = Engine(config)
    ref = build_ref(path, settings.content_max_bytes)
    decision = engine.classify(ref)
    content_read = (
        settings.content_scan
        and ref.is_file
        and decision.stage in (Stage.TYPE, Stage.UNSORTED)
    )
    placement = Sorter(config, engine).placement_for(path, ref, decision.category)

    source = settings.source.resolve()
    # The entry itself is not resolved: a run sorts a symlink sitting in the
    # folder, wherever it points, so only the folder it sits in matters.
    outside = path.absolute().parent.resolve() != source
    never = candidate_skip_reason(path, settings, config.managed_dirs)
    skipped = never or not_yet_reason(path, settings)
    name = placement.new_name or path.name
    folder = settings.source / decision.category / placement.subdir
    duplicate = error = None
    if not (skipped or outside):
        if (blocker := file_in_the_way(folder, settings.source)) is not None:
            error = in_the_way(blocker, settings.source)
        elif settings.dedupe:
            planned = planned or PlannedPass(config, engine)
            found = planned.outcome(settings.source / path.name)
            duplicate = found.duplicate_of if found else None
    return Explanation(
        path=path,
        category=decision.category,
        stage=decision.stage,
        rule=decision.rule,
        destination=folder / name,
        renamed_to=placement.new_name,
        skipped=skipped,
        outside=outside,
        duplicate_of=duplicate,
        sortable=never is None,
        error=error,
        content_chars=len(ref.text()) if content_read else None,
    )
