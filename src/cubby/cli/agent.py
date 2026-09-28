"""Commands for the background agent: install, uninstall, status, doctor."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .. import __version__
from ..adapters import config as config_module
from ..adapters import notify as notify_module
from ..adapters import state
from ..adapters.config import (
    find_user_config,
)
from ..adapters.extraction import PARSABLE
from ..adapters.ledger import KEEP_LINES, Heartbeat, Ledger, RunRecord
from ..adapters.logging import human_line, read_tail
from ..adapters.notify import notifier
from ..adapters.pause import Pause, current_pause
from ..adapters.service import (
    DEFAULT_LABEL,
    Service,
    ServiceSpec,
    detect_service,
    get_service,
)
from ..adapters.ui import Palette, dumps_for_terminal
from ..adapters.ui import escape_for_terminal as shown
from ..app.activity import Activity, summarize
from ..domain.duration import format_duration
from .common import (
    EXIT_FAILED,
    EXIT_OK,
    format_age,
    format_features,
    kv,
    load_from_args,
    palette,
    source_error,
)

#: The window of `cubby status`'s activity summary.
ACTIVITY_HOURS = 24


def _program_args(args: argparse.Namespace) -> list[str]:
    """The command the background service should run: ``cubby watch ...``.

    The flags given to ``install`` are baked into the agent so it sorts with the
    same settings the user asked for.
    """
    exe = shutil.which("cubby")
    base = [exe] if exe else [sys.executable, "-m", "cubby"]
    base += ["watch", "--wait-for-source"]
    if getattr(args, "config", None):
        base += ["--config", str(Path(args.config).expanduser().resolve())]
    if getattr(args, "source", None):
        base += ["--source", str(Path(args.source).expanduser().resolve())]
    if getattr(args, "delay", None) is not None:
        base += ["--delay", str(args.delay)]
    if getattr(args, "interval", None) is not None:
        base += ["--interval", str(args.interval)]
    if getattr(args, "no_content", False):
        base += ["--no-content"]
    if getattr(args, "month_style", None):
        base += ["--month-style", str(args.month_style)]
    if getattr(args, "month_lang", None):
        base += ["--month-lang", str(args.month_lang)]
    return base


def cmd_install(args: argparse.Namespace) -> int:
    config = load_from_args(args)
    if source_error(config, args):
        return EXIT_FAILED
    service = get_service()
    log_path = state.log_path()
    environment = {"CUBBY_STATE_DIR": str(state.state_dir())}
    if config_env := os.environ.get("CUBBY_CONFIG"):
        environment["CUBBY_CONFIG"] = str(Path(config_env).expanduser().resolve())
    # launchd and systemd start the agent without the login shell's variables:
    # without this, it would read another config than the one install read.
    if config_home := config_module.config_home_override():
        environment["XDG_CONFIG_HOME"] = str(config_home)
    spec = ServiceSpec(program_args=_program_args(args), log_path=log_path, environment=environment)
    path = service.install(spec)
    print(f"Installed {service.name} agent: {shown(str(path))} (running)")
    print(
        f"Cubby will watch {shown(str(config.settings.source))} "
        f"(delay {format_duration(config.settings.delay)}). Logs: {shown(str(log_path))}"
    )
    return EXIT_OK


def cmd_uninstall(args: argparse.Namespace) -> int:
    service = detect_service()
    if service is None or not service.uninstall():
        print("No cubby agent was installed.")
    else:
        print(f"Removed the {service.name} agent.")
    # The manager can lose track of a process that still runs, and a foreground
    # `cubby watch` has no unit at all: the heartbeat is the last word.
    pid = _still_sorting()
    if pid is not None:
        print(
            f"cubby: a cubby process (pid {pid}) is still sorting; stop it with: kill -TERM {pid}",
            file=sys.stderr,
        )
        return EXIT_FAILED
    return EXIT_OK


def _installed_for(service: Service) -> float:
    """Seconds since the agent's unit file was written."""
    try:
        return time.time() - service.unit_path(DEFAULT_LABEL).stat().st_mtime
    except OSError:
        return 0.0


