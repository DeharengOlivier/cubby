"""Linux systemd backend: a per-user service that runs ``cubby watch``."""

from __future__ import annotations

import subprocess
from pathlib import Path

from .base import Service, ServiceSpec

_UNIT_DIR = Path.home() / ".config" / "systemd" / "user"

_UNIT_TEMPLATE = """\
[Unit]
Description=Cubby - sort the Downloads folder
After=default.target

[Service]
Type=simple
ExecStart={exec_start}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
"""


class SystemdService(Service):
    name = "systemd"

    def _unit_name(self, label: str) -> str:
        # "com.cubby.agent" -> "cubby.service"; keep it tidy for systemctl.
        stem = label.split(".")[-1] if "." in label else label
        return f"{'cubby' if stem == 'agent' else stem}.service"

    def unit_path(self, label: str) -> Path:
        return _UNIT_DIR / self._unit_name(label)

    def install(self, spec: ServiceSpec) -> Path:
        _UNIT_DIR.mkdir(parents=True, exist_ok=True)
        path = self.unit_path(spec.label)
        exec_start = " ".join(spec.program_args)
        path.write_text(_UNIT_TEMPLATE.format(exec_start=exec_start))

        unit = self._unit_name(spec.label)
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
        subprocess.run(["systemctl", "--user", "enable", "--now", unit], capture_output=True)
        return path

    def uninstall(self, label: str = "com.cubby.agent") -> bool:
        path = self.unit_path(label)
        if not path.exists():
            return False
        unit = self._unit_name(label)
        subprocess.run(["systemctl", "--user", "disable", "--now", unit], capture_output=True)
        path.unlink()
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True)
        return True
