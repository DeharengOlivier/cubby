"""Commands for the background agent: install, uninstall, status, doctor."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from dataclasses import asdict, replace
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
from ..adapters.extraction import PARSABLE, converters_present
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
from ..adapters.ui import Palette, Shown, dumps_for_terminal
from ..adapters.ui import escape_for_terminal as shown
from ..app.activity import Activity, summarize
from ..app.readiness import Readiness, check_readiness
from ..domain.category import Config, Settings
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
    # The same for the state folder's base: the agent must find the default
    # state folder the shell finds, or on macOS their logs would part.
    if xdg_state := os.environ.get("XDG_STATE_HOME"):
        environment["XDG_STATE_HOME"] = str(Path(xdg_state).expanduser().resolve())
    spec = ServiceSpec(program_args=_program_args(args), log_path=log_path, environment=environment)
    path = service.install(spec)
    print(f"Installed {shown(service.name)} agent: {shown(str(path))} (running)")
    print(
        f"Cubby will watch {shown(str(config.settings.source))} "
        f"(delay {shown(format_duration(config.settings.delay))}). Logs: {shown(str(log_path))}"
    )
    return EXIT_OK


def cmd_uninstall(args: argparse.Namespace) -> int:
    service = detect_service()
    if service is None or not service.uninstall():
        print("No cubby agent was installed.")
    else:
        print(f"Removed the {shown(service.name)} agent.")
    # The manager can lose track of a process that still runs, and a foreground
    # `cubby watch` has no unit at all: the heartbeat is the last word.
    pid = _still_sorting()
    if pid is not None:
        print(
            f"cubby: a cubby process (pid {pid:d}) is still sorting; "
            f"stop it with: kill -TERM {pid:d}",
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
        "interval": beat.interval if beat else None,
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


def _agent_text(pal: Palette, agent: dict[str, Any]) -> Shown:
    if not agent["installed"]:
        if live := agent["live_pid"]:
            return pal.yellow(Shown(f"not installed, but cubby (pid {live:d}) is still sorting"))
        return pal.yellow(Shown("not installed"))
    manager = shown(agent["manager"])
    if agent["running"]:
        pid = f", pid {agent['live_pid']:d}" if agent["live_pid"] else ""
        return pal.green(Shown(f"running ({manager}{pid})"))
    return pal.yellow(Shown(f"installed but not running ({manager})"))


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
    passing = agent["running"] and not agent["stale"] and not agent["never_passed"]
    live = bool(agent["live_pid"]) or passing
    readiness = _readiness(args, agent["watching"], paused=pause is not None)
    # Not ready is a fault (exit 1) only for an agent that should be sorting,
    # installed or alive. A pause is the user's own decision, which ends by
    # itself or on `cubby resume`: not ready, but not a fault. Without an
    # agent, readiness is advice for the next install.
    expected = agent["installed"] or bool(agent["live_pid"])
    healthy = (not agent["installed"] or passing) and not (expected and readiness.problems)
    saturation = _saturation(day, agent)

    if getattr(args, "json", False):
        payload = {
            "version": 1,
            "healthy": healthy,
            "live": live,
            "readiness": readiness.to_json(),
            "agent": agent,
            "paused": pause.to_json() if pause else None,
            "last_run": last.to_json() if last else None,
            "activity": {"hours": ACTIVITY_HOURS, **asdict(day), "saturation": saturation},
            "log": str(state.log_path()),
        }
        print(dumps_for_terminal(payload))
        return EXIT_OK if healthy else EXIT_FAILED

    pal = palette()
    _print_status(pal, agent, pause)
    kv(pal, "live", Shown("yes") if live else pal.yellow(Shown("no")))
    kv(pal, "ready", _readiness_text(pal, readiness, pause))
    _print_runs(pal, last, day)
    _print_latency(pal, day, saturation, agent)
    _print_log(pal)
    return EXIT_OK if healthy else EXIT_FAILED


def _readiness(args: argparse.Namespace, watching: str | None, *, paused: bool) -> Readiness:
    """Readiness, checked with the settings the agent runs with, as far as they are known.

    In order: ``--config`` (the person asked; a config it cannot read is exit
    2), the settings the agent reports in its heartbeat, the command line of
    the installed unit, then the default config. The folder is the one the
    agent last watched, when it said.

    Raises:
        ValueError: The ``--config`` file is missing or invalid.
    """
    if getattr(args, "config", None):
        path = Path(args.config).expanduser()
        if not path.is_file():
            raise ValueError(f"config file not found: {path}")
        return _checked(load_from_args(args), watching, paused, "flag", str(path))
    beat = Ledger().heartbeat()
    if beat is not None and beat.runs_with is not None:
        runs_with = beat.runs_with
        try:
            settings = replace(
                Settings(),
                source=Path(watching or beat.source),
                unsorted_dir=runs_with.unsorted_dir,
                content_scan=runs_with.content_scan,
            )
        except ValueError:
            pass  # a damaged report: fall back to the unit
        else:
            managed = frozenset(runs_with.folders)
            return _check(settings, managed, paused, "agent", runs_with.config)
    service = detect_service()
    unit = service.program_args() if service and service.is_installed() else None
    flags = _agent_flags(unit) if unit else None
    basis = "unit" if flags is not None else "default"
    flags = flags if flags is not None else argparse.Namespace()
    config_file = getattr(flags, "config", None) or config_module.find_user_config()
    try:
        config = load_from_args(flags)
    except (ValueError, OSError) as exc:  # TOMLDecodeError included
        problem = f"config error: {exc}"
        return Readiness((problem,), paused, basis=basis, config=_text(config_file))
    return _checked(config, watching, paused, basis, _text(config_file))


def _text(path: str | Path | None) -> str | None:
    return None if path is None else str(path)


def _agent_flags(program_args: list[str]) -> argparse.Namespace | None:
    """The readiness settings of an installed ``cubby watch`` command line."""
    if "watch" not in program_args:
        return None
    flags = argparse.Namespace(config=None, source=None, no_content=False)
    rest = program_args[program_args.index("watch") + 1 :]
    for flag, value in zip(rest, [*rest[1:], None], strict=True):
        if flag in ("--config", "--source") and value is not None:
            setattr(flags, flag.removeprefix("--"), value)
        elif flag == "--no-content":
            flags.no_content = True
    return flags


def _checked(
    config: Config, watching: str | None, paused: bool, basis: str, config_file: str | None
) -> Readiness:
    settings = config.settings
    if watching:
        settings = replace(settings, source=Path(watching))
    return _check(settings, config.managed_dirs, paused, basis, config_file)


def _check(
    settings: Settings, managed: frozenset[str], paused: bool, basis: str, config: str | None
) -> Readiness:
    readiness = check_readiness(
        settings,
        managed,
        state_folder=state.state_dir(),
        paused=paused,
        converters=converters_present() if settings.content_scan else {},
    )
    return replace(readiness, basis=basis, config=config)


_BASIS_TEXT = {
    "agent": "the settings the agent reported",
    "unit": "the installed agent's command line",
    "flag": "--config",
    "default": "the default config",
}


def _readiness_text(pal: Palette, readiness: Readiness, pause: Pause | None) -> Shown:
    if readiness.ready:
        text = Shown("yes")
    else:
        reasons = [*readiness.problems, *([pause.describe()] if pause else [])]
        text = pal.yellow(Shown(f"no ({shown('; '.join(reasons))})"))
    if readiness.degraded:
        formats = shown(", ".join(f".{ext}" for ext in readiness.degraded))
        degraded = Shown(f"; degraded: no converter reads {formats} (see cubby doctor)")
        text = Shown(text + pal.dim(degraded))
    basis = shown(_BASIS_TEXT[readiness.basis])
    if readiness.config:
        basis = Shown(f"{basis} ({shown(readiness.config)})")
    return Shown(text + pal.dim(Shown(f"; checked with {basis}")))


def _saturation(day: Activity, agent: dict[str, Any]) -> float | None:
    """The p95 run duration as a share of the interval between passes."""
    interval: float | None = agent["interval"]
    if day.pass_ms is None or not interval:
        return None
    return round(day.pass_ms.p95 / (interval * 1000), 4)


def _print_latency(
    pal: Palette, day: Activity, saturation: float | None, agent: dict[str, Any]
) -> None:
    if day.pass_ms is not None:
        latency = day.pass_ms
        text = Shown(
            f"p50 {_seconds(latency.p50)} s, p95 {_seconds(latency.p95)} s, "
            f"max {_seconds(latency.max)} s over {latency.count:d} "
            f"agent run{'s' if latency.count != 1 else ''}"
        )
        if saturation is not None:
            interval = shown(format_duration(agent["interval"]))
            share = Shown(f"p95 is {saturation:.0%} of the {interval} interval")
            text = Shown(text + "; " + (pal.yellow(share) if saturation >= 0.5 else share))
        kv(pal, "run time", text)
    if day.backlog is not None:
        backlog = day.backlog
        trend = shown(
            {1: "rising", -1: "falling", 0: "steady"}[
                (backlog.last > backlog.first) - (backlog.last < backlog.first)
            ]
        )
        kv(
            pal,
            "backlog",
            Shown(
                f"{backlog.first:d} -> {backlog.last:d} waiting to settle ({trend}), "
                f"peak {backlog.peak:d}"
            ),
        )


def _seconds(ms: int) -> Shown:
    return Shown(f"{ms / 1000:.3f}".rstrip("0").rstrip("."))


def _print_status(pal: Palette, agent: dict[str, Any], pause: Pause | None) -> None:
    kv(pal, "agent", _agent_text(pal, agent))
    if agent["unit"]:
        kv(pal, "agent file", shown(agent["unit"]))
    if agent["watching"]:
        # The heartbeat outlives the process: only a live one is watching now.
        live = agent["live_pid"] or agent["running"]
        kv(pal, "watching" if live else "last watched", shown(agent["watching"]))
    if pause:
        kv(pal, "paused", Shown(pal.yellow(shown(pause.describe()) + ": no file is moved")))
    kv(pal, "last pass", _last_pass_text(pal, agent))


def _print_runs(pal: Palette, last: RunRecord | None, day: Activity) -> None:
    if last is None:
        kv(pal, "last run", pal.dim(Shown("none recorded")))
    else:
        summary = f"{shown(last.finished)}  moved {last.moved:d}"
        if last.failed:
            summary += pal.yellow(f", {last.failed:d} failed")
        kv(pal, "last run", Shown(f"{summary}  ({shown(last.mode)}, run {shown(last.run)})"))
        for failure in last.failures[:5]:
            print(f"  {shown(failure.file)}  {pal.dim(shown(failure.error))}")
    _print_activity(pal, day)


def _print_log(pal: Palette) -> None:
    log_path = state.log_path()
    tail = read_tail(log_path, limit=5)
    kv(pal, "recent log", pal.dim(shown(str(log_path)) if tail else Shown("(none yet)")))
    for record in tail:
        line = human_line(record) if "ts" in record else shown(record["msg"])
        print(f"  {pal.dim(line)}")


def _print_activity(pal: Palette, day: Activity) -> None:
    if not day.runs:
        kv(pal, f"last {ACTIVITY_HOURS} h", pal.dim(Shown("no run moved or failed anything")))
        return
    summary = f"{day.runs:d} run{'s' if day.runs != 1 else ''}, moved {day.moved:d}"
    if day.failed:
        summary += pal.yellow(f", {day.failed:d} failed")
    if day.extraction_failures:
        # Sorted by name and type, so not failed: but a converter broke on them.
        unread = f", content unreadable for {day.extraction_failures:d} (see cubby log --warnings)"
        summary += pal.yellow(unread)
    if not day.complete:
        summary += pal.yellow(
            f" (the ledger keeps its last {KEEP_LINES:d} runs: older ones may be missing)"
        )
    kv(pal, f"last {ACTIVITY_HOURS} h", Shown(summary))
    for group in day.errors:
        print(f"  {group.count:d}x {shown(_shortened(group.kind))}")
        files = ", ".join(shown(name) for name in group.files)
        seen = f"last seen {shown(group.last_seen)}; files: {files}"
        print(f"     {pal.dim(seen + '; cubby ' + shown(', '.join(group.versions)))}")


def _shortened(text: str, limit: int = 100) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _last_pass_text(pal: Palette, agent: dict[str, Any]) -> Shown:
    if agent["never_passed"]:
        return pal.yellow(Shown("no pass completed since install: see the log"))
    if agent["last_pass_age"] is None:
        return pal.dim(Shown("never"))
    text = shown(format_age(agent["last_pass_age"]))
    text = pal.yellow(Shown(text + " (stale)")) if agent["stale"] else text
    measures = agent["last_pass"]
    return Shown(f"{text}, {_measures_text(pal, measures)}") if measures else text


def _measures_text(pal: Palette, measures: dict[str, Any]) -> str:
    seconds = f"{measures['seconds']:.3f}".rstrip("0").rstrip(".")
    parts = [f"took {seconds} s", f"moved {measures['moved']:d}"]
    if measures["failed"]:
        parts.append(pal.yellow(f"{measures['failed']:d} failed"))
    if measures["waiting"]:
        parts.append(f"{measures['waiting']:d} waiting to settle")
    return ", ".join(parts)


def _notifications_text(pal: Palette, enabled: bool) -> Shown:
    if not enabled:
        return pal.dim(Shown("off (notify = false)"))
    if notify_module.command("cubby") is None:
        return pal.yellow(
            Shown(
                "on, but no notification tool (osascript or notify-send) found: "
                "problems go to the log only"
            )
        )
    return Shown("on")


def cmd_doctor(args: argparse.Namespace) -> int:
    pal = palette()
    config = load_from_args(args)
    service = detect_service()
    print(pal.bold(pal.accent(f"cubby {shown(__version__)}")))
    kv(pal, "platform", shown(sys.platform))
    kv(
        pal,
        "service",
        shown(service.name) if service else pal.yellow(Shown("none (manual watch only)")),
    )
    kv(pal, "config file", shown(str(find_user_config() or "defaults only")))
    kv(pal, "source", shown(str(config.settings.source)))
    kv(pal, "state", shown(str(state.state_dir())))
    kv(pal, "log", shown(str(state.log_path())))
    present = converters_present()
    tools = {name: present[name] for name in ("pdftotext", "textutil", "antiword", "catdoc")}
    libs = {name: present[name] for name in ("pypdf", "docx", "openpyxl")}
    kv(pal, "extract tools", format_features(pal, tools))
    kv(pal, "extract libs", format_features(pal, libs))
    kv(pal, "parsable", pal.dim(shown(", ".join(sorted(PARSABLE)))))
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
