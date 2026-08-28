"""Filesystem adapter: candidate discovery, eligibility and safe moves."""

from __future__ import annotations

import hashlib
import shutil
import time
from collections.abc import Iterator
from pathlib import Path

from ..domain.category import Settings
from ..domain.file_ref import FileRef
from ..domain.naming import safe_component
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


def resolve_inside(root: Path, destination: Path) -> Path:
    """Return ``destination`` resolved, or raise if it escapes ``root``.

    The last barrier before a write: every caller has already been checked, and
    this is here for the one that has not been thought of yet. Both sides are
    resolved so a symlinked root (``/tmp`` on macOS) compares correctly.

    Raises:
        ValueError: ``destination`` is not inside ``root``.
    """
    resolved_root = Path(root).resolve()
    resolved = Path(destination).resolve()
    if resolved != resolved_root and not resolved.is_relative_to(resolved_root):
        raise ValueError(
            f"refusing to write outside the watched folder: {destination} "
            f"is not inside {root}."
        )
    return resolved


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


def _digest(path: Path) -> str | None:
    try:
        h = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def files_identical(a: Path, b: Path) -> bool:
    """True if both are regular files with the same size and content."""
    try:
        if not (a.is_file() and b.is_file()) or a.stat().st_size != b.stat().st_size:
            return False
    except OSError:
        return False
    return _digest(a) == _digest(b)


def move_into(
    path: Path,
    category_dir: Path,
    *,
    root: Path,
    dedupe: bool = False,
    rename_to: str | None = None,
) -> Path:
    """Move ``path`` into ``category_dir``, never overwriting. Returns the dest.

    ``root`` is the folder cubby manages; the destination is refused if it falls
    outside it, and nothing is moved or created in that case. ``rename_to`` sets
    the destination filename (e.g. a cleaned invoice name) and must be a single
    name, not a path; it defaults to the source's own name. With ``dedupe`` and a
    byte-identical file already present under the same name, the redundant
    ``path`` is removed instead of being kept as a `` (1)`` copy, and the
    existing file's path is returned.

    Raises:
        ValueError: The destination or the new name would escape ``root``.
    """
    target_name = safe_component(rename_to, field="rename_to") if rename_to else path.name
    # Check before creating anything: a refused move must leave no trace.
    resolve_inside(root, category_dir)
    category_dir.mkdir(parents=True, exist_ok=True)
    same_name = category_dir / target_name
    if dedupe and same_name.exists() and files_identical(path, same_name):
        path.unlink()
        return same_name
    destination = unique_destination(category_dir, target_name)
    shutil.move(str(path), str(destination))
    return destination
