"""Linux systemd backend: a per-user service that runs ``cubby watch``."""

from __future__ import annotations

import re
from pathlib import Path

from .. import state
from .base import (
    DEFAULT_LABEL,
    STOP_TIMEOUT,
    STOPPING_TIMEOUT,
    Service,
    ServiceError,
    ServiceSpec,
    require_success,
    run_manager,
    wait_until,
)

_UNIT_DIR = Path.home() / ".config" / "systemd" / "user"

#: How long an installed agent has to reach "active".
_START_TIMEOUT = 10.0

_UNIT_TEMPLATE = """\
[Unit]
Description=Cubby - sort the Downloads folder
After=default.target

[Service]
Type=simple
{environment}ExecStart={exec_start}
Restart=on-failure
RestartSec=5
TimeoutStopSec={stop_timeout}
StandardOutput=append:{log}
StandardError=append:{log}

[Install]
WantedBy=default.target
"""


def quote_argument(arg: str) -> str:
    """One argument as systemd's ``ExecStart=`` reads it back, unchanged.

    systemd splits ``ExecStart=`` on whitespace, honors double quotes and C-style
    backslash escapes, expands ``%`` specifiers and ``$VARIABLES``. So every
    argument is double-quoted with ``\\`` and ``"`` escaped, a newline written
    as ``\\n``, and ``%`` and ``$`` doubled, which is how systemd spells them
    literally.
    """
    escaped = (
        arg.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")  # a raw newline would end the ExecStart= line
        .replace("%", "%%")
        .replace("$", "$$")
    )
    return f'"{escaped}"'


#: One argument as :func:`quote_argument` writes it, and what follows it.
_QUOTED_ARGUMENT = re.compile(r'"((?:[^"\\]|\\.)*)"(?:\s+|$)', re.DOTALL)


def unit_program_args(unit: str) -> list[str] | None:
    """The ``ExecStart=`` of a unit cubby wrote, read back; None if it is not one."""
    line = next((ln for ln in unit.splitlines() if ln.startswith("ExecStart=")), None)
    if line is None:
        return None
    rest = line.removeprefix("ExecStart=")
    args: list[str] = []
    while rest:
        match = _QUOTED_ARGUMENT.match(rest)
        if match is None:
            return None
        escaped = match.group(1).replace("%%", "%").replace("$$", "$")
        args.append(re.sub(r"\\(.)", lambda m: "\n" if m[1] == "n" else m[1], escaped))
        rest = rest[match.end() :]
    return args or None


def environment_line(key: str, value: str) -> str:
    """``Environment="KEY=value"``, read back by systemd as exactly that.

    In ``Environment=`` specifiers (``%``) are expanded and C escapes apply
    inside quotes, but ``$`` has no special meaning.
    """
    assignment = f"{key}={value}"
    escaped = (
        assignment.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("%", "%%")
    )
    return f'Environment="{escaped}"\n'


def _unit_path_value(path: Path) -> str:
    """A path for ``StandardOutput=append:``, where specifiers still apply."""
    return str(path).replace("%", "%%")


class SystemdService(Service):
    name = "systemd"

    def _unit_name(self, label: str) -> str:
        # "com.cubby.agent" -> "cubby.service"; keep it tidy for systemctl.
        stem = label.rsplit(".", 1)[-1]
        return f"{'cubby' if stem == 'agent' else stem}.service"

    def unit_path(self, label: str) -> Path:
        return _UNIT_DIR / self._unit_name(label)

    def is_running(self, label: str = DEFAULT_LABEL) -> bool:
        try:
            result = run_manager(["systemctl", "--user", "is-active", self._unit_name(label)])
        except ServiceError:
            return False
        return result.returncode == 0 and result.stdout.strip() == "active"

    def program_args(self, label: str = DEFAULT_LABEL) -> list[str] | None:
        try:
            return unit_program_args(self.unit_path(label).read_text("utf-8"))
        except (OSError, UnicodeError):
            return None

    def install(self, spec: ServiceSpec) -> Path:
        _UNIT_DIR.mkdir(parents=True, exist_ok=True)
        state.ensure_parent(spec.log_path)
        path = self.unit_path(spec.label)
        exec_start = " ".join(quote_argument(arg) for arg in spec.program_args)
        path.write_text(
            _UNIT_TEMPLATE.format(
                environment="".join(
                    environment_line(k, v) for k, v in sorted(spec.environment.items())
                ),
                exec_start=exec_start,
                log=_unit_path_value(spec.log_path),
                stop_timeout=STOP_TIMEOUT,
            ),
            encoding="utf-8",
        )

        unit = self._unit_name(spec.label)
        require_success(["systemctl", "--user", "daemon-reload"])
        require_success(["systemctl", "--user", "enable", unit])
        # restart, not start: a reinstall must pick up the new command line.
        require_success(["systemctl", "--user", "restart", unit], timeout=STOPPING_TIMEOUT)
        if not wait_until(lambda: self.is_running(spec.label), _START_TIMEOUT):
            raise ServiceError(
                f"{unit} was installed but is not running; "
                f"see `systemctl --user status {unit}` and {spec.log_path}"
            )
        return path

    def uninstall(self, label: str = DEFAULT_LABEL) -> bool:
        path = self.unit_path(label)
        if not path.exists():
            return False
        unit = self._unit_name(label)
        require_success(["systemctl", "--user", "disable", "--now", unit], timeout=STOPPING_TIMEOUT)
        if self.is_running(label):
            # The unit stays, so `systemctl --user stop` still has one to name.
            raise ServiceError(f"{unit} is still running after 'systemctl --user disable --now'")
        path.unlink()
        require_success(["systemctl", "--user", "daemon-reload"])
        return True
