"""Commands for the background agent: install, uninstall, status, doctor."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from .. import __version__
from ..adapters import state
from ..adapters.config import (
    find_user_config,
)
from ..adapters.extraction import PARSABLE
from ..adapters.ledger import Ledger, RunRecord
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
from ..adapters.ui import Palette
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
    if getattr(args, "month_style", None):
        base += ["--month-style", str(args.month_style)]
    if getattr(args, "month_lang", None):
        base += ["--month-lang", str(args.month_lang)]
    return base


def cmd_install(args: argparse.Namespace) -> int:
    config = load_from_args(args)
    if source_error(config):
        return EXIT_FAILED
    service = get_service()
    log_path = state.log_path()
    environment = {"CUBBY_STATE_DIR": str(state.state_dir())}
    if config_env := os.environ.get("CUBBY_CONFIG"):
        environment["CUBBY_CONFIG"] = str(Path(config_env).expanduser().resolve())
    spec = ServiceSpec(program_args=_program_args(args), log_path=log_path, environment=environment)
    path = service.install(spec)
    print(f"Installed {service.name} agent: {path} (running)")
    print(
        f"Cubby will watch {config.settings.source} "
        f"(delay {format_duration(config.settings.delay)}). Logs: {log_path}"
    )
    return EXIT_OK


def cmd_uninstall(args: argparse.Namespace) -> int:
    service = detect_service()
    if service is None or not service.uninstall():
        print("No cubby agent was installed.")
        return EXIT_OK
    print(f"Removed the {service.name} agent.")
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
    never_passed = bool(
        service and installed and running and beat is None and _installed_for(service) > 120.0
    )
    return {
        "manager": service.name if service else None,
        "installed": installed,
        "running": running,
        "unit": str(service.unit_path(DEFAULT_LABEL)) if service and installed else None,
        "last_pass_age": age,
        "stale": bool(beat and age is not None and stale_after and age > stale_after),
        "never_passed": never_passed,
        "watching": beat.source if beat else None,
    }


def _agent_text(pal: Palette, agent: dict[str, Any]) -> str:
    if not agent["installed"]:
        return pal.yellow("not installed")
    if agent["running"]:
        return pal.green(f"running ({agent['manager']})")
    return pal.yellow(f"installed but not running ({agent['manager']})")


def cmd_status(args: argparse.Namespace) -> int:
    agent = _agent_state()
    pause = current_pause()
    runs = Ledger().runs(limit=1)
    last = runs[0] if runs else None
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
            "log": str(state.log_path()),
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return EXIT_OK if healthy else EXIT_FAILED

    _print_status(agent, pause, last)
    return EXIT_OK if healthy else EXIT_FAILED


def _print_status(agent: dict[str, Any], pause: Pause | None, last: RunRecord | None) -> None:
    pal = palette()
    kv(pal, "agent", _agent_text(pal, agent))
    if agent["unit"]:
        kv(pal, "agent file", agent["unit"])
    if agent["watching"]:
        kv(pal, "watching", agent["watching"])
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
            print(f"  {failure.file}  {pal.dim(failure.error)}")
    log_path = state.log_path()
    tail = read_tail(log_path, limit=5)
    kv(pal, "recent log", pal.dim(str(log_path)) if tail else pal.dim("(none yet)"))
    for record in tail:
        print(f"  {pal.dim(human_line(record) if 'ts' in record else record['msg'])}")


def _last_pass_text(pal: Palette, agent: dict[str, Any]) -> str:
    if agent["never_passed"]:
        return pal.yellow("no pass completed since install: see the log")
    if agent["last_pass_age"] is None:
        return pal.dim("never")
    text = format_age(agent["last_pass_age"])
    return pal.yellow(text + " (stale)") if agent["stale"] else text


def cmd_doctor(args: argparse.Namespace) -> int:
    pal = palette()
    config = load_from_args(args)
    service = detect_service()
    print(pal.bold(pal.accent(f"cubby {__version__}")))
    kv(pal, "platform", sys.platform)
    kv(pal, "service", service.name if service else pal.yellow("none (manual watch only)"))
    kv(pal, "config file", str(find_user_config() or "defaults only"))
    kv(pal, "source", str(config.settings.source))
    kv(pal, "state", str(state.state_dir()))
    kv(pal, "log", str(state.log_path()))
    tools = {name: bool(shutil.which(name)) for name in ("pdftotext", "textutil", "antiword")}
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
    kv(pal, "notifications", "on" if config.settings.notify else pal.dim("off (notify = false)"))
    if getattr(args, "notify", False):
        return _test_notification(pal)
    return 0


def _test_notification(pal: Palette) -> int:
    """Send one notification, so the person can see the alert channel works."""
    problems: list[str] = []
    notifier(True, warn=problems.append)("Test notification: cubby can reach you.")
    if problems:
        print(pal.yellow(f"notification test failed: {problems[0]}"), file=sys.stderr)
        return EXIT_FAILED
    print("Sent a test notification. If it did not appear, allow notifications for")
    print("Script Editor (macOS) or your notification daemon (Linux).")
    return EXIT_OK
