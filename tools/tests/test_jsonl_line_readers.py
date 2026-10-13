"""T635: every JSON-lines reader splits a record on the newline its writer joins with.

`str.splitlines()` also ends a line at U+2028, U+2029, U+0085 and the other separators a JSON
string may hold raw - `json.dumps(..., ensure_ascii=False)` writes them, and so do ripgrep
and `gh --jq` - so a reader splitting with it cut one record in two, and each half failed to
decode: a graph holding one such fact read as damaged in every consumer, an approval as a
broken chain, a search match as nothing at all. T634 fixed the shard reader; this guard keeps
the rest from drifting back. It is a source scan, so it reads every module, not only the ones
a test happens to import.

Its blind spot: a reader that splits JSON lines WITHOUT parsing them - counting or comparing
entries, as the append-only check did (review B17-6, now `validate._entries`) - is not the shape
it looks for. Such a reader is found by reading, and fixed by hand.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _sources() -> list[Path]:
    """Every shipped Python module: the installer, and `tools/` without its tests."""
    out = [ROOT / "sdlc-init.py"]
    for path in sorted((ROOT / "tools").rglob("*.py")):
        parts = path.relative_to(ROOT).parts
        if "tests" in parts or path.name.startswith("test_") or "__pycache__" in parts:
            continue
        out.append(path)
    return out


def _own_nodes(scope: ast.AST):
    """The nodes of one scope, never descending into a function nested in it - each function
    is judged by its own body."""
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        yield node
        stack.extend(ast.iter_child_nodes(node))


def _calls(node: ast.AST, attr: str, owner: str | None = None) -> bool:
    """Whether `node` holds a call to `.<attr>(...)` - on a name `owner`, when one is given."""
    return any(isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
               and sub.func.attr == attr
               and (owner is None or (isinstance(sub.func.value, ast.Name)
                                      and sub.func.value.id == owner))
               for sub in ast.walk(node))


def splitlines_json_readers(source: str, name: str) -> list[str]:
    """Each loop or comprehension that walks text split by `.splitlines()` - directly, or
    through a name bound to it - and parses what it walks with `json.loads`: a JSON-lines
    reader that cuts a record where its writer did not. Taking the first line of an error
    with `.splitlines()` beside a `json.loads` of a whole document is not that shape."""
    tree = ast.parse(source, name)
    scopes = [tree, *(node for node in ast.walk(tree)
                      if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)))]
    hits = []
    for scope in scopes:
        nodes = list(_own_nodes(scope))
        split_names = {target.id for node in nodes if isinstance(node, ast.Assign)
                       and _calls(node.value, "splitlines")
                       for target in node.targets if isinstance(target, ast.Name)}

        def walks_split_text(iterable: ast.AST) -> bool:
            return _calls(iterable, "splitlines") or any(
                isinstance(sub, ast.Name) and sub.id in split_names
                for sub in ast.walk(iterable))

        for node in nodes:
            if isinstance(node, ast.For) and walks_split_text(node.iter):
                parsed = any(_calls(stmt, "loads", "json") for stmt in node.body)
            elif isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
                parsed = (any(walks_split_text(gen.iter) for gen in node.generators)
                          and _calls(node, "loads", "json"))
            else:
                continue
            if parsed:
                hits.append(f"{name}:{node.lineno} {getattr(scope, 'name', '<module>')}")
    return hits


def test_the_scan_finds_a_reader_that_splits_with_splitlines():
    """The positive control: a scan that finds nothing must be able to find something."""
    offender = ("import json\n"
                "def rows(text):\n"
                "    return [json.loads(line) for line in text.splitlines() if line]\n")
    assert splitlines_json_readers(offender, "x.py") == ["x.py:3 rows"]
    fixed = offender.replace("text.splitlines()", 'text.split("\\n")')
    assert splitlines_json_readers(fixed, "x.py") == []
    looped = ("import json\n"
              "def rows(text):\n"
              "    lines = text.splitlines()\n"
              "    out = []\n"
              "    for n, line in enumerate(lines, 1):\n"
              "        out.append(json.loads(line))\n"
              "    return out\n")
    assert splitlines_json_readers(looped, "x.py") == ["x.py:5 rows"], (
        "a loop over a name bound to split text is the same reader")
    not_a_reader = ("import json\n"
                    "def run(doc, err):\n"
                    "    first = err.splitlines()[0]\n"
                    "    return json.loads(doc), first\n")
    assert splitlines_json_readers(not_a_reader, "x.py") == [], (
        "the first line of an error beside a whole JSON document is no JSON-lines reader")
    nested = ("import json\n"
              "def outer(text):\n"
              "    lines = text.splitlines()\n"
              "    def inner(raw):\n"
              "        return json.loads(raw)\n"
              "    return lines, inner\n")
    assert splitlines_json_readers(nested, "x.py") == [], (
        "a nested function is judged by its own body, not its parent's")


def test_no_json_lines_reader_splits_with_splitlines():
    sources = _sources()
    assert len(sources) > 100, f"the scan found only {len(sources)} modules - a broken walk"
    offenders = [hit for path in sources
                 for hit in splitlines_json_readers(path.read_text(encoding="utf-8"),
                                                    path.relative_to(ROOT).as_posix())]
    assert offenders == [], (
        "these read JSON lines but split the text with `str.splitlines()`, which also ends a "
        "line at U+2028, U+2029 and U+0085 a JSON string may hold raw - split on the newline "
        "the writer joins with (T635):\n  " + "\n  ".join(offenders))
