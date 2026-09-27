"""OS service adapters that keep ``cubby watch`` running in the background."""

from .base import DEFAULT_LABEL, Service, ServiceError, ServiceSpec
from .factory import detect_service, get_service

__all__ = [
    "DEFAULT_LABEL",
    "Service",
    "ServiceError",
    "ServiceSpec",
    "detect_service",
    "get_service",
]
