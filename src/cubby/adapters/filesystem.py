"""Filesystem adapter: candidate discovery, eligibility and safe moves."""

from __future__ import annotations

import shutil
import time
from collections.abc import Iterator
from pathlib import Path

from ..domain.category import Settings
from ..domain.file_ref import FileRef
from .extraction import extract_text


def build_ref(path: Path, max_bytes: int = 4000) -> FileRef:
    """Wrap a real path as a FileRef, wiring extraction as the read_text port."""
    ext = path.suffix.lower().lstrip(".")
    is_file = path.is_file()
    return FileRef(
        name=path.name,
        stem=path.stem,
        ext=ext,
        is_file=is_file,
        read_text=lambda: extract_text(path, ext, max_bytes) if is_file else "",
    )


def iter_candidates(settings: Settings, managed: frozenset[str] = frozenset()) -> Iterator[Path]:
    """Yield top-level entries in the source folder that cubby may sort.

    Hidden entries, the ``_Unsorted`` folder and every managed category folder
    are skipped so a sorted file is never picked up again on the next run.
    """
    source = settings.source
    if not source.is_dir():
        return
    skip = set(managed) | {settings.unsorted_dir}
    for entry in sorted(source.iterdir()):
        if entry.name.startswith("."):
            continue
        if entry.name in skip:
            continue
        yield entry


def is_eligible(path: Path, settings: Settings, now: float | None = None) -> bool:
    """True if ``path`` is settled enough to move.

    A file is eligible when it is not an in-progress download and its most
    recent change is older than ``settings.delay`` (so a file still being
    written is left alone).
    """
    ext = path.suffix.lower().lstrip(".")
    if ext in settings.skip_ext:
        return False
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return False
    now = time.time() if now is None else now
    return (now - mtime) >= settings.delay


def unique_destination(dest_dir: Path, name: str) -> Path:
    """Pick a non-clobbering path inside ``dest_dir`` for ``name``.

    If ``name`` already exists, insert `` (1)``, `` (2)`` ... before the suffix.
    """
    candidate = dest_dir / name
    if not candidate.exists():
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    counter = 1
    while True:
        candidate = dest_dir / f"{stem} ({counter}){suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def move_into(path: Path, category_dir: Path) -> Path:
    """Move ``path`` into ``category_dir``, never overwriting. Returns the dest."""
    category_dir.mkdir(parents=True, exist_ok=True)
    destination = unique_destination(category_dir, path.name)
    shutil.move(str(path), str(destination))
    return destination
