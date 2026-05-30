"""Human-friendly duration parsing, e.g. ``"1m"`` -> ``60.0`` seconds."""

from __future__ import annotations

import re

_DURATION_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([smhd]?)\s*$", re.IGNORECASE)
_UNIT_SECONDS = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_duration(value: str | int | float) -> float:
    """Convert a duration into seconds.

    Accepts a bare number (already seconds) or a string with an optional unit
    suffix: ``s`` (seconds), ``m`` (minutes), ``h`` (hours), ``d`` (days).

    >>> parse_duration("1m")
    60.0
    >>> parse_duration("30s")
    30.0
    >>> parse_duration(90)
    90.0
    """
    if isinstance(value, bool):  # bool is an int subclass; reject it explicitly
        raise TypeError("duration cannot be a boolean")
    if isinstance(value, (int, float)):
        return float(value)
    match = _DURATION_RE.match(str(value))
    if not match:
        raise ValueError(f"invalid duration: {value!r}")
    amount, unit = match.groups()
    return float(amount) * _UNIT_SECONDS[unit.lower()]


def format_duration(seconds: float) -> str:
    """Render seconds back into the most natural unit (inverse of parsing)."""
    for unit, factor in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= factor and seconds % factor == 0:
            return f"{int(seconds // factor)}{unit}"
    return f"{int(seconds)}s" if seconds == int(seconds) else f"{seconds}s"
