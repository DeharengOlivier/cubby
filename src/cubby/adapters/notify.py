"""Desktop notifications: the agent's way to reach the person it works for.

The agent runs unattended, so a problem it only writes to its log is a
problem nobody sees. A notification goes to the logged-in user through the
platform's own tool: ``osascript`` on macOS, ``notify-send`` on Linux. The
message travels as a program argument, never inside a script, so a file name
cannot inject AppleScript or shell. Its control characters are escaped as they
are for the terminal, and for ``notify-send``, whose body is markup, so are
``&``, ``<`` and ``>``: a file name cannot inject a tag or a link either.

Notifying is best effort and never raises: a missing tool or a failure is
reported once to the caller's ``warn`` and the agent carries on.
"""

from __future__ import annotations

import html
import shutil
import subprocess
import sys
from collections.abc import Callable

from .ui import escape_for_terminal

TIMEOUT = 5.0
TITLE = "cubby"
#: Notification centres truncate long text; keep the useful start.
MAX_CHARS = 240

Notify = Callable[[str], None]
Warn = Callable[[str], None]


def _quiet(_: str) -> None:
    return None


def command(message: str, platform: str | None = None) -> list[str] | None:
    """The argument list that shows ``message``, or None if there is no tool."""
    platform = platform or sys.platform
    text = escape_for_terminal(message)
    text = text if len(text) <= MAX_CHARS else text[: MAX_CHARS - 1] + "…"
    if platform == "darwin":
        tool = shutil.which("osascript")
        if tool is None:
            return None
        return [
            tool,
            "-e",
            "on run argv",
            "-e",
            f'display notification (item 1 of argv) with title "{TITLE}"',
            "-e",
            "end run",
            text,
        ]
    tool = shutil.which("notify-send")
    if tool is None:
        return None
    # Shortened first, then made markup: a cut never falls inside ``&amp;``.
    return [tool, "--app-name", TITLE, TITLE, html.escape(text, quote=False)]


def notifier(enabled: bool, *, warn: Warn = _quiet) -> Notify:
    """A function that shows a notification, or does nothing when disabled."""
    if not enabled:
        return _quiet
    warned = False

    def notify(message: str) -> None:
        nonlocal warned
        cmd = command(message)
        problem: str | None = None
        if cmd is None:
            problem = "no notification tool (osascript or notify-send) found"
        else:
            try:
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=TIMEOUT, check=False
                )
            except (OSError, subprocess.SubprocessError) as exc:
                problem = f"notification failed: {exc}"
            else:
                if result.returncode != 0:
                    detail = (result.stderr or "").strip() or f"exit code {result.returncode}"
                    problem = f"notification failed: {detail}"
        if problem and not warned:
            warned = True
            warn(f"{problem}; problems are still written to the log")

    return notify
