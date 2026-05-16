"""Shared contract for background-service backends."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ServiceSpec:
    """Everything a backend needs to register a long-running agent."""

    program_args: list[str]                 # e.g. ["/usr/local/bin/cubby", "watch"]
    label: str = "com.cubby.agent"          # reverse-dns id (launchd) / unit name stem
    log_path: Path = field(default_factory=lambda: Path.home() / "Library" / "Logs" / "cubby.log")


class Service(ABC):
    """A background-service backend (launchd, systemd, ...)."""

    name: str

    @abstractmethod
    def install(self, spec: ServiceSpec) -> Path:
        """Write the unit/agent file, load it, and return its path."""

    @abstractmethod
    def uninstall(self, label: str = "com.cubby.agent") -> bool:
        """Unload and remove the agent. Returns False if nothing was installed."""

    @abstractmethod
    def unit_path(self, label: str) -> Path:
        """Where the unit/agent file lives for ``label``."""

    def is_installed(self, label: str = "com.cubby.agent") -> bool:
        return self.unit_path(label).exists()
