"""Explain use case: where would this file go, which rule decided, or why not."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..adapters.filesystem import build_ref, candidate_skip_reason, duplicate_in, not_yet_reason
from ..domain.category import Config
from ..domain.engine import Engine
from ..domain.file_ref import Stage
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


def explain(path: Path, config: Config) -> Explanation:
    """Classify ``path`` exactly as a run would, and say whether a run would move it.

    Raises:
        FileNotFoundError: ``path`` does not exist.
    """
    if not path.exists() and not path.is_symlink():
        raise FileNotFoundError(2, "no such file", str(path))
    settings = config.settings
    engine = Engine(config)
    ref = build_ref(path, settings.content_max_bytes)
    decision = engine.classify(ref)
    placement = Sorter(config, engine).placement_for(path, ref, decision.category)

    source = settings.source.resolve()
    # The entry itself is not resolved: a run sorts a symlink sitting in the
    # folder, wherever it points, so only the folder it sits in matters.
    outside = path.absolute().parent.resolve() != source
    skipped = candidate_skip_reason(path, settings, config.managed_dirs) or not_yet_reason(
        path, settings
    )
    name = placement.new_name or path.name
    folder = settings.source / decision.category / placement.subdir
    duplicate = duplicate_in(path, folder, name) if settings.dedupe and path.is_file() else None
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
    )
