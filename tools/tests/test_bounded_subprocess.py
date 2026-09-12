"""A timed subprocess either keeps what it printed or says it could not look.

`subprocess.run(..., timeout=...)` does not return when the clock runs out; it
raises `TimeoutExpired`, and that exception is carrying `.stdout` and
`.stderr` - everything the child printed before it was killed. A site that
neither catches it nor delegates the catching loses that output AND crashes
its caller. That is the property this file pins.

`cli.run_bounded` is ONE way to satisfy it - the shared one, which folds the
partial output and a reason into a 4-tuple - but it is not the definition. A
module that wraps its own call in `try: ... except subprocess.TimeoutExpired`
has satisfied the property by hand and is not defective. So the rule here is
two-way, and a site is reported only when it does NEITHER.

The sweep DERIVES its sites from the tree rather than from a list somebody
wrote down, because a list is exactly what let the two `review/` scanners
survive the pass that fixed four of their siblings. `EXEMPT` holds the sites
allowed to be wrong and the measured reason for each; anything else new is
red here the moment it lands.

Scope is the SHIPPED modules. A test that spawns the harness under a clock is
exercising a CLI, and the evidence a timeout would lose there is the
assertion's own.
"""
from __future__ import annotations

import ast
import builtins
import subprocess
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parent.parent
ROOT = TOOLS.parent

#: Sites that are neither bounded through the helper nor guarded by hand,
#: keyed `path::function` so a line moving does not silently retire one.
#: Every reason below was MEASURED by resolving each enclosing handler to a
#: real class - not by reading the code - and every one of them is a defect
#: waiting on its own tracker row, not a design.
#:
#: **Empty, and that is the finished state.** The four sites this table was
#: opened for - `impact_ab.py::_rg_counts`, `impact_ab.py::extract_parent`,
#: `release.py::repo_of` and `release.py::push`, the push wall itself - were
#: each fixed and each exemption deleted in the same commit, because the
#: staleness test below is what makes a fixed site impossible to leave listed.
#: A new entry needs a MEASURED reason and a tracker row, not a note.
#:
#: **WHAT THIS SWEEP DOES NOT ASK, said out loud because an empty table reads like
#: a finished subject.** It asks whether a call is BOUNDED — a clock and a helper
#: that keeps what the child printed. It never asks whether the caller READS the
#: exit code that helper hands back. `impact_ab.py::_rg_counts` was listed here,
#: fixed, and deleted from the table while still discarding ripgrep's exit code, so
#: a filesystem loop returned a partial count as a whole one; that is a separate
#: row (its own test lives beside the function). Bounded is not the same as read.
EXEMPT: dict[str, str] = {}


def _enclosing(tree: ast.AST) -> list[tuple[int, int, str]]:
    """(first line, last line, dotted name) for every def and class."""
    spans: list[tuple[int, int, str]] = []

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            name = prefix
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.ClassDef)):
                name = f"{prefix}.{child.name}" if prefix else child.name
                spans.append((child.lineno, child.end_lineno or child.lineno,
                              name))
            walk(child, name)

    walk(tree, "")
    return spans


def _name_at(spans: list[tuple[int, int, str]], line: int) -> str:
    """The innermost def containing `line`, or `<module>`."""
    holding = [s for s in spans if s[0] <= line <= s[1]]
    return max(holding, key=lambda s: s[0])[2] if holding else "<module>"


def _timed_calls(tree: ast.AST) -> list[ast.Call]:
    """`subprocess.run(...)` calls that pass a `timeout=`.

    Matched on the spelling every shipped module uses - `import subprocess`
    then `subprocess.run(...)`. A module that aliased the callable would slip
    past this; none does today, and the positive control below is what says
    the matcher fires at all.
    """
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (isinstance(func, ast.Attribute) and func.attr == "run"
                and isinstance(func.value, ast.Name)
                and func.value.id == "subprocess"
                and any(kw.arg == "timeout" for kw in node.keywords)):
            out.append(node)
    return out


def _local_classes(tree: ast.AST) -> dict[str, type]:
    """Bare names bound to a `subprocess` class by a `from` import."""
    out: dict[str, type] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "subprocess":
            for alias in node.names:
                cls = getattr(subprocess, alias.name, None)
                if isinstance(cls, type):
                    out[alias.asname or alias.name] = cls
    return out


