"""macOS launchd backend: a per-user LaunchAgent that runs ``cubby watch``."""

from __future__ import annotations

import plistlib
from pathlib import Path

from .. import state
from .base import (
    DEFAULT_LABEL,
    Service,
    ServiceError,
    ServiceSpec,
    require_success,
    run_manager,
    wait_until,
)

_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"

#: How long a loaded agent has to show up as running.
_START_TIMEOUT = 10.0


class LaunchdService(Service):
    name = "launchd"

    def unit_path(self, label: str) -> Path:
        return _AGENTS_DIR / f"{label}.plist"

    def is_running(self, label: str = DEFAULT_LABEL) -> bool:
        try:
            result = run_manager(["launchctl", "list", label])
        except ServiceError:
            return False
        return result.returncode == 0 and '"PID"' in result.stdout

    def install(self, spec: ServiceSpec) -> Path:
        _AGENTS_DIR.mkdir(parents=True, exist_ok=True)
        state.ensure_parent(spec.log_path)
        path = self.unit_path(spec.label)

        # launchd passes ProgramArguments to execve as a list: no quoting layer.
        plist: dict[str, object] = {
            "Label": spec.label,
            "ProgramArguments": spec.program_args,
            "RunAtLoad": True,
            "KeepAlive": True,
            "ProcessType": "Background",
            "StandardOutPath": str(spec.log_path),
            "StandardErrorPath": str(spec.log_path),
        }
        if spec.environment:
            plist["EnvironmentVariables"] = dict(spec.environment)
        with path.open("wb") as handle:
            plistlib.dump(plist, handle)

        # Unload first so a reinstall picks up changes. Failing to unload an
        # agent that was not loaded is expected and not an error.
        run_manager(["launchctl", "unload", str(path)])
        require_success(["launchctl", "load", "-w", str(path)])
        # `launchctl load` exits 0 on many failures; believe `list`, not `load`.
        if not wait_until(lambda: self.is_running(spec.label), _START_TIMEOUT):
            raise ServiceError(
                f"{spec.label} was loaded but is not running; "
                f"see `launchctl list {spec.label}` and {spec.log_path}"
            )
        return path

    def uninstall(self, label: str = DEFAULT_LABEL) -> bool:
        path = self.unit_path(label)
        if not path.exists():
            return False
        # An agent that is installed but not loaded unloads with an error;
        # the file is still ours to remove.
        run_manager(["launchctl", "unload", "-w", str(path)])
        path.unlink()
        if self.is_running(label):
            raise ServiceError(f"{label} is still running after unload")
        return True
