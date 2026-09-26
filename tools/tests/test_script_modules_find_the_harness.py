"""T465: every harness tool run BY PATH finds its own package.

`python tools/sdlc/<tool>.py` is a promised interface (the README; T448 kept it),
and it is how an adopter's plain python - no editable install - runs a tool. A
tool that imports `tools.sdlc.*` needs the harness root on `sys.path`, and T463
was the one that did not have it: `state.py --new` made its run folder and died
on `ModuleNotFoundError`. Two things hid it, and this guard removes both. The
dev venv's editable install puts the root on the path for every in-venv test,
so the check runs under `-S`. And the import was LAZY: `--help` exits 0 without
reaching it, which is why T448's per-subcommand `--help` spawns stayed green. So
the question is not "does --help work" but "after the tool's own setup, does
`tools.sdlc` come from this harness".

A tool that names no `tools.` module at all - no import, and no module named in
a string for `importlib` - runs outside the harness by design (the two model
runners) and needs nothing. The string counts: `policy.py` reaches
`tools.provider_smoke` only through `importlib.import_module`, which an
import-statement rule would have exempted.
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from tools.sdlc.__main__ import subcommands

ROOT = Path(__file__).resolve().parents[2]

#: Run in a child `python -E -S` - no site (so no editable install) and no
#: PYTHONPATH. It does what the interpreter does for `python <tool>`: the tool's
#: own directory first on sys.path, run as __main__, here with `--help`, which
#: every tool answers without doing any work. Then it asks where `tools.sdlc`
#: comes from, now that the tool has set itself up.
_CHILD = r"""
import os, runpy, sys
tool, root = sys.argv[1], os.path.realpath(sys.argv[2])
sys.argv = [tool, "--help"]
sys.path.insert(0, os.path.dirname(tool))
try:
    runpy.run_path(tool, run_name="__main__")
except SystemExit:
    pass
try:
    import tools.sdlc
except ImportError as exc:
    print("VERDICT not importable: " + ascii(str(exc)))
else:
    where = os.path.realpath(tools.sdlc.__file__)
    try:
        inside = (os.path.normcase(os.path.commonpath([where, root]))
                  == os.path.normcase(root))
    except ValueError:
        inside = False
    print("VERDICT ok" if inside else "VERDICT from elsewhere: " + ascii(where))
"""


def names_a_harness_module(path: Path) -> bool:
    """Whether a tool imports - or names, for `importlib` - any `tools.` module."""
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "tools":
            return True
        if isinstance(node, ast.Import) and any(a.name.split(".")[0] == "tools"
                                                for a in node.names):
            return True
        if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and node.value.startswith("tools.")):
            return True
    return False


def finds_the_harness(tool: Path, root: Path, cwd: Path) -> str:
    """'' when `tool`, run by path from `cwd`, leaves `tools.sdlc` importable
    from `root`; otherwise what went wrong."""
    done = subprocess.run([sys.executable, "-E", "-S", "-c", _CHILD, str(tool), str(root)],
                          cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=300)
    verdicts = [line for line in done.stdout.splitlines() if line.startswith("VERDICT ")]
    if not verdicts:
        return f"no verdict (exit {done.returncode}): {done.stderr.strip()[-600:]}"
    return "" if verdicts[-1] == "VERDICT ok" else verdicts[-1][len("VERDICT "):]


#: The tools a person runs by path - the set the `sdlc` dispatcher derives - that
#: need the harness package.
TOOLS = {name: path for name, path in subcommands().items() if names_a_harness_module(path)}


def test_the_set_is_derived_and_holds_the_tool_that_broke():
    """An empty set would pass every case below vacuously (pytest would call it
    a skip), and the set must still hold the tool this row exists for."""
    assert "state" in TOOLS, sorted(TOOLS)


@pytest.mark.parametrize("name", sorted(TOOLS))
def test_a_tool_run_by_path_finds_the_harness(name, tmp_path):
    assert finds_the_harness(TOOLS[name], ROOT, tmp_path) == ""


#: T542: the scripts at `tools/`'s top level are run by path by the same plain python
#: - `provider_smoke.py` is RUNNER_CONTRACT's own smoke - and it was the one of them
#: with no path line: `ModuleNotFoundError`, at the exit code that tool means as "red".
SCRIPTS = {p.stem: p for p in sorted((ROOT / "tools").glob("*.py"))
           if p.stem not in ("__init__", "conftest") and names_a_harness_module(p)}


def test_the_script_set_is_derived_and_holds_the_tool_that_broke():
    assert "provider_smoke" in SCRIPTS, sorted(SCRIPTS)


@pytest.mark.parametrize("name", sorted(SCRIPTS))
def test_a_script_run_by_path_finds_the_harness(name, tmp_path):
    assert finds_the_harness(SCRIPTS[name], ROOT, tmp_path) == ""


_LAZY_TOOL = '''import argparse


def main():
    argparse.ArgumentParser().parse_args()
    from tools.sdlc import events  # noqa: F401 - lazy, as state.create's was


if __name__ == "__main__":
    main()
'''
_PATH_LINE = ("import sys\nfrom pathlib import Path\n\n"
              "sys.path.insert(0, str(Path(__file__).resolve().parents[2]))\n")


def _harness(base: Path, source: str) -> Path:
    """A minimal harness tree holding one tool."""
    sdlc = base / "tools" / "sdlc"
    sdlc.mkdir(parents=True)
    for package in (base / "tools", sdlc):
        (package / "__init__.py").write_text("", encoding="utf-8")
    (sdlc / "events.py").write_text("", encoding="utf-8")
    (sdlc / "tool.py").write_text(source, encoding="utf-8")
    return sdlc / "tool.py"


def test_the_check_reports_t463s_shape_and_passes_its_fix(tmp_path):
    """The positive control. T463's exact shape - a lazy `tools.` import behind
    a `--help` that exits 0 - must be REPORTED by the same check the real tools
    pass; and the same tool with the path line must pass, so the check is not
    simply failing everything synthetic."""
    (tmp_path / "elsewhere").mkdir()
    broken = _harness(tmp_path / "broken", _LAZY_TOOL)
    assert names_a_harness_module(broken)
    assert "not importable" in finds_the_harness(broken, tmp_path / "broken",
                                                 tmp_path / "elsewhere")
    fixed = _harness(tmp_path / "fixed", _PATH_LINE + _LAZY_TOOL)
    assert finds_the_harness(fixed, tmp_path / "fixed", tmp_path / "elsewhere") == ""


def test_a_tool_that_names_the_harness_only_as_a_string_still_needs_it(tmp_path):
    """`importlib.import_module("tools.x")` fails exactly like an import
    statement when the root is missing, so a string naming a `tools.` module
    puts the tool in the set."""
    tool = tmp_path / "tool.py"
    tool.write_text('import importlib\n\nimportlib.import_module("tools.provider_smoke")\n',
                    encoding="utf-8")
    assert names_a_harness_module(tool)
    tool.write_text("import json\n", encoding="utf-8")
    assert not names_a_harness_module(tool)
