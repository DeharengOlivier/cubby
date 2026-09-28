"""No text reaches a terminal unless it is known to be escaped, and this test holds that.

A file name is untrusted text (``tests/test_terminal_escape.py`` says why).
Those behavior tests pin each sink that exists; this check covers the sinks
still to be written, so that a new ``print`` of a file name fails CI instead
of passing it (CODING-RULES principle 11: a rule held by review alone is not
a control).

Two mechanisms share the work. mypy holds what a type can: the escapers
return ``Shown``, a ``Palette`` keeps it, and ``kv`` and the renderers ask
for it or return it. ``# type: ignore`` is banned in ``src`` (below) and
``typing.cast`` by ruff (TID251), so mypy cannot be told to look away. This
AST check holds the rest, including ``Any``, which mypy lets through: every
argument of a ``print``, of a write to ``sys.stdout`` or ``sys.stderr``, of
``sys.exit`` or ``SystemExit``, of a ``Shown(...)`` construction (under any
alias) and of a cubby function's parameter annotated ``Shown`` (``kv``'s
value) must be an expression known to be safe:

- a literal; ``len``, ``int``, ``round`` or a cubby function annotated to
  return a number; arithmetic other than ``+``, ``%`` and ``*`` (never a
  text); ``json.dumps`` with its default ``ensure_ascii``;
- an f-string whose every value is safe, or is formatted with ``!r`` or
  ``!a`` (``repr`` escapes every character that is not printable), or with a
  number-only format such as ``:d`` or ``.3f`` (a text would raise);
- ``+``, ``or``, ``x if c else y``, a list, a tuple, an index or a
  comprehension over safe parts;
- ``*`` of a string literal (the other side is a count), or of safe parts;
- a call to a function of cubby whose every ``return`` and ``yield`` is safe
  (``-> Shown`` included: the annotation is not taken on trust), to
  a palette method over one safe text, or to ``join``, ``strip`` or
  ``ljust`` on safe text;
- a parameter annotated ``Shown`` (its callers are checked), or a local name
  whose every assignment, ``+=``, ``append``, ``extend`` and loop over is
  safe. Binding a sink to another name (``say = print``, ``S = Shown``,
  ``stream = sys.stderr``) is itself refused.

Anything else, an attribute read, a loop over objects, a parameter typed
``str``, is text the check cannot vouch for: pass it through ``shown(...)``.

Known limits, none used in ``src`` today: a sink passed as a value rather
than called or assigned (``log=print``, ``file=stream`` of an opened
terminal); a ``.write`` on a stream obtained another way than ``sys``;
palette methods and cubby functions are recognised by name, and a method
of a class by the same name as a function is judged with it; ``!r``, ``!a``
and number-only formats trust ``__repr__`` and ``__format__``; a violation
is reported where the text reaches a sink, not where it entered.
"""

from __future__ import annotations

import ast
import tokenize
from collections import defaultdict
from pathlib import Path

SOURCE = Path(__file__).resolve().parent.parent / "src" / "cubby"

#: Modules that never write to a person's terminal: the parser child's output
#: is captured by the parent, which logs it (shown escaped when read back).
NOT_A_TERMINAL = {"adapters/parsers.py"}
#: Where text becomes ``Shown``: the escapers themselves.
ESCAPERS = {"escape_for_terminal", "dumps_for_terminal"}
STREAM_WRITES = {"sys.stdout.write", "sys.stderr.write"}
#: Calls that print their argument when it is a text.
EXITS = {"sys.exit", "SystemExit", "exit", "quit"}
CASTS = {"cast", "typing.cast", "typing_extensions.cast"}
#: Names that must not be bound to another name, or the calls above would hide.
SINKS = {"print", "Shown", "sys.stdout", "sys.stderr", *STREAM_WRITES, *EXITS, *CASTS}
PALETTE_METHODS = {"bold", "dim", "accent", "cyan", "green", "yellow"}
#: ``str`` methods whose result is safe when the text they are called on is.
STRIP_METHODS = {"strip", "lstrip", "rstrip"}
PAD_METHODS = {"ljust", "rjust", "center"}  # the fill character must be safe too
#: Calls that yield the items of their safe arguments, and calls that yield a number.
ITERATORS = {"zip", "sorted", "reversed"}
NUMBERS = {"len", "int", "round"}
NUMBER_TYPES = {"int", "float", "bool"}
#: Format types only a number accepts: ``format("x", "d")`` raises.
NUMBER_FORMATS = set("bcdeEfFgGnoxX%")

