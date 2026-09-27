"""Pick the right service backend for the current platform."""

from __future__ import annotations

import shutil
import sys

from .base import Service, ServiceError
from .launchd import LaunchdService
from .systemd import SystemdService


def detect_service() -> Service | None:
    """Return the best available backend, or ``None`` if none is usable."""
    if sys.platform == "darwin" and shutil.which("launchctl"):
        return LaunchdService()
    if shutil.which("systemctl"):
        return SystemdService()
    return None


def get_service() -> Service:
    """Like :func:`detect_service` but raises a helpful error when unsupported.

    Raises:
        ServiceError: No supported service manager is available.
    """
    service = detect_service()
    if service is None:
        raise ServiceError(
            "no supported service manager found (need launchd on macOS or "
            "systemd on Linux). Run `cubby watch` manually, e.g. under nohup or tmux."
        )
    return service
