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
from ..adapters.ui import Palette
from ..app.explain import Explanation, explain
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
    }


def _print_explanation(pal: Palette, item: Explanation, source: Path) -> None:
    print(pal.bold(str(item.path)))
    try:
        shown = item.destination.relative_to(source)
    except ValueError:
        shown = item.destination
    kv(pal, "  goes to", pal.accent(str(shown)))
    rule = f"{item.rule}  ({item.stage.value} stage)" if item.rule else "no rule matched"
    kv(pal, "  decided by", rule)
    if item.renamed_to:
        kv(pal, "  renamed", item.renamed_to)
    if item.outside:
        kv(pal, "  note", pal.yellow("not in the watched folder: a run would not see it"))
    elif item.skipped:
        kv(pal, "  left alone", pal.yellow(item.skipped))


def cmd_explain(args: argparse.Namespace) -> int:
    config = load_from_args(args)
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
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return EXIT_OK
    pal = palette()
    if not runs:
        print(pal.dim("No runs recorded yet."))
        return EXIT_OK
    for summary in runs:
        record = summary.record
        counts = f"moved {record.moved}"
        if record.failed:
            counts += pal.yellow(f", {record.failed} failed")
        flag = "" if summary.undo == "undoable" else pal.dim(f"  {summary.undo}")
        print(f"{record.finished}  {pal.accent(record.run)}  {record.mode:<5}  {counts}{flag}")
    print(pal.dim("\nUndo one with: cubby undo --run <id>"))
    return EXIT_OK


def cmd_log(args: argparse.Namespace) -> int:
    records = read_all()
    if not records:
        if not args.json:  # --json prints no line at all: a script sees an empty list
            print(palette().dim("No log yet: the agent writes one once it runs."))
        return EXIT_OK
    if args.run:
        records = [r for r in records if r.get("run") == args.run]
        if not records:
            print(f"cubby: no log line for run {args.run!r}; see 'cubby history'", file=sys.stderr)
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
            f"cubby: cubby already reads {in_use}; a new file at {target} would change "
            "which config is used. Edit that one, or use --force to write anyway.",
            file=sys.stderr,
        )
        return EXIT_FAILED
    try:
        write_starter_config(target, force=args.force)
    except FileExistsError:
        print(f"cubby: {target} already exists; use --force to replace it", file=sys.stderr)
        return EXIT_FAILED
    except OSError as error:
        print(f"cubby: could not write {target}: {error}", file=sys.stderr)
        return EXIT_FAILED
    print(f"Wrote a starter config to {target}")
    print("Preview what it does with: cubby plan")
    return EXIT_OK