def _resolve(expr: ast.expr, local: dict[str, type]) -> type | None:
    """The class an except-clause expression names, if it can be resolved."""
    if isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Name):
        if expr.value.id == "subprocess":
            cls = getattr(subprocess, expr.attr, None)
            return cls if isinstance(cls, type) else None
        return None
    if isinstance(expr, ast.Name):
        cls = local.get(expr.id) or getattr(builtins, expr.id, None)
        return cls if isinstance(cls, type) else None
    return None


def _catches_timeout(handler: ast.ExceptHandler, local: dict[str, type]) -> bool:
    """Would this handler run on a `TimeoutExpired`?

    Answered by RESOLVING the named class and asking `issubclass`, not by
    matching the word. `except subprocess.SubprocessError` catches a timeout
    - it is TimeoutExpired's parent - and release.py:192 is written exactly
    that way; a text match would have called that site defective.
    """
    if handler.type is None:
        return True                                        # a bare `except:`
    parts = (handler.type.elts if isinstance(handler.type, ast.Tuple)
             else [handler.type])
    for part in parts:
        cls = _resolve(part, local)
        if isinstance(cls, type) and issubclass(subprocess.TimeoutExpired, cls):
            return True
    return False


def unhandled(sources: list[tuple[str, str]]) -> dict[str, list[int]]:
    """`{path::function: [lines]}` for every timed call that does NEITHER.

    A call counts as guarded when it is lexically inside the body of a `try`
    one of whose handlers would run on a `TimeoutExpired`. A call that goes
    through `cli.run_bounded` never reaches here at all - it is not a
    `subprocess.run`.
    """
    out: dict[str, list[int]] = {}
    for label, text in sources:
        tree = ast.parse(text)
        calls = _timed_calls(tree)
        if not calls:
            continue
        local = _local_classes(tree)
        guarded = [
            (min(s.lineno for s in node.body),
             max(getattr(s, "end_lineno", s.lineno) for s in node.body))
            for node in ast.walk(tree)
            if isinstance(node, ast.Try)
            and any(_catches_timeout(h, local) for h in node.handlers)
        ]
        spans = _enclosing(tree)
        for call in calls:
            if any(lo <= call.lineno <= hi for lo, hi in guarded):
                continue
            out.setdefault(f"{label}::{_name_at(spans, call.lineno)}",
                           []).append(call.lineno)
    return out


def shipped() -> list[Path]:
    """Every shipped module under `tools/` - tests and caches excluded."""
    return sorted(p for p in TOOLS.rglob("*.py")
                  if "tests" not in p.parts and "__pycache__" not in p.parts)


def sources() -> list[tuple[str, str]]:
    """`(repo-relative path, text)` for every shipped module."""
    return [(p.relative_to(ROOT).as_posix(), p.read_text(encoding="utf-8"))
            for p in shipped()]


#: A site that neither delegates nor catches. It is fed through the SAME
#: sweep the rule uses, alongside the real corpus, so that blinding the
#: corpus reddens this control instead of quietly passing it.
_PLANTED_BAD = ("import subprocess\n"
                "def scan():\n"
                "    return subprocess.run(['x'], capture_output=True,"
                " timeout=5)\n")
_PLANTED_GUARDED = ("import subprocess\n"
                    "def scan():\n"
                    "    try:\n"
                    "        return subprocess.run(['x'], timeout=5)\n"
                    "    except subprocess.TimeoutExpired:\n"
                    "        return None\n")
_PLANTED_DELEGATED = ("from tools import cli\n"
                      "def scan():\n"
                      "    return cli.run_bounded(['x'], timeout=5)\n")


def test_the_sweep_reports_a_site_that_neither_bounds_nor_catches() -> None:
    """Positive control: a guard that reports nothing would pass silently.

    Three planted modules go through the real sweep with the real corpus -
    one unguarded, one guarded by hand, one delegating to the helper. Only
    the first may be named, and the corpus assertions below are what fail if
    somebody blinds the file list to make the rule pass.
    """
    corpus = sources()
    assert len(corpus) > 20, "the sweep read no shipped modules"
    assert any(label.endswith("tools/cli.py") for label, _ in corpus)

    found = unhandled(corpus + [("planted/bad.py", _PLANTED_BAD),
                                ("planted/guarded.py", _PLANTED_GUARDED),
                                ("planted/delegated.py", _PLANTED_DELEGATED)])
    assert found.get("planted/bad.py::scan") == [3], found
    assert not [k for k in found if k.startswith("planted/guarded.py")]
    assert not [k for k in found if k.startswith("planted/delegated.py")]


