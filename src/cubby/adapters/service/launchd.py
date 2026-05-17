"""macOS launchd backend: a per-user LaunchAgent that runs ``cubby watch``."""
from __future__ import annotations

import plistlib
import subprocess
from pathlib import Path

from .base import Service, ServiceSpec

_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"


class LaunchdService(Service):
    name = "launchd"

    def unit_path(self, label: str) -> Path:
        return _AGENTS_DIR / f"{label}.plist"

    def install(self, spec: ServiceSpec) -> Path:
        _AGENTS_DIR.mkdir(parents=True, exist_ok=True)
        spec.log_path.parent.mkdir(parents=True, exist_ok=True)
        path = self.unit_path(spec.label)

        plist = {
            "Label": spec.label,
            "ProgramArguments": spec.program_args,
            "RunAtLoad": True,
            "KeepAlive": True,
            "ProcessType": "Background",
            "StandardOutPath": str(spec.log_path),
            "StandardErrorPath": str(spec.log_path),
        }
        with path.open("wb") as handle:
            plistlib.dump(plist, handle)

        # Reload so a reinstall picks up changes; ignore "not loaded" on unload.
        subprocess.run(["launchctl", "unload", str(path)], capture_output=True)
        subprocess.run(["launchctl", "load", "-w", str(path)], capture_output=True)
        return path

    def uninstall(self, label: str = "com.cubby.agent") -> bool:
        path = self.unit_path(label)
        if not path.exists():
            return False
        subprocess.run(["launchctl", "unload", str(path)], capture_output=True)
        path.unlink()
        return True
