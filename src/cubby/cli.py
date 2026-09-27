"""Command-line entry point. Thin: it parses args and wires app + adapters."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import tomllib
from pathlib import Path
from typing import Any

from . import __version__
from .adapters import state
from .adapters.config import (
    default_user_config_path,
    find_user_config,
    load_config,
    write_starter_config,
)
from .adapters.extraction import PARSABLE
from .adapters.journal import Journal
from .adapters.ledger import Ledger
from .adapters.lock import exclusive
from .adapters.logging import LevelLogger, file_logger, human_line, read_tail
from .adapters.service import (
    DEFAULT_LABEL,
    Service,
    ServiceError,
    ServiceSpec,
    detect_service,
    get_service,
)
from .adapters.ui import Palette, banner, supports_color
from .app.explain import Explanation, explain
from .app.history import recent_runs
from .app.report import SortOutcome, render_json, render_plan
from .app.sorter import Sorter
from .app.undo import undo_run
from .app.watcher import Watcher
from .domain.category import Config
from .domain.duration import format_duration

#: Exit codes, named so callers and tests do not repeat the integers.
EXIT_OK = 0
EXIT_FAILED = 1
EXIT_BAD_CONFIG = 2


def _palette() -> Palette:
    return Palette(supports_color(sys.stdout))


def _build_overrides(args: argparse.Namespace) -> dict[str, Any]:
    settings: dict[str, Any] = {}
    if getattr(args, "source", None):
        settings["source"] = args.source
    if getattr(args, "delay", None) is not None:
        settings["delay"] = args.delay
    if getattr(args, "interval", None) is not None:
        settings["interval"] = args.interval
    if getattr(args, "no_content", False):
        settings["content_scan"] = False
    if getattr(args, "month_style", None):
        settings["month_style"] = args.month_style
    if getattr(args, "month_lang", None):
        settings["month_lang"] = args.month_lang
    return {"settings": settings} if settings else {}


def _config_path(args: argparse.Namespace) -> Path | str:
    """The config file in play, for an error message."""
    if getattr(args, "config", None):
        return Path(args.config).expanduser()
    return find_user_config() or "the packaged defaults"


def _load(args: argparse.Namespace) -> Config:
    user_path = Path(args.config).expanduser() if getattr(args, "config", None) else None
    return load_config(user_path=user_path, overrides=_build_overrides(args))


def _require_source(config: Config) -> str | None:
    """Return an error message if the source folder is unusable, else None."""
    source = config.settings.source
    if not source.exists():
        return f"source folder does not exist: {source}"
    if not source.is_dir():
        return f"source is not a folder: {source}"
    return None


def _source_error(config: Config) -> bool:
    """Print why the source folder is unusable, if it is. True means stop."""
    if error := _require_source(config):
        print(f"cubby: {error}", file=sys.stderr)
        return True
    return False


def _loud(log: LevelLogger) -> LevelLogger:
    """A logger whose warnings and errors also reach stderr, whatever the verbosity."""

    def log_and_tell(message: str, *, level: str = "INFO") -> None:
        log(message, level=level)
        if level != "INFO":
            print(f"cubby: {level.lower()}: {message}", file=sys.stderr)

    return log_and_tell


def _print_outcomes(outcomes: list[SortOutcome], *, applied: bool) -> None:
    pal = _palette()
    if pal.enabled:
        print(banner(pal))
    print(render_plan(outcomes, applied=applied, palette=pal))


def cmd_plan(args: argparse.Namespace) -> int:
    config = _load(args)
    if _source_error(config):
        return EXIT_FAILED
    outcomes = Sorter(config).sort_once(apply=False, respect_age=False)
    if getattr(args, "json", False):
        print(render_json(outcomes, applied=False))
        return EXIT_OK
    _print_outcomes(outcomes, applied=False)
    return EXIT_OK


def cmd_run(args: argparse.Namespace) -> int:
    config = _load(args)
    if _source_error(config):
        return EXIT_FAILED
    log = file_logger(echo=args.verbose)
    loud = _loud(log)

    def warn(message: str) -> None:
        loud(message, level="WARNING")

    sorter = Sorter(config, log=log, warn=warn, journal=Journal(), ledger=Ledger())
    with exclusive():
        outcomes = sorter.sort_once(apply=True)
    _print_outcomes(outcomes, applied=True)
    return EXIT_FAILED if any(o.needs_attention for o in outcomes) else EXIT_OK


def cmd_undo(args: argparse.Namespace) -> int:
    with exclusive():
        try:
            result = undo_run(Journal(), getattr(args, "run", None), log=print)
        except KeyError:
            print(
                f"cubby: no run {args.run!r} in the journal; see 'cubby history'", file=sys.stderr
            )
            return EXIT_FAILED
    print(f"Restored {result.restored} file(s).")
    if result.failed:
        print(
            f"cubby: {len(result.failed)} file(s) could not be restored and stay pending; "
            "run 'cubby undo' again once the cause is fixed.",
            file=sys.stderr,
        )
        return EXIT_FAILED
    return EXIT_OK


def cmd_watch(args: argparse.Namespace) -> int:
    config = _load(args)
    # The agent waits for a folder that is not there yet (a drive mounted after
    # login) instead of exiting into a restart loop; a person is told at once.
    if not getattr(args, "wait_for_source", False) and _source_error(config):
        return EXIT_FAILED
    # Under launchd or systemd, stdout is appended to the log file already
    # written by the logger: echo only to a person at a terminal.
    log = file_logger(echo=sys.stdout.isatty())
    loud = _loud(log) if sys.stderr.isatty() else log

    def warn(message: str) -> None:
        loud(message, level="WARNING")

    ledger = Ledger()
    sorter = Sorter(config, log=log, warn=warn, journal=Journal(), ledger=ledger, mode="watch")
    watcher = Watcher(sorter, config.settings.interval, log=loud, ledger=ledger)
    log(
        f"cubby watching {config.settings.source} "
        f"(delay {format_duration(config.settings.delay)}, "
        f"every {format_duration(config.settings.interval)})"
    )
    try:
        watcher.run()
    except KeyboardInterrupt:
        log("cubby stopped")
    return EXIT_OK


def _explanation_json(item: Explanation) -> dict[str, Any]:
    return {
        "path": str(item.path),
        "category": item.category,
        "stage": item.stage.value,
        "rule": item.rule,
        "destination": str(item.destination),
        "renamed_to": item.renamed_to,
        "skipped": item.skipped,
        "outside_source": item.outside,
    }


def _print_explanation(pal: Palette, item: Explanation, source: Path) -> None:
    print(pal.bold(str(item.path)))
    try:
        shown = item.destination.relative_to(source)
    except ValueError:
        shown = item.destination
    _kv(pal, "  goes to", pal.accent(str(shown)))
    rule = f"{item.rule}  ({item.stage.value} stage)" if item.rule else "no rule matched"
    _kv(pal, "  decided by", rule)
    if item.renamed_to:
        _kv(pal, "  renamed", item.renamed_to)
    if item.outside:
        _kv(pal, "  note", pal.yellow("not in the watched folder: a run would not see it"))
    elif item.skipped:
        _kv(pal, "  left alone", pal.yellow(item.skipped))


def cmd_explain(args: argparse.Namespace) -> int:
    config = _load(args)
    items: list[Explanation] = []
    missing = False
    for raw in args.files:
        try:
            items.append(explain(Path(raw).expanduser(), config))
        except FileNotFoundError:
            print(f"cubby: no such file: {raw}", file=sys.stderr)
            missing = True
    if getattr(args, "json", False):
        payload = {"version": 1, "items": [_explanation_json(i) for i in items]}
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        pal = _palette()
        for item in items:
            _print_explanation(pal, item, config.settings.source)
    return EXIT_FAILED if missing else EXIT_OK


def cmd_history(args: argparse.Namespace) -> int:
    runs = recent_runs(Ledger(), Journal(), limit=args.limit)
    if getattr(args, "json", False):
        payload = {
            "version": 1,
            "runs": [{**r.record.to_json(), "undone": r.undone} for r in runs],
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return EXIT_OK
    pal = _palette()
    if not runs:
        print(pal.dim("No runs recorded yet."))
        return EXIT_OK
    for summary in runs:
        record = summary.record
        counts = f"moved {record.moved}"
        if record.failed:
            counts += pal.yellow(f", {record.failed} failed")
        flag = pal.dim("  undone") if summary.undone else ""
        print(f"{record.finished}  {pal.accent(record.run)}  {record.mode:<5}  {counts}{flag}")
    print(pal.dim("\nUndo one with: cubby undo --run <id>"))
    return EXIT_OK


def cmd_init(args: argparse.Namespace) -> int:
    target = Path(args.path).expanduser() if args.path else default_user_config_path()
    try:
        write_starter_config(target, force=args.force)
    except FileExistsError:
        print(f"cubby: {target} already exists; use --force to replace it", file=sys.stderr)
        return EXIT_FAILED
    print(f"Wrote a starter config to {target}")
    print("Preview what it does with: cubby plan")
    return EXIT_OK


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
    config = _load(args)
    if _source_error(config):
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


def _kv(pal: Palette, key: str, value: str) -> None:
    print(f"{pal.dim(key.ljust(16))}{value}")


def _features(pal: Palette, mapping: dict[str, bool]) -> str:
    parts = [
        pal.green(f"{name} ok") if present else pal.dim(f"{name} -")
        for name, present in mapping.items()
    ]
    return "  ".join(parts)


def _age(seconds: float) -> str:
    return format_duration(max(0, round(seconds))) + " ago"


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
            "last_run": last.to_json() if last else None,
            "log": str(state.log_path()),
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return EXIT_OK if healthy else EXIT_FAILED

    pal = _palette()
    _kv(pal, "agent", _agent_text(pal, agent))
    if agent["unit"]:
        _kv(pal, "agent file", agent["unit"])
    if agent["watching"]:
        _kv(pal, "watching", agent["watching"])
    if agent["never_passed"]:
        _kv(pal, "last pass", pal.yellow("no pass completed since install: see the log"))
    elif agent["last_pass_age"] is None:
        _kv(pal, "last pass", pal.dim("never"))
    else:
        text = _age(agent["last_pass_age"])
        _kv(pal, "last pass", pal.yellow(text + " (stale)") if agent["stale"] else text)
    if last is None:
        _kv(pal, "last run", pal.dim("none recorded"))
    else:
        summary = f"{last.finished}  moved {last.moved}"
        if last.failed:
            summary += pal.yellow(f", {last.failed} failed")
        _kv(pal, "last run", f"{summary}  ({last.mode}, run {last.run})")
        for failure in last.failures[:5]:
            print(f"  {failure.file}  {pal.dim(failure.error)}")
    log_path = state.log_path()
    tail = read_tail(log_path, limit=5)
    _kv(pal, "recent log", pal.dim(str(log_path)) if tail else pal.dim("(none yet)"))
    for record in tail:
        print(f"  {pal.dim(human_line(record) if 'ts' in record else record['msg'])}")
    return EXIT_OK if healthy else EXIT_FAILED


def cmd_doctor(args: argparse.Namespace) -> int:
    pal = _palette()
    config = _load(args)
    service = detect_service()
    print(pal.bold(pal.accent(f"cubby {__version__}")))
    _kv(pal, "platform", sys.platform)
    _kv(pal, "service", service.name if service else pal.yellow("none (manual watch only)"))
    _kv(pal, "config file", str(find_user_config() or "defaults only"))
    _kv(pal, "source", str(config.settings.source))
    _kv(pal, "state", str(state.state_dir()))
    _kv(pal, "log", str(state.log_path()))
    tools = {name: bool(shutil.which(name)) for name in ("pdftotext", "textutil", "antiword")}
    libs = {}
    for lib in ("pypdf", "docx", "openpyxl"):
        try:
            __import__(lib)
            libs[lib] = True
        except (ImportError, OSError):  # absent, or a broken native wheel
            libs[lib] = False
    _kv(pal, "extract tools", _features(pal, tools))
    _kv(pal, "extract libs", _features(pal, libs))
    _kv(pal, "parsable", pal.dim(", ".join(sorted(PARSABLE))))
    return 0


def _add_common_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", help="path to a config file")
    parser.add_argument("--source", help="folder to sort (default: from config)")
    parser.add_argument("--delay", help="min age before moving a file, e.g. 1m, 30s")
    parser.add_argument("--interval", help="watch poll interval, e.g. 30s")
    parser.add_argument("--no-content", action="store_true", help="disable content scanning")
    parser.add_argument(
        "--month-style",
        choices=("numeric", "letters"),
        help="invoice folder style: numeric (2026-07) or letters (juillet 2026)",
    )
    parser.add_argument(
        "--month-lang",
        choices=("fr", "en"),
        help="language for the letters style: fr (juillet) or en (July)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="echo actions")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cubby", description="Tidy your Downloads folder.")
    parser.add_argument("--version", action="version", version=f"cubby {__version__}")
    parser.set_defaults(func=None)
    sub = parser.add_subparsers(dest="command")

    p_plan = sub.add_parser("plan", help="show where files would go (moves nothing)")
    _add_common_flags(p_plan)
    p_plan.add_argument("--json", action="store_true", help="output the plan as JSON")
    p_plan.set_defaults(func=cmd_plan)

    p_run = sub.add_parser("run", help="sort the folder once")
    _add_common_flags(p_run)
    p_run.set_defaults(func=cmd_run)

    p_watch = sub.add_parser("watch", help="keep sorting the folder in the foreground")
    _add_common_flags(p_watch)
    p_watch.add_argument(
        "--wait-for-source",
        action="store_true",
        help="wait for a missing folder to appear instead of exiting (the agent uses this)",
    )
    p_watch.set_defaults(func=cmd_watch)

    p_undo = sub.add_parser("undo", help="revert the most recent run (or --run ID)")
    p_undo.add_argument("--run", help="the run to revert, as listed by 'cubby history'")
    p_undo.set_defaults(func=cmd_undo)

    p_status = sub.add_parser(
        "status", help="is the agent running, and what did it do last (exit 1 if unhealthy)"
    )
    p_status.add_argument("--json", action="store_true", help="output the status as JSON")
    p_status.set_defaults(func=cmd_status)

    p_install = sub.add_parser("install", help="install the background agent (auto-start)")
    _add_common_flags(p_install)
    p_install.set_defaults(func=cmd_install)

    p_uninstall = sub.add_parser("uninstall", help="remove the background agent")
    p_uninstall.set_defaults(func=cmd_uninstall)

    p_explain = sub.add_parser(
        "explain", help="say where files would go and which rule decides (moves nothing)"
    )
    _add_common_flags(p_explain)
    p_explain.add_argument("files", nargs="+", metavar="FILE", help="files to explain")
    p_explain.add_argument("--json", action="store_true", help="output as JSON")
    p_explain.set_defaults(func=cmd_explain)

    p_history = sub.add_parser("history", help="list recent runs, and which were undone")
    p_history.add_argument("-n", "--limit", type=int, default=20, help="how many runs (20)")
    p_history.add_argument("--json", action="store_true", help="output as JSON")
    p_history.set_defaults(func=cmd_history)

    p_init = sub.add_parser("init", help="write a starter config file")
    p_init.add_argument("--path", help="where to write it (default: ~/.config/cubby/config.toml)")
    p_init.add_argument("--force", action="store_true", help="replace an existing file")
    p_init.set_defaults(func=cmd_init)

    p_doctor = sub.add_parser("doctor", help="report environment and extraction support")
    _add_common_flags(p_doctor)
    p_doctor.set_defaults(func=cmd_doctor)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Run one cubby command.

    Args:
        argv: Arguments to parse. Defaults to ``sys.argv[1:]``.

    Returns:
        :data:`EXIT_OK`, :data:`EXIT_FAILED` or :data:`EXIT_BAD_CONFIG`.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.func is None:
        print(banner(_palette()))
        parser.print_help()
        return EXIT_OK
    try:
        exit_code: int = args.func(args)
    except tomllib.TOMLDecodeError as exc:
        # Before ValueError: TOMLDecodeError is one, and the file it came from
        # is the useful half of the message when three locations are possible.
        print(
            f"cubby: config error: {_config_path(args)} is not valid TOML: {exc}",
            file=sys.stderr,
        )
        return EXIT_BAD_CONFIG
    except ValueError as exc:
        # A setting cubby cannot act on. The message names it; a traceback
        # would not tell the user which line of their file to correct.
        print(f"cubby: config error: {exc}", file=sys.stderr)
        return EXIT_BAD_CONFIG
    except ServiceError as exc:
        print(f"cubby: {exc}", file=sys.stderr)
        return EXIT_FAILED
    except OSError as exc:
        print(f"cubby: {exc}", file=sys.stderr)
        return EXIT_FAILED
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