def test_a_handler_naming_a_parent_class_still_counts_as_catching() -> None:
    """`except subprocess.SubprocessError` runs on a timeout; so does a tuple."""
    parent = ("import subprocess\n"
              "def f():\n"
              "    try:\n"
              "        subprocess.run(['x'], timeout=1)\n"
              "    except subprocess.SubprocessError:\n"
              "        pass\n")
    aliased = ("import subprocess\n"
               "from subprocess import TimeoutExpired as Slow\n"
               "def f():\n"
               "    try:\n"
               "        subprocess.run(['x'], timeout=1)\n"
               "    except (OSError, Slow):\n"
               "        pass\n")
    only_os = ("import subprocess\n"
               "def f():\n"
               "    try:\n"
               "        subprocess.run(['x'], timeout=1)\n"
               "    except OSError:\n"
               "        pass\n")
    assert not unhandled([("a.py", parent)])
    assert not unhandled([("b.py", aliased)])
    assert unhandled([("c.py", only_os)]) == {"c.py::f": [4]}


@pytest.mark.parametrize("key", sorted(EXEMPT) or [None])
def test_every_exemption_still_names_an_unhandled_site(key: str | None) -> None:
    """A stale exemption is a hole nobody is watching through.

    The `or [None]` is not a formality. `parametrize` over an EMPTY sequence
    emits one SKIPPED placeholder, and CI runs with `SDLC_FORBID_SKIPS=1`
    (`.github/workflows/ci.yml`), where `conftest.pytest_sessionfinish` turns
    any skip into exit 1 - so an empty table was a RED CI job that every local
    run reported green. The `None` case also gives the empty state an
    assertion of its own: nothing else in this file fails when a row is added,
    because the sweep below FILTERS offenders by `EXEMPT` and the process test
    compares against `len(EXEMPT)`, so both stay green for any self-consistent
    table.
    """
    if key is None:
        assert EXEMPT == {}, (
            "EXEMPT is not empty, so this placeholder should not have run - "
            f"parametrize should have expanded over {sorted(EXEMPT)}")
        return
    path, _, name = key.partition("::")
    assert (ROOT / path).exists(), f"{key} is exempt and its file is gone"
    found = unhandled(sources())
    assert key in found, (
        f"{key} is exempt from the timed-subprocess rule and no longer "
        f"breaks it - delete the exemption. Unhandled today: {sorted(found)}")


def test_every_timed_subprocess_keeps_what_it_printed_or_says_it_could_not_look(
) -> None:
    """The rule, derived from the tree rather than from a remembered list."""
    offenders = {k: v for k, v in unhandled(sources()).items() if k not in EXEMPT}
    assert not offenders, (
        "these sites call subprocess.run with a timeout, do not go through "
        "cli.run_bounded, and sit under no handler that would run on a "
        "TimeoutExpired - so a command the clock kills discards what it "
        "printed and takes the caller down with it: "
        + "; ".join(f"{k} (line {','.join(str(n) for n in v)})"
                    for k, v in sorted(offenders.items()))
        + " - adopt cli.run_bounded, catch subprocess.TimeoutExpired, or add "
          "the site to EXEMPT with the measured reason")


def test_the_guard_runs_as_a_process() -> None:
    """People run this file directly; importing it must not need pytest."""
    proc = subprocess.run(
        [sys.executable, "-c",
         "import sys;sys.path.insert(0,%r);"
         "from tools.tests import test_bounded_subprocess as g;"
         "print(len(g.sources()), len(g.unhandled(g.sources())))" % str(ROOT)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(ROOT), timeout=120)
    assert proc.returncode == 0, proc.stderr
    read, broken = (int(n) for n in proc.stdout.split())
    assert read > 20 and broken == len(EXEMPT)
