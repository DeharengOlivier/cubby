"""Shared contract for background-service backends."""

from __future__ import annotations

import subprocess
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .. import state

DEFAULT_LABEL = "com.cubby.agent"

#: Every call to launchctl or systemctl is bounded: a hung service manager
#: must not hang `cubby install`.
COMMAND_TIMEOUT = 30.0


class ServiceError(RuntimeError):
    """The service manager refused, failed, or the agent did not start."""


@dataclass(frozen=True)
class ServiceSpec:
    """Everything a backend needs to register a long-running agent."""

    program_args: list[str]  # e.g. ["/usr/local/bin/cubby", "watch"]
    label: str = DEFAULT_LABEL  # reverse-dns id (launchd) / unit name stem
    log_path: Path = field(default_factory=state.log_path)
    # Environment the agent needs to find the same state and config as the CLI.
    environment: dict[str, str] = field(default_factory=dict)


def run_manager(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a service-manager command with a timeout; the caller judges the result.

    Raises:
        ServiceError: The command could not be started or timed out.
    """
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=COMMAND_TIMEOUT, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ServiceError(f"{' '.join(cmd)} failed: {exc}") from exc


def require_success(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    """Run ``cmd`` and raise with its own words if it fails.

    Raises:
        ServiceError: Non-zero exit, timeout, or the command is missing.
    """
    result = run_manager(cmd)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip() or f"exit code {result.returncode}"
        raise ServiceError(f"{' '.join(cmd)} failed: {detail}")
    return result


def wait_until(check: Callable[[], bool], timeout: float, poll: float = 0.2) -> bool:
    """Poll ``check`` until it is true or ``timeout`` seconds have passed."""
    deadline = time.monotonic() + timeout
    while True:
        if check():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(poll)


class Service(ABC):
    """A background-service backend (launchd, systemd, ...)."""

    name: str

    @abstractmethod
    def install(self, spec: ServiceSpec) -> Path:
        """Write the unit, load it, verify the agent runs, and return the unit's path.

        Raises:
            ServiceError: The manager refused, or the agent is not running afterwards.
        """

    @abstractmethod
    def uninstall(self, label: str = DEFAULT_LABEL) -> bool:
        """Stop and remove the agent. Returns False if nothing was installed.

        Raises:
            ServiceError: The manager could not stop it.
        """

    @abstractmethod
    def unit_path(self, label: str) -> Path:
        """Where the unit/agent file lives for ``label``."""

    @abstractmethod
    def is_running(self, label: str = DEFAULT_LABEL) -> bool:
        """What the service manager says, not what the unit file suggests."""

    def is_installed(self, label: str = DEFAULT_LABEL) -> bool:
        return self.unit_path(label).exists()
