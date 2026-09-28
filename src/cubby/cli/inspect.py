"""Commands that explain, list and set up: explain, history, log, init."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ..adapters import config as config_module
from ..adapters.config import (
    default_user_config_path,
    write_starter_config,
)
from ..adapters.journal import Journal
from ..adapters.ledger import Ledger
from ..adapters.logging import human_line, read_all
from ..adapters.ui import Palette, Shown, dumps_for_terminal, os_error_text
from ..adapters.ui import escape_for_terminal as shown
from ..app.explain import Explanation, PlannedPass, explain
from ..app.history import recent_runs
from .common import (
    EXIT_FAILED,
    EXIT_OK,
    kv,
    load_from_args,
    palette,
)


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
        "duplicate_of": str(item.duplicate_of) if item.duplicate_of else None,
        "error": item.error,
        "content_chars": item.content_chars,
    }


def _print_explanation(pal: Palette, item: Explanation, source: Path) -> None:
    print(pal.bold(shown(str(item.path))))
    # What a run does comes first; where the file would go otherwise, after.
    stays = "not in the watched folder: a run would not see it" if item.outside else item.skipped
    if stays:
        kv(pal, "  stays where it is", pal.yellow(shown(stays)), _WIDTH)
    destination = shown(_near(item.destination, source))
    if item.error is not None and not stays:
        kv(pal, "  would fail", pal.yellow(shown(item.error)), _WIDTH)
    elif item.duplicate_of is not None and not stays:
        where = f"as a duplicate of {shown(_near(item.duplicate_of, source))}"
        kv(pal, "  deleted", pal.accent(Shown(where)), _WIDTH)
    elif not stays:
        kv(pal, "  goes to", pal.accent(destination), _WIDTH)
    elif item.sortable or item.outside:  # never sorted: no destination to announce
        kv(pal, "  would go to", pal.accent(destination), _WIDTH)
    rule = (
        f"{shown(item.rule)}  ({shown(item.stage.value)} stage)" if item.rule else "no rule matched"
    )
    kv(pal, "  decided by", Shown(rule), _WIDTH)
    if item.content_chars is not None:
        read = (
            f"read ({item.content_chars:d} characters), no content pattern matched"
            if item.content_chars
            else "no text could be read from it"
        )
        kv(pal, "  content", pal.dim(Shown(read)), _WIDTH)
    if item.renamed_to:
        kv(pal, "  renamed", shown(item.renamed_to), _WIDTH)


#: The key column of ``explain``: its longest key and two spaces.
_WIDTH = len("  stays where it is") + 2


def _near(path: Path, folder: Path) -> str:
    try:
        return str(path.relative_to(folder))
    except ValueError:
        return str(path)


def cmd_explain(args: argparse.Namespace) -> int:
    config = load_from_args(args)
    items: list[Explanation] = []
    missing = False
    planned = PlannedPass(config)
    for raw in args.files:
        try:
            items.append(explain(Path(raw).expanduser(), config, planned))
        except FileNotFoundError:
            print(f"cubby: no such file: {shown(raw)}", file=sys.stderr)
            missing = True
    if getattr(args, "json", False):
        payload = {"version": 1, "items": [_explanation_json(i) for i in items]}
        print(dumps_for_terminal(payload))
    else:
        pal = palette()
        for item in items:
            _print_explanation(pal, item, config.settings.source)
    return EXIT_FAILED if missing else EXIT_OK


def cmd_history(args: argparse.Namespace) -> int:
    runs = recent_runs(Ledger(), Journal(), limit=args.limit)
    if getattr(args, "json", False):
        payload = {
            "version": 1,
            "runs": [{**r.record.to_json(), "undone": r.undone, "undo": r.undo} for r in runs],
        }
        print(dumps_for_terminal(payload))
        return EXIT_OK
    pal = palette()
    if not runs:
        print(pal.dim("No runs recorded yet."))
        return EXIT_OK
    for summary in runs:
        record = summary.record
        counts = f"moved {record.moved:d}"
        if record.failed:
            counts += pal.yellow(f", {record.failed:d} failed")
        flag = "" if summary.undo == "undoable" else pal.dim(f"  {shown(summary.undo)}")
        finished, run, mode = shown(record.finished), shown(record.run), shown(record.mode)
        print(f"{finished}  {pal.accent(run)}  {mode:<5}  {counts}{flag}")
    print(pal.dim("\nUndo one with: cubby undo --run <id>"))
    return EXIT_OK


def cmd_log(args: argparse.Namespace) -> int:
    records = read_all()
    if not records and not args.run:
        if not args.json:  # --json prints no line at all: a script sees an empty list
            print(palette().dim("No log yet: the agent writes one once it runs."))
        return EXIT_OK
    if args.run:
        records = [r for r in records if r.get("run") == args.run]
        if not records:
            print(
                f"cubby: no log line for run {args.run!r} (the log keeps the most recent "
                f"lines only); see 'cubby history'",
                file=sys.stderr,
            )
            return EXIT_FAILED
    if args.warnings:
        # A line with no level is not cubby's own (a traceback the service
        # manager captured): shown, since it is rarely good news.
        records = [r for r in records if r.get("level") != "INFO"]
    for record in records[-args.lines :]:
        print(json.dumps(record) if args.json else human_line(record))
    return EXIT_OK


def cmd_init(args: argparse.Namespace) -> int:
    target = Path(args.path).expanduser() if args.path else default_user_config_path()
    in_use = config_module.find_user_config()
    if in_use is not None and in_use != target and not args.force:
        print(
            f"cubby: cubby already reads {shown(str(in_use))}; a new file at "
            f"{shown(str(target))} would change which config is used. Edit that one, "
            "or use --force to write anyway.",
            file=sys.stderr,
        )
        return EXIT_FAILED
    try:
        write_starter_config(target, force=args.force)
    except FileExistsError:
        print(
            f"cubby: {shown(str(target))} already exists; use --force to replace it",
            file=sys.stderr,
        )
        return EXIT_FAILED
    except OSError as error:
        print(
            f"cubby: could not write {shown(str(target))}: {shown(os_error_text(error))}",
            file=sys.stderr,
        )
        return EXIT_FAILED
    print(f"Wrote a starter config to {shown(str(target))}")
    print("Preview what it does with: cubby plan")
    return EXIT_OK
