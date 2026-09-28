"""Command-line entry point. Thin: it parses args and wires app + adapters."""

from .common import EXIT_BAD_CONFIG, EXIT_FAILED, EXIT_OK
from .main import build_parser, main

__all__ = ["EXIT_BAD_CONFIG", "EXIT_FAILED", "EXIT_OK", "build_parser", "main"]