def _agent_state() -> dict[str, Any]:
    """What the service manager and the heartbeat say about the agent."""
    service = detect_service()
    installed = bool(service and service.is_installed())
    running = bool(service and installed and service.is_running())
    beat = Ledger().heartbeat()
    stale_after = max(3 * beat.interval, 120.0) if beat else None
    age = beat.age_seconds() if beat else None
    stale = bool(beat and age is not None and stale_after and age > stale_after)
    # A fresh heartbeat from a live process: something is sorting, installed or
    # not (a foreground `cubby watch`, or an agent that survived its uninstall).
    live_pid = _live_pid(beat, stale)
    never_passed = bool(
        service and installed and running and beat is None and _installed_for(service) > 120.0
    )
    return {
        "manager": service.name if service else None,
        "installed": installed,
        "running": running,
        "unit": str(service.unit_path(DEFAULT_LABEL)) if service and installed else None,
        "last_pass_age": age,
        "stale": stale,
        "live_pid": live_pid,
        "never_passed": never_passed,
        "watching": beat.source if beat else None,
        "last_pass": asdict(beat.last_pass) if beat and beat.last_pass else None,
    }


def _live_pid(beat: Heartbeat | None, stale: bool) -> int | None:
    """The pid behind a fresh heartbeat, if that process still exists."""
    return beat.pid if beat and not stale and beat.process_alive() else None


def _still_sorting() -> int | None:
    """A cubby process that beat within its last few intervals and is alive."""
    beat = Ledger().heartbeat()
    if beat is None:
        return None
    return _live_pid(beat, beat.age_seconds() > max(3 * beat.interval, 120.0))


def _agent_text(pal: Palette, agent: dict[str, Any]) -> str:
    if not agent["installed"]:
        if agent["live_pid"]:
            return pal.yellow(
                f"not installed, but cubby (pid {agent['live_pid']}) is still sorting"
            )
        return pal.yellow("not installed")
    if agent["running"]:
        pid = f", pid {agent['live_pid']}" if agent["live_pid"] else ""
        return pal.green(f"running ({agent['manager']}{pid})")
    return pal.yellow(f"installed but not running ({agent['manager']})")


def cmd_status(args: argparse.Namespace) -> int:
    agent = _agent_state()
    pause = current_pause()
    runs = Ledger().runs()
    last = runs[0] if runs else None
    # A ledger at its line limit has dropped its oldest runs (the count is of
    # the lines that could be read, so a damaged line can hide a trim).
    day = summarize(
        runs,
        since=datetime.now() - timedelta(hours=ACTIVITY_HOURS),
        trimmed=len(runs) >= KEEP_LINES,
    )
    healthy = not agent["installed"] or (
        agent["running"] and not agent["stale"] and not agent["never_passed"]
    )

    if getattr(args, "json", False):
        payload = {
            "version": 1,
            "healthy": healthy,
            "agent": agent,
            "paused": pause.to_json() if pause else None,
            "last_run": last.to_json() if last else None,
            "activity": {"hours": ACTIVITY_HOURS, **asdict(day)},
            "log": str(state.log_path()),
        }
        print(dumps_for_terminal(payload))
        return EXIT_OK if healthy else EXIT_FAILED

    _print_status(agent, pause, last, day)
    return EXIT_OK if healthy else EXIT_FAILED


def _print_status(
    agent: dict[str, Any], pause: Pause | None, last: RunRecord | None, day: Activity
) -> None:
    pal = palette()
    kv(pal, "agent", _agent_text(pal, agent))
    if agent["unit"]:
        kv(pal, "agent file", shown(agent["unit"]))
    if agent["watching"]:
        # The heartbeat outlives the process: only a live one is watching now.
        live = agent["live_pid"] or agent["running"]
        kv(pal, "watching" if live else "last watched", shown(agent["watching"]))
    if pause:
        kv(pal, "paused", pal.yellow(pause.describe() + ": no file is moved"))
    kv(pal, "last pass", _last_pass_text(pal, agent))
    if last is None:
        kv(pal, "last run", pal.dim("none recorded"))
    else:
        summary = f"{last.finished}  moved {last.moved}"
        if last.failed:
            summary += pal.yellow(f", {last.failed} failed")
        kv(pal, "last run", f"{summary}  ({last.mode}, run {last.run})")
        for failure in last.failures[:5]:
            print(f"  {shown(failure.file)}  {pal.dim(shown(failure.error))}")
    _print_activity(pal, day)
    log_path = state.log_path()
    tail = read_tail(log_path, limit=5)
    kv(pal, "recent log", pal.dim(shown(str(log_path))) if tail else pal.dim("(none yet)"))
    for record in tail:
        line = human_line(record) if "ts" in record else shown(record["msg"])
        print(f"  {pal.dim(line)}")


