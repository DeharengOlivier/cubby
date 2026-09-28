"""Every process cubby starts has a timeout, and this test is what holds that.

A child that never returns (a converter stuck on a crafted file, a service
manager that hangs) must not freeze the agent. Reviews caught each call so
far; this AST check makes a new call without ``timeout=`` fail CI instead
(CODING-RULES principle 11: a rule held by review alone is not a control).
"""

from __future__ import annotations

import ast
from pathlib import Path

SOURCE = Path(__file__).resolve().parent.parent / "src" / "cubby"

#: Calls that start a process or open a connection and accept ``timeout=``.
NEEDS_TIMEOUT = {"run", "check_output", "check_call", "call", "communicate", "urlopen"}
#: Calls with no timeout at all: never allowed.
FORBIDDEN = {
    ("os", "system"),
    ("os", "popen"),
    ("subprocess", "Popen"),
    ("subprocess", "getoutput"),
}


def _calls(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            owner = node.func.value
            yield node, (owner.id if isinstance(owner, ast.Name) else None), node.func.attr


def _violations(source: str, filename: str) -> list[str]:
    found = []
    for node, owner, attr in _calls(ast.parse(source)):
        where = f"{filename}:{node.lineno}"
        if (owner, attr) in FORBIDDEN:
            found.append(f"{where}: {owner}.{attr} cannot be bounded")
        elif (
            owner in {"subprocess", "urllib", "request"}
            and attr in NEEDS_TIMEOUT
            and not any(k.arg == "timeout" for k in node.keywords)
        ):
            found.append(f"{where}: {owner}.{attr} without timeout=")
    # ``from subprocess import run`` would hide a call from the check above.
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module in {"subprocess", "urllib.request"}:
            found.append(f"{filename}:{node.lineno}: import the module, not its functions")
    return found


def test_every_process_call_in_cubby_has_a_timeout():
    violations = [
        v
        for path in sorted(SOURCE.rglob("*.py"))
        for v in _violations(path.read_text(encoding="utf-8"), str(path.relative_to(SOURCE)))
    ]
    assert violations == []


def test_the_check_sees_the_calls_it_is_meant_to_catch():
    assert _violations("import subprocess\nsubprocess.run(['x'])\n", "f.py") == [
        "f.py:2: subprocess.run without timeout="
    ]
    assert _violations("import os\nos.system('x')\n", "f.py") == [
        "f.py:2: os.system cannot be bounded"
    ]
    assert _violations("import subprocess\nsubprocess.run(['x'], timeout=5)\n", "f.py") == []
    assert _violations("from subprocess import run\n", "f.py") == [
        "f.py:1: import the module, not its functions"
    ]
