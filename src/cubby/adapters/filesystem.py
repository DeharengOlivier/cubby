"""Filesystem adapter: candidate discovery, eligibility and safe moves."""

from __future__ import annotations

import errno
import fnmatch
import hashlib
import os
import shutil
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ..domain.category import Settings
from ..domain.duration import format_duration
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


def ignored_by(name: str, patterns: tuple[str, ...]) -> str | None:
    """The first ``ignore`` pattern matching ``name`` (case-insensitive), if any."""
    lowered = name.lower()
    return next((p for p in patterns if fnmatch.fnmatchcase(lowered, p.lower())), None)


def candidate_skip_reason(path: Path, settings: Settings, managed: frozenset[str]) -> str | None:
    """Why cubby never considers ``path`` for sorting, or None if it does.

    Hidden entries, the ``_Unsorted`` folder and every managed category folder
    are skipped so a sorted file is never picked up again on the next run;
    names matching an ``ignore`` pattern are the user's to keep.
    """
    if path.name.startswith("."):
        return "hidden file"
    if path.name in managed or path.name == settings.unsorted_dir:
        return "a folder cubby files into"
    if pattern := ignored_by(path.name, settings.ignore):
        return f"ignored by pattern {pattern!r}"
    return None


def iter_candidates(settings: Settings, managed: frozenset[str] = frozenset()) -> Iterator[Path]:
    """Yield top-level entries in the source folder that cubby may sort."""
    source = settings.source
    if not source.is_dir():
        return
    for entry in sorted(source.iterdir()):
        if candidate_skip_reason(entry, settings, managed) is None:
            yield entry


def not_yet_reason(path: Path, settings: Settings, now: float | None = None) -> str | None:
    """Why ``path`` is not settled enough to move yet, or None if it is.

    A file is settled when it is not an in-progress download and its most
    recent change is older than ``settings.delay`` (so a file still being
    written is left alone).
    """
    ext = path.suffix.lower().lstrip(".")
    if ext in settings.skip_ext:
        return f"download in progress (.{ext})"
    try:
        mtime = path.stat().st_mtime
    except OSError as exc:
        return f"cannot be read ({exc.strerror or exc})"
    now = time.time() if now is None else now
    if (now - mtime) < settings.delay:
        return f"too recent: moves once it is {format_duration(settings.delay)} old"
    return None


def is_eligible(path: Path, settings: Settings, now: float | None = None) -> bool:
    """True if ``path`` is settled enough to move (see :func:`not_yet_reason`)."""
    return not_yet_reason(path, settings, now) is None


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
            f"refusing to write outside the watched folder: {destination} is not inside {root}."
        )
    return resolved


def unique_destination(dest_dir: Path, name: str) -> Path:
    """Pick a non-clobbering path inside ``dest_dir`` for ``name``.

    If ``name`` already exists, insert `` (1)``, `` (2)`` ... before the suffix.
    """
    candidate = dest_dir / name
    if not _taken(candidate):
        return candidate
    stem = candidate.stem
    suffix = candidate.suffix
    counter = 1
    while True:
        candidate = dest_dir / f"{stem} ({counter}){suffix}"
        if not _taken(candidate):
            return candidate
        counter += 1


def _taken(path: Path) -> bool:
    """True if ``path`` names anything, a dangling symlink included."""
    return path.exists() or path.is_symlink()


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


@dataclass(frozen=True)
class Moved:
    """What :func:`move_into` did: moved ``path`` to ``destination``, or, for
    ``dedupe``, deleted it because ``destination`` already held the same bytes."""

    destination: Path
    op: Literal["move", "dedupe"]


#: How many times a name may be taken under us before the move gives up.
_MAX_NAME_RACES = 100

#: Errors after which a hard link cannot be the way to move a file.
_NO_LINK = frozenset({errno.EXDEV, errno.EPERM, errno.EMLINK, errno.ENOTSUP, errno.EOPNOTSUPP})


def move_no_clobber(source: Path, destination: Path) -> None:
    """Move ``source`` to ``destination``, failing rather than replacing a file.

    ``os.rename`` silently replaces an existing destination on POSIX, so a file
    that appeared there between choosing the name and moving would be lost. For
    a regular file on the same filesystem, a hard link is created first: that
    fails atomically if the name is taken, and only then is the source removed.
    Elsewhere (a folder, another filesystem) the destination is checked just
    before a copying move; the window is small and cubby holds its lock.

    Raises:
        FileExistsError: ``destination`` already exists.
        OSError: The move failed; ``source`` is still in place.
    """
    if source.is_file() and not source.is_symlink():
        try:
            os.link(source, destination)
        except OSError as exc:
            if exc.errno not in _NO_LINK:
                raise
        else:
            source.unlink()
            return
    if _taken(destination):
        raise FileExistsError(errno.EEXIST, "destination exists", str(destination))
    shutil.move(str(source), str(destination))


def move_into(
    path: Path,
    category_dir: Path,
    *,
    root: Path,
    dedupe: bool = False,
    rename_to: str | None = None,
) -> Moved:
    """Move ``path`` into ``category_dir``, never overwriting.

    ``root`` is the folder cubby manages; the destination is refused if it falls
    outside it, and nothing is moved or created in that case. ``rename_to`` sets
    the destination filename (e.g. a cleaned invoice name) and must be a single
    name, not a path; it defaults to the source's own name. With ``dedupe`` and a
    byte-identical file already present under the same name, the redundant
    ``path`` is removed instead of being kept as a `` (1)`` copy.

    Raises:
        ValueError: The destination or the new name would escape ``root``.
        OSError: The file could not be moved; it is still in place.
    """
    target_name = safe_component(rename_to, field="rename_to") if rename_to else path.name
    # Check before creating anything: a refused move must leave no trace.
    resolve_inside(root, category_dir)
    category_dir.mkdir(parents=True, exist_ok=True)
    same_name = category_dir / target_name
    if dedupe and same_name.exists() and files_identical(path, same_name):
        path.unlink()
        return Moved(same_name, "dedupe")
    for _ in range(_MAX_NAME_RACES):
        destination = unique_destination(category_dir, target_name)
        try:
            move_no_clobber(path, destination)
        except FileExistsError:
            continue  # taken since we looked: pick the next free name
        return Moved(destination, "move")
    raise FileExistsError(
        errno.EEXIST, "every candidate name was taken while moving", str(category_dir)
    )
