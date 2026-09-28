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
NEEDS_TIMEOUT = {
    "subprocess.run",
    "subprocess.check_output",
    "subprocess.check_call",
    "subprocess.call",
    "urllib.request.urlopen",
    "request.urlopen",
}
#: A method that waits on a child, whatever the object is called.
NEEDS_TIMEOUT_METHODS = {"communicate", "wait"}
#: Calls with no timeout at all: never allowed.
FORBIDDEN = {
    "os.system",
    "os.popen",
    "subprocess.Popen",
    "subprocess.getoutput",
    "subprocess.getstatusoutput",
}
FORBIDDEN_PREFIXES = ("os.spawn", "os.posix_spawn")
#: Modules that must be imported whole and under their own name, or the dotted
#: names above would miss the calls (``from subprocess import run``, ``as sp``).
WATCHED_MODULES = {"subprocess", "urllib", "urllib.request"}


def _dotted(node: ast.expr) -> str | None:
    """``a.b.c`` for a chain of names and attributes, else None."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        owner = _dotted(node.value)
        return f"{owner}.{node.attr}" if owner else None
    return None


def _has_timeout(call: ast.Call) -> bool:
    return any(
        k.arg == "timeout" and not (isinstance(k.value, ast.Constant) and k.value.value is None)
        for k in call.keywords
    )


def _call_violation(node: ast.Call) -> str | None:
    name = _dotted(node.func)
    if name in FORBIDDEN or (name and name.startswith(FORBIDDEN_PREFIXES)):
        return f"{name} cannot be bounded"
    method = node.func.attr if isinstance(node.func, ast.Attribute) else None
    if (name in NEEDS_TIMEOUT or method in NEEDS_TIMEOUT_METHODS) and not _has_timeout(node):
        return f"{name or method} without timeout="
    return None


def _import_violation(node: ast.AST) -> str | None:
    if isinstance(node, ast.ImportFrom) and node.module in WATCHED_MODULES:
        return "import the module, not its functions"
    if isinstance(node, ast.Import):
        for alias in node.names:
            if alias.name in WATCHED_MODULES and alias.asname:
                return f"import {alias.name} under its own name"
    return None


def _violations(source: str, filename: str) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(source)):
        problem = _call_violation(node) if isinstance(node, ast.Call) else _import_violation(node)
        if problem:
            found.append(f"{filename}:{node.lineno}: {problem}")
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


def test_the_check_sees_what_the_first_version_missed():
    # Blind spots found by the review of PR 7.
    assert _violations("import urllib.request\nurllib.request.urlopen('u')\n", "f.py") == [
        "f.py:2: urllib.request.urlopen without timeout="
    ]
    assert _violations("import subprocess as sp\n", "f.py") == [
        "f.py:1: import subprocess under its own name"
    ]
    assert _violations("import subprocess\nsubprocess.run(['x'], timeout=None)\n", "f.py") == [
        "f.py:2: subprocess.run without timeout="
    ]
    assert _violations("import os\nos.spawnv(os.P_WAIT, 'x', ['x'])\n", "f.py") == [
        "f.py:2: os.spawnv cannot be bounded"
    ]
    assert _violations("child.communicate()\n", "f.py") == [
        "f.py:1: child.communicate without timeout="
    ]
