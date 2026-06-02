"""Command-line entry point. Thin: it parses args and wires app + adapters."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from . import __version__
from .adapters.config import find_user_config, load_config
from .adapters.extraction import PARSABLE
from .adapters.journal import Journal
from .adapters.logging import DEFAULT_LOG, file_logger
from .adapters.service import ServiceSpec, detect_service, get_service
from .app.report import render_plan
from .app.sorter import Sorter
from .app.undo import undo_last_run
from .app.watcher import Watcher
from .domain.category import Config
from .domain.duration import format_duration


def _build_overrides(args: argparse.Namespace) -> dict:
    settings: dict = {}
    if getattr(args, "source", None):
        settings["source"] = args.source
    if getattr(args, "delay", None) is not None:
        settings["delay"] = args.delay
    if getattr(args, "interval", None) is not None:
        settings["interval"] = args.interval
    if getattr(args, "no_content", False):
        settings["content_scan"] = False
    return {"settings": settings} if settings else {}


def _load(args: argparse.Namespace) -> Config:
    user_path = Path(args.config).expanduser() if getattr(args, "config", None) else None
    return load_config(user_path=user_path, overrides=_build_overrides(args))


def cmd_plan(args: argparse.Namespace) -> int:
    config = _load(args)
    outcomes = Sorter(config).sort_once(apply=False, respect_age=False)
    print(render_plan(outcomes, applied=False))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    config = _load(args)
    log = file_logger(echo=args.verbose)
    outcomes = Sorter(config, log=log, journal=Journal()).sort_once(apply=True)
    print(render_plan(outcomes, applied=True))
    return 0


def cmd_undo(args: argparse.Namespace) -> int:
    restored = undo_last_run(Journal(), log=print)
    print(f"Restored {restored} file(s).")
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    config = _load(args)
    log = file_logger(echo=True)
    sorter = Sorter(config, log=log)
    watcher = Watcher(sorter, config.settings.interval, log=log)
    log(
        f"cubby watching {config.settings.source} "
        f"(delay {format_duration(config.settings.delay)}, "
        f"every {format_duration(config.settings.interval)})"
    )
    try:
        watcher.run()
    except KeyboardInterrupt:
        log("cubby stopped")
    return 0


def _program_args(args: argparse.Namespace) -> list[str]:
    """The command the background service should run: ``cubby watch ...``.

    The flags given to ``install`` are baked into the agent so it sorts with the
    same settings the user asked for.
    """
    exe = shutil.which("cubby")
    base = [exe] if exe else [sys.executable, "-m", "cubby"]
    base.append("watch")
    if getattr(args, "config", None):
        base += ["--config", str(Path(args.config).expanduser().resolve())]
    if getattr(args, "source", None):
        base += ["--source", str(Path(args.source).expanduser().resolve())]
    if getattr(args, "delay", None) is not None:
        base += ["--delay", str(args.delay)]
    if getattr(args, "interval", None) is not None:
        base += ["--interval", str(args.interval)]
    return base


def cmd_install(args: argparse.Namespace) -> int:
    config = _load(args)
    service = get_service()
    spec = ServiceSpec(program_args=_program_args(args), log_path=DEFAULT_LOG)
    path = service.install(spec)
    print(f"Installed {service.name} agent: {path}")
    print(
        f"Cubby will watch {config.settings.source} "
        f"(delay {format_duration(config.settings.delay)}). Logs: {DEFAULT_LOG}"
    )
    return 0


def cmd_uninstall(args: argparse.Namespace) -> int:
    service = detect_service()
    if service is None or not service.uninstall():
        print("No cubby agent was installed.")
        return 0
    print(f"Removed the {service.name} agent.")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    config = _load(args)
    service = detect_service()
    print(f"cubby {__version__}")
    print(f"platform        : {sys.platform}")
    print(f"service backend : {service.name if service else 'none (manual watch only)'}")
    print(f"config file     : {find_user_config() or 'defaults only'}")
    print(f"source folder   : {config.settings.source}")
    tools = {name: bool(shutil.which(name)) for name in ("pdftotext", "textutil", "antiword")}
    libs = {}
    for lib in ("pypdf", "docx", "openpyxl"):
        try:
            __import__(lib)
            libs[lib] = True
        except Exception:
            libs[lib] = False
    print(f"extract tools   : {tools}")
    print(f"extract libs    : {libs}")
    print(f"parsable types  : {', '.join(sorted(PARSABLE))}")
    return 0


def _add_common_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", help="path to a config file")
    parser.add_argument("--source", help="folder to sort (default: from config)")
    parser.add_argument("--delay", help="min age before moving a file, e.g. 1m, 30s")
    parser.add_argument("--interval", help="watch poll interval, e.g. 30s")
    parser.add_argument("--no-content", action="store_true", help="disable content scanning")
    parser.add_argument("-v", "--verbose", action="store_true", help="echo actions")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cubby", description="Tidy your Downloads folder.")
    parser.add_argument("--version", action="version", version=f"cubby {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_plan = sub.add_parser("plan", help="show where files would go (moves nothing)")
    _add_common_flags(p_plan)
    p_plan.set_defaults(func=cmd_plan)

    p_run = sub.add_parser("run", help="sort the folder once")
    _add_common_flags(p_run)
    p_run.set_defaults(func=cmd_run)

    p_watch = sub.add_parser("watch", help="keep sorting the folder in the foreground")
    _add_common_flags(p_watch)
    p_watch.set_defaults(func=cmd_watch)

    p_undo = sub.add_parser("undo", help="revert the most recent run")
    p_undo.set_defaults(func=cmd_undo)

    p_install = sub.add_parser("install", help="install the background agent (auto-start)")
    _add_common_flags(p_install)
    p_install.set_defaults(func=cmd_install)

    p_uninstall = sub.add_parser("uninstall", help="remove the background agent")
    p_uninstall.set_defaults(func=cmd_uninstall)

    p_doctor = sub.add_parser("doctor", help="report environment and extraction support")
    _add_common_flags(p_doctor)
    p_doctor.set_defaults(func=cmd_doctor)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
