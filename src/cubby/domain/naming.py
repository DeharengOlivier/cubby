"""Rules for turning configured text into a filesystem name.

A category name, the unsorted folder, a renamed invoice: each becomes part of a
path that cubby then writes to. A string from a config file is an input, not a
path, so it is validated here before it is ever joined onto one. The rule is
deliberately strict: one component, no separators, nothing that can climb.
"""

from __future__ import annotations

#: Names that are legal strings but cannot be a folder cubby owns.
RESERVED_COMPONENTS = frozenset({"", ".", ".."})

_SEPARATORS = ("/", "\\", "\0")


def safe_component(value: str, *, field: str) -> str:
    """Return ``value`` normalized as one safe path component.

    Args:
        value: The configured text.
        field: The setting it came from, named in the error so the user knows
            what to correct.

    Returns:
        The value with surrounding whitespace removed.

    Raises:
        ValueError: The value is empty, reserved, or contains a path separator.
    """
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text, got {type(value).__name__}.")

    component = value.strip()
    if component in RESERVED_COMPONENTS:
        raise ValueError(
            f"{field} must be a folder name, got {value!r}. "
            "Empty names, '.' and '..' are not folders cubby can own."
        )
    for separator in _SEPARATORS:
        if separator in component:
            raise ValueError(
                f"{field} must be a single folder name with no path separator, "
                f"got {value!r}. Nested or absolute paths would let cubby write "
                "outside the folder it watches."
            )
    return component
