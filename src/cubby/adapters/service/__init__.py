"""OS service adapters that keep ``cubby watch`` running in the background."""

from .base import Service, ServiceSpec
from .factory import detect_service, get_service

__all__ = ["Service", "ServiceSpec", "detect_service", "get_service"]