Scope = ast.FunctionDef | ast.AsyncFunctionDef | ast.Module

#: The parts an expression is made of: it is safe when they all are.
PARTS = {
    ast.JoinedStr: lambda node: node.values,
    ast.BinOp: lambda node: [node.left, node.right],  # +, % or *
    ast.BoolOp: lambda node: node.values,
    ast.IfExp: lambda node: [node.body, node.orelse],  # the condition is not shown
    ast.List: lambda node: node.elts,
    ast.Tuple: lambda node: node.elts,
    ast.ListComp: lambda node: [node.elt],
    ast.GeneratorExp: lambda node: [node.elt],
    ast.Subscript: lambda node: [node.value],  # an item of a safe list
}


def _dotted(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        owner = _dotted(node.value)
        return f"{owner}.{node.attr}" if owner else None
    return None


def _is_shown_name(name: str | None) -> bool:
    return name is not None and (name == "Shown" or name.endswith(".Shown"))


def _number_only(spec: ast.JoinedStr | None) -> bool:
    if spec is None or not all(isinstance(v, ast.Constant) for v in spec.values):
        return False
    text = "".join(str(v.value) for v in spec.values if isinstance(v, ast.Constant))
    return bool(text) and text[-1] in NUMBER_FORMATS


class Checker:
    """Decides which expressions of a set of modules are safe to show."""

    def __init__(self, modules: dict[str, ast.Module]):
        self.modules = modules
        self.functions: dict[str, list[ast.FunctionDef | ast.AsyncFunctionDef]] = defaultdict(list)
        self.scope_of: dict[ast.AST, Scope] = {}
        self.module_of: dict[Scope, ast.Module] = {}
        #: ``shown`` for ``escape_for_terminal as shown``, per module.
        self.aliases: dict[ast.Module, dict[str, str]] = {}
        for tree in modules.values():
            self.module_of[tree] = tree
            self.aliases[tree] = {
                alias.asname: alias.name
                for node in ast.walk(tree)
                if isinstance(node, ast.Import | ast.ImportFrom)
                for alias in node.names
                if alias.asname
            }
            self._index(tree, tree)
        self._functions_seen: dict[str, bool] = {}
        self._names_seen: dict[tuple[int, str], bool] = {}

    def _index(self, node: ast.AST, scope: Scope) -> None:
        for child in ast.iter_child_nodes(node):
            self.scope_of[child] = scope
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                self.functions[child.name].append(child)
                self.module_of[child] = self.module_of[scope]
                self._index(child, child)
            else:
                self._index(child, scope)

    def violations(self) -> list[str]:
        found = []
        for filename, tree in sorted(self.modules.items()):
            for node in ast.walk(tree):
                binding = isinstance(node, ast.Assign | ast.AnnAssign | ast.NamedExpr)
                if binding and node.value and self._resolve(node.value, tree) in SINKS:
                    found.append(f"{filename}:{node.lineno}: {ast.unparse(node)} (a sink)")
                if not isinstance(node, ast.Call):
                    continue
                for arg in self._shown_arguments(node):
                    if not self.safe(arg, self.scope_of[node]):
                        found.append(f"{filename}:{arg.lineno}: {ast.unparse(arg)}")
        return found

    def _resolve(self, node: ast.expr, module: ast.Module) -> str | None:
        """The dotted name ``node`` stands for, through ``import ... as`` aliases."""
        name = _dotted(node)
        if name is None:
            return None
        head, _, rest = name.partition(".")
        head = self.aliases[module].get(head, head)
        return f"{head}.{rest}" if rest else head

    def _is_shown(self, annotation: ast.expr | None, scope: Scope) -> bool:
        if isinstance(annotation, ast.Constant):  # a quoted annotation
            return _is_shown_name(str(annotation.value))
        module = self.module_of[scope]
        return annotation is not None and _is_shown_name(self._resolve(annotation, module))

    def _shown_arguments(self, call: ast.Call) -> list[ast.expr]:
        scope = self.scope_of[call]
        name = self._resolve(call.func, self.module_of[scope])
        if name == "print":
            return [*call.args, *(k.value for k in call.keywords if k.arg in {"sep", "end"})]
        if name in STREAM_WRITES or name in EXITS:
            return list(call.args)
        if name in CASTS:
            return call.args[1:] if call.args and self._is_shown(call.args[0], scope) else []
        if _is_shown_name(name):
            in_escaper = not isinstance(scope, ast.Module) and scope.name in ESCAPERS
            return [] if in_escaper else [*call.args, *(k.value for k in call.keywords)]
        return self._shown_parameters(call, name)

    def _shown_parameters(self, call: ast.Call, name: str | None) -> list[ast.expr]:
        """The arguments given to parameters annotated ``Shown`` of a cubby function."""
        found = []
        for definition in self.functions.get(name or "", []):
            arguments = definition.args
            positional = [*arguments.posonlyargs, *arguments.args]
            by_name = {a.arg: a for a in [*positional, *arguments.kwonlyargs]}
            scope = definition
            found += [
                value
                for parameter, value in zip(positional, call.args, strict=False)
                if self._is_shown(parameter.annotation, scope)
            ]
            found += [
                k.value
                for k in call.keywords
                if k.arg in by_name and self._is_shown(by_name[k.arg].annotation, scope)
            ]
        return found

    def safe(self, node: ast.expr, scope: Scope) -> bool:
        if isinstance(node, ast.Constant):
            return isinstance(node.value, str | int | float)
        if isinstance(node, ast.FormattedValue):
            spec = node.format_spec if isinstance(node.format_spec, ast.JoinedStr) else None
            repr_like = node.conversion in (ord("r"), ord("a"))
            return repr_like or _number_only(spec) or self.safe(node.value, scope)
        if isinstance(node, ast.BinOp) and _safe_whatever_its_parts(node):
            return True
        if isinstance(node, ast.Call):
            return self._safe_call(node, scope)
        if isinstance(node, ast.Name):
            return self._safe_name(node.id, scope)
        parts = PARTS.get(type(node))
        return parts is not None and all(self.safe(part, scope) for part in parts(node))

    def _safe_call(self, call: ast.Call, scope: Scope) -> bool:
        func = call.func
        if isinstance(func, ast.Name):
            return self._safe_function_call(call, func.id, scope)
        if _dotted(func) == "json.dumps":
            # ensure_ascii, the default, writes every character but printable ASCII as an escape.
            return all(k.arg != "ensure_ascii" for k in call.keywords)
        if not isinstance(func, ast.Attribute):
            return False
        if func.attr in PALETTE_METHODS:
            return len(call.args) == 1 and not call.keywords and self.safe(call.args[0], scope)
        arguments = _kept_arguments(func.attr, call)
        return (
            arguments is not None
            and self.safe(func.value, scope)
            and all(self.safe(arg, scope) for arg in arguments)
        )

    def _safe_function_call(self, call: ast.Call, called: str, scope: Scope) -> bool:
        name = self.aliases[self.module_of[scope]].get(called, called)
        if name in NUMBERS or _is_shown_name(name):
            return True  # the argument of Shown(...) is checked as a sink of its own
        if name in ITERATORS:
            return all(self.safe(arg, scope) for arg in call.args)
        return name in self.functions and self._safe_function(name)

    def _safe_function(self, name: str) -> bool:
        if name not in self._functions_seen:
            self._functions_seen[name] = True  # a recursive call does not decide
            self._functions_seen[name] = all(
                self._returns_safe(definition) for definition in self.functions[name]
            )
        return self._functions_seen[name]

    def _returns_safe(self, definition: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
        if _is_number(definition.returns):
            return True  # mypy holds every return of it (warn_return_any: no Any either)
        # ``-> Shown`` is not taken on trust: its returns are checked like any other.
        # What a generator yields is what it gives, as much as what it returns.
        returns = [
            node.value
            for node in ast.walk(definition)
            if isinstance(node, ast.Return | ast.Yield | ast.YieldFrom)
            and self.scope_of[node] is definition
        ]
        return all(value is not None and self.safe(value, definition) for value in returns)

    def _safe_name(self, name: str, scope: Scope) -> bool:
        key = (id(scope), name)
        if key not in self._names_seen:
            self._names_seen[key] = True  # ``x = x + ...`` does not decide
            self._names_seen[key] = self._bindings_safe(name, scope)
        return self._names_seen[key]

    def _bindings_safe(self, name: str, scope: Scope) -> bool:
        if not isinstance(scope, ast.Module):
            arguments = scope.args
            for arg in [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]:
                if arg.arg == name:
                    return self._is_shown(arg.annotation, scope)  # callers are checked
        verdicts = [
            self._binding_safe(node, name, scope)
            for node in ast.walk(scope)
            if self.scope_of.get(node) is scope
        ]
        bound = [verdict for verdict in verdicts if verdict is not None]
        if not bound:
            module = self.module_of[scope]
            return scope is not module and self._safe_name(name, module)
        return all(bound)

    def _binding_safe(self, node: ast.AST, name: str, scope: Scope) -> bool | None:
        """Whether ``node`` binds ``name`` to safe text; None when it does not bind it."""
        if isinstance(node, ast.Assign):
            verdicts = [self._assigned_safe(t, node.value, name, scope) for t in node.targets]
            bound = [v for v in verdicts if v is not None]
            return all(bound) if bound else None
        if isinstance(node, ast.AnnAssign | ast.AugAssign | ast.NamedExpr):
            return self._one_target_safe(node, name, scope)
        if isinstance(node, ast.For | ast.AsyncFor | ast.comprehension):
            # An item of a safe list is safe: ``for art, text in zip(SHELF, side)``.
            return self.safe(node.iter, scope) if _binds(node.target, name) else None
        if _grows(node, name):
            assert isinstance(node, ast.Call)
            return self.safe(node.args[-1], scope)
        return False if _binds_otherwise(node, name) else None

    def _one_target_safe(
        self, node: ast.AnnAssign | ast.AugAssign | ast.NamedExpr, name: str, scope: Scope
    ) -> bool | None:
        if not (isinstance(node.target, ast.Name) and node.target.id == name):
            return None
        # ``x: Shown = ...`` is checked too: mypy lets an ``Any`` in.
        return node.value is not None and self.safe(node.value, scope)

    def _assigned_safe(
        self, target: ast.expr, value: ast.expr, name: str, scope: Scope
    ) -> bool | None:
        # ``a, b = (safe for ...)``, ``a, b = safe, safe`` or ``a[i] = safe``: every part is safe.
        whole = target.value if isinstance(target, ast.Subscript) else target
        return self.safe(value, scope) if _binds(whole, name) else None


def _safe_whatever_its_parts(node: ast.BinOp) -> bool:
    if not isinstance(node.op, ast.Add | ast.Mod | ast.Mult):
        return True  # -, /, //: a number, never a text
    # "-" * width repeats a literal: the other side is a count, or it raises.
    text_literal = [
        isinstance(s, ast.Constant) and isinstance(s.value, str) for s in (node.left, node.right)
    ]
    return isinstance(node.op, ast.Mult) and any(text_literal)


def _is_number(annotation: ast.expr | None) -> bool:
    return isinstance(annotation, ast.Name) and annotation.id in NUMBER_TYPES


def _kept_arguments(method: str, call: ast.Call) -> list[ast.expr] | None:
    """The arguments whose text a ``str`` method puts in its result, None for another method."""
    if method == "join":
        return call.args
    if method in STRIP_METHODS:
        return []  # the characters to strip are not shown
    if method in PAD_METHODS:
        return call.args[1:]  # the fill character is shown, the width is not
    return None


def _grows(node: ast.AST, name: str) -> bool:
    """Whether ``node`` is ``name.append(...)``, ``name.extend(...)`` or ``name.insert(...)``."""
    if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
        return False
    owner = node.func.value
    grows = node.func.attr in {"append", "extend", "insert"}
    return grows and isinstance(owner, ast.Name) and owner.id == name and bool(node.args)


def _binds_otherwise(node: ast.AST, name: str) -> bool:
    """Whether ``node`` binds ``name`` to a value the check cannot see (import, with, except)."""
    if isinstance(node, ast.withitem):
        return node.optional_vars is not None and _binds(node.optional_vars, name)
    if isinstance(node, ast.Import | ast.ImportFrom):
        return name in {alias.asname or alias.name.split(".")[0] for alias in node.names}
    return isinstance(node, ast.ExceptHandler) and node.name == name


def _binds(target: ast.expr, name: str) -> bool:
    return any(isinstance(n, ast.Name) and n.id == name for n in ast.walk(target))


def _violations(sources: dict[str, str]) -> list[str]:
    return Checker({name: ast.parse(text) for name, text in sources.items()}).violations()


def test_nothing_reaches_a_terminal_unescaped():
    sources = {
        path.relative_to(SOURCE).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(SOURCE.rglob("*.py"))
    }
    shown_to_a_person = {name: text for name, text in sources.items() if name not in NOT_A_TERMINAL}
    assert _violations(shown_to_a_person) == []


def test_the_check_refuses_raw_text():
    assert _violations({"m.py": "def f(path):\n    print(path.name)\n"}) == ["m.py:2: path.name"]
    assert _violations({"m.py": "def f(o):\n    print(f'moved {o.name}')\n"}) == [
        "m.py:2: f'moved {o.name}'"
    ]
    assert _violations({"m.py": "import sys\ndef f(n):\n    sys.stderr.write(n)\n"}) == [
        "m.py:3: n"
    ]
    # A name is only as safe as every value it is given.
    loop = "def f(names):\n    for n in names:\n        print(n)\n"
    assert _violations({"m.py": loop}) == ["m.py:3: n"]
    grown = "def f(o):\n    lines = []\n    lines.append(o.name)\n    print('\\n'.join(lines))\n"
    assert _violations({"m.py": grown}) == ["m.py:4: '\\n'.join(lines)"]
    # A function is only as safe as every value it returns.
    helper = "def g(o):\n    return o.name\ndef f(o):\n    print(g(o))\n"
    assert _violations({"m.py": helper}) == ["m.py:4: g(o)"]
    # Shown(...) only wraps what the check vouches for.
    assert _violations({"m.py": "def f(o):\n    return Shown(o.name)\n"}) == ["m.py:2: o.name"]
    assert _violations({"m.py": "def f(o):\n    return cast(Shown, o.name)\n"}) == [
        "m.py:2: o.name"
    ]
    assert _violations({"ui.py": "def escape_for_terminal(t):\n    return Shown(t)\n"}) == []
    # A text padded or indexed is still a text.
    assert _violations({"m.py": "def f(o):\n    print(f'{o.name:>5}')\n"}) == [
        "m.py:2: f'{o.name:>5}'"
    ]
    changed = "def f(o):\n    lines = ['a']\n    lines[0] = o.name\n    print(lines[0])\n"
    assert _violations({"m.py": changed}) == ["m.py:4: lines[0]"]
    assert _violations({"m.py": "import json\nprint(json.dumps(v, ensure_ascii=False))\n"}) == [
        "m.py:2: json.dumps(v, ensure_ascii=False)"
    ]


def test_the_check_refuses_what_the_review_of_pr_32_got_past_it():
    # I1: an ``Any`` given to a parameter annotated Shown, by position or by name.
    kv = "def kv(pal, key, value: Shown): ...\n"
    assert _violations({"m.py": kv + "def f(a):\n    kv(1, 'k', a['unit'])\n"}) == [
        "m.py:3: a['unit']"
    ]
    assert _violations({"m.py": kv + "def f(a):\n    kv(1, 'k', value=a.x)\n"}) == ["m.py:3: a.x"]
    typed = "def f(a):\n    x: Shown = a['unit']\n    print(x)\n"
    assert _violations({"m.py": typed}) == ["m.py:3: x"]
    # I2: Shown under another name, and cast to it through an attribute.
    alias = "from ui import Shown as S\ndef f(o):\n    print(S(o.name))\n"
    assert _violations({"m.py": alias}) == ["m.py:3: o.name"]
    cast = "import typing as t\nimport ui\ndef f(o):\n    return t.cast(ui.Shown, o.name)\n"
    assert _violations({"m.py": cast}) == ["m.py:4: o.name"]
    # I4: a generator is only as safe as what it yields.
    names = "def g(xs):\n    for x in xs:\n        yield x\n"
    joined = names + "def f(xs):\n    print('\\n'.join(g(xs)))\n"
    assert _violations({"m.py": joined}) == ["m.py:5: '\\n'.join(g(xs))"]
    bare = "def g():\n    yield\ndef f():\n    print(g())\n"
    assert _violations({"m.py": bare}) == ["m.py:4: g()"]
    # M2: repetition, exits and sinks bound to another name.
    assert _violations({"m.py": "def f(o):\n    print(o.name * 1)\n"}) == ["m.py:2: o.name * 1"]
    exits = "import sys\ndef f(o):\n    sys.exit(o.name)\n    raise SystemExit(o.name)\n"
    assert _violations({"m.py": exits}) == ["m.py:3: o.name", "m.py:4: o.name"]
    aliased = "import sys\nsay = print\nstream = sys.stderr\n"
    assert _violations({"m.py": aliased}) == [
        "m.py:2: say = print (a sink)",
        "m.py:3: stream = sys.stderr (a sink)",
    ]
    # M4: a palette method styles one safe text, nothing else.
    assert _violations({"m.py": "def f(o):\n    print(o.name.bold())\n"}) == [
        "m.py:2: o.name.bold()"
    ]


def test_src_never_silences_mypy():
    # A used ``# type: ignore[arg-type]`` would let raw text into ``kv``; ruff
    # and mypy only ban the bare form. ``typing.cast`` is banned by ruff (TID251).
    silenced = [
        f"{path.relative_to(SOURCE)}:{token.start[0]}"
        for path in sorted(SOURCE.rglob("*.py"))
        for token in tokenize.generate_tokens(
            iter(path.read_text(encoding="utf-8").splitlines(keepends=True)).__next__
        )
        if token.type == tokenize.COMMENT and "type:" in token.string and "ignore" in token.string
    ]
    assert silenced == []


def test_the_check_accepts_what_is_escaped_or_not_a_text():
    accepted = """
def shown(text) -> Shown: ...
def g(o):
    return 'x' if o else shown(o.name)
def kv(pal, key, value: Shown):
    print(f'{pal.dim(shown(key).ljust(9))}{value}')
def f(o, pal, items):
    summary = f'moved {o.moved:d} of {len(items)}, {o.seconds:.3f} s, {o.run!r}'
    summary += pal.yellow(shown(o.error))
    lines = [shown(name) for name in items]
    lines.extend(f'  {shown(n)}' for n in items)
    a, b = (shown(x) for x in items)
    print(summary, g(o), '\\n'.join(lines).lstrip(), a + b, o.count - 1)
    for art, text in zip(('+', '-'), lines):
        print(f'{art} {text}', json.dumps(o))
    print('-' * len(items), 2 * '=')
    raise SystemExit(code())
    return Shown(f'{summary}{b}')
def code() -> int: ...
"""
    assert _violations({"m.py": accepted}) == []