def _print_activity(pal: Palette, day: Activity) -> None:
    if not day.runs:
        kv(pal, f"last {ACTIVITY_HOURS} h", pal.dim("no run moved or failed anything"))
        return
    summary = f"{day.runs} run{'s' if day.runs != 1 else ''}, moved {day.moved}"
    if day.failed:
        summary += pal.yellow(f", {day.failed} failed")
    if day.extraction_failures:
        # Sorted by name and type, so not failed: but a converter broke on them.
        unread = f", content unreadable for {day.extraction_failures} (see cubby log --warnings)"
        summary += pal.yellow(unread)
    if not day.complete:
        summary += pal.yellow(
            f" (the ledger keeps its last {KEEP_LINES} runs: older ones may be missing)"
        )
    kv(pal, f"last {ACTIVITY_HOURS} h", summary)
    for group in day.errors:
        print(f"  {group.count}x {shown(_shortened(group.kind))}")
        files = ", ".join(shown(name) for name in group.files)
        seen = f"last seen {shown(group.last_seen)}; files: {files}"
        print(f"     {pal.dim(seen + '; cubby ' + ', '.join(group.versions))}")


def _shortened(text: str, limit: int = 100) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _last_pass_text(pal: Palette, agent: dict[str, Any]) -> str:
    if agent["never_passed"]:
        return pal.yellow("no pass completed since install: see the log")
    if agent["last_pass_age"] is None:
        return pal.dim("never")
    text = format_age(agent["last_pass_age"])
    text = pal.yellow(text + " (stale)") if agent["stale"] else text
    measures = agent["last_pass"]
    return f"{text}, {_measures_text(pal, measures)}" if measures else text


def _measures_text(pal: Palette, measures: dict[str, Any]) -> str:
    seconds = f"{measures['seconds']:.3f}".rstrip("0").rstrip(".")
    parts = [f"took {seconds} s", f"moved {measures['moved']}"]
    if measures["failed"]:
        parts.append(pal.yellow(f"{measures['failed']} failed"))
    if measures["waiting"]:
        parts.append(f"{measures['waiting']} waiting to settle")
    return ", ".join(parts)


def _notifications_text(pal: Palette, enabled: bool) -> str:
    if not enabled:
        return pal.dim("off (notify = false)")
    if notify_module.command("cubby") is None:
        return pal.yellow(
            "on, but no notification tool (osascript or notify-send) found: "
            "problems go to the log only"
        )
    return "on"


def cmd_doctor(args: argparse.Namespace) -> int:
    pal = palette()
    config = load_from_args(args)
    service = detect_service()
    print(pal.bold(pal.accent(f"cubby {__version__}")))
    kv(pal, "platform", sys.platform)
    kv(pal, "service", service.name if service else pal.yellow("none (manual watch only)"))
    kv(pal, "config file", shown(str(find_user_config() or "defaults only")))
    kv(pal, "source", shown(str(config.settings.source)))
    kv(pal, "state", shown(str(state.state_dir())))
    kv(pal, "log", shown(str(state.log_path())))
    tools = {
        name: bool(shutil.which(name)) for name in ("pdftotext", "textutil", "antiword", "catdoc")
    }
    libs = {}
    for lib in ("pypdf", "docx", "openpyxl"):
        try:
            __import__(lib)
            libs[lib] = True
        except (ImportError, OSError):  # absent, or a broken native wheel
            libs[lib] = False
    kv(pal, "extract tools", format_features(pal, tools))
    kv(pal, "extract libs", format_features(pal, libs))
    kv(pal, "parsable", pal.dim(", ".join(sorted(PARSABLE))))
    kv(pal, "notifications", _notifications_text(pal, config.settings.notify))
    if getattr(args, "notify", False):
        return _test_notification(pal)
    return 0


def _test_notification(pal: Palette) -> int:
    """Send one notification, so the person can see the alert channel works."""
    problems: list[str] = []
    notifier(True, warn=problems.append)("Test notification: cubby can reach you.")
    if problems:
        print(pal.yellow(f"notification test failed: {shown(problems[0])}"), file=sys.stderr)
        return EXIT_FAILED
    print("Sent a test notification. If it did not appear, allow notifications for")
    print("Script Editor (macOS) or your notification daemon (Linux).")
    return EXIT_OK
