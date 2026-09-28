"""The argument parser and the entry point."""

from __future__ import annotations

import argparse
import sys
import tomllib
from typing import TypeAlias

from .. import __version__
from ..adapters.service import (
    ServiceError,
)
from ..adapters.ui import banner
from .agent import cmd_doctor, cmd_install, cmd_status, cmd_uninstall
from .common import (
    EXIT_BAD_CONFIG,
    EXIT_FAILED,
    EXIT_OK,
    at_least_one,
    config_path_of,
    palette,
    positive_duration,
)
from .inspect import cmd_explain, cmd_history, cmd_init, cmd_log
from .sorting import cmd_pause, cmd_plan, cmd_resume, cmd_run, cmd_undo, cmd_watch

#: What ``add_subparsers`` returns; argparse does not name the type publicly.
Subcommands: TypeAlias = "argparse._SubParsersAction[argparse.ArgumentParser]"


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

    _add_sorting_commands(sub)
    _add_agent_commands(sub)
    _add_inspection_commands(sub)
    _add_control_commands(sub)

    return parser


def _add_sorting_commands(sub: Subcommands) -> None:
    """Subcommands to sort, watch and take back."""
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


def _add_agent_commands(sub: Subcommands) -> None:
    """Subcommands to the background agent."""
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


def _add_inspection_commands(sub: Subcommands) -> None:
    """Subcommands to explain, look back and set up."""
    p_explain = sub.add_parser(
        "explain", help="say where files would go and which rule decides (moves nothing)"
    )
    _add_common_flags(p_explain)
    p_explain.add_argument("files", nargs="+", metavar="FILE", help="files to explain")
    p_explain.add_argument("--json", action="store_true", help="output as JSON")
    p_explain.set_defaults(func=cmd_explain)

    p_history = sub.add_parser("history", help="list recent runs, and which were undone")
    p_history.add_argument(
        "-n", "--limit", type=at_least_one, default=20, help="how many runs (20)"
    )
    p_history.add_argument("--json", action="store_true", help="output as JSON")
    p_history.set_defaults(func=cmd_history)

    p_log = sub.add_parser("log", help="show what the agent logged (--run, --warnings)")
    p_log.add_argument("-n", "--lines", type=at_least_one, default=20, help="how many lines (20)")
    p_log.add_argument("--run", help="only the lines of this run, as listed by 'cubby history'")
    p_log.add_argument("--warnings", action="store_true", help="only warnings and errors")
    p_log.add_argument("--json", action="store_true", help="output the records as JSON lines")
    p_log.set_defaults(func=cmd_log)

    p_init = sub.add_parser("init", help="write a starter config file")
    p_init.add_argument("--path", help="where to write it (default: ~/.config/cubby/config.toml)")
    p_init.add_argument("--force", action="store_true", help="replace an existing file")
    p_init.set_defaults(func=cmd_init)


def _add_control_commands(sub: Subcommands) -> None:
    """Subcommands to check and hold the agent."""
    p_doctor = sub.add_parser("doctor", help="report environment and extraction support")
    _add_common_flags(p_doctor)
    p_doctor.add_argument(
        "--notify", action="store_true", help="send a test notification and report the result"
    )
    p_doctor.set_defaults(func=cmd_doctor)

    p_pause = sub.add_parser("pause", help="stop the agent moving files, without uninstalling it")
    p_pause.add_argument(
        "--for",
        dest="duration",
        type=positive_duration,
        help="resume by itself after this long, e.g. 2h (default: until 'cubby resume')",
    )
    p_pause.set_defaults(func=cmd_pause)

    p_resume = sub.add_parser("resume", help="let a paused agent sort again")
    p_resume.set_defaults(func=cmd_resume)


def _tolerate_undecodable_names() -> None:
    """Print any file name, even one that is not valid UTF-8.

    Linux allows such names and Python holds them as surrogate characters,
    which a strict stream refuses: the report of a run that has already moved
    the file would crash. They are shown with backslash escapes instead.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="backslashreplace")


def main(argv: list[str] | None = None) -> int:
    """Run one cubby command.

    Args:
        argv: Arguments to parse. Defaults to ``sys.argv[1:]``.

    Returns:
        :data:`EXIT_OK`, :data:`EXIT_FAILED` or :data:`EXIT_BAD_CONFIG`.
    """
    _tolerate_undecodable_names()
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.func is None:
        print(banner(palette()))
        parser.print_help()
        return EXIT_OK
    try:
        exit_code: int = args.func(args)
    except tomllib.TOMLDecodeError as exc:
        # Before ValueError: TOMLDecodeError is one, and the file it came from
        # is the useful half of the message when three locations are possible.
        print(
            f"cubby: config error: {config_path_of(args)} is not valid TOML: {exc}",
            file=sys.stderr,
        )
        return EXIT_BAD_CONFIG
    except (OSError, UnicodeError) as exc:
        # UnicodeError before ValueError, which it subclasses: a name or a text
        # cubby could not encode is not a setting to correct.
        print(f"cubby: {exc}", file=sys.stderr)
        return EXIT_FAILED
    except ValueError as exc:
        # A setting cubby cannot act on. The message names it; a traceback
        # would not tell the user which line of their file to correct.
        print(f"cubby: config error: {exc}", file=sys.stderr)
        return EXIT_BAD_CONFIG
    except ServiceError as exc:
        print(f"cubby: {exc}", file=sys.stderr)
        return EXIT_FAILED
    return exit_code
