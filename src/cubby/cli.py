"""Command-line entry point. Thin: it parses args and wires app + adapters."""
from __future__ import annotations

import argparse
from pathlib import Path

from . import __version__
from .adapters.config import find_user_config, load_config
from .adapters.logging import DEFAULT_LOG, file_logger
from .app.report import render_plan
from .app.sorter import Sorter
from .domain.category import Config


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
    outcomes = Sorter(config, log=log).sort_once(apply=True)
    print(render_plan(outcomes, applied=True))
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

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
