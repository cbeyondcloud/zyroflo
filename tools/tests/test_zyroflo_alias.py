"""T696 (Z4): `zyroflo` is a second name for the `tools` package, never a second copy.

A caller that pins this harness as a library writes `import zyroflo.sdlc.approve`;
every `tools.*` import keeps working. The two names must reach the SAME module
objects, whichever is imported first: a second copy would hold its own globals,
so a monkeypatch through one name would not be seen through the other, and a
class from one copy would fail `isinstance` against the other's.

The order matters, so the import-order tests run in a fresh interpreter, where
neither name has been imported yet - this process has imported `tools` long
before any test runs.
"""
from __future__ import annotations

import importlib
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

import tools.sdlc.content
import tools.sdlc.policy
import zyroflo

ROOT = Path(__file__).resolve().parents[2]


def _fresh(code: str) -> subprocess.CompletedProcess:
    """Run `code` in a new interpreter at the repository root."""
    return subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=120)


# ── one module object under two names ────────────────────────────────────────


def test_a_module_imported_through_the_alias_is_the_tools_module():
    import zyroflo.sdlc.policy as aliased

    assert aliased is tools.sdlc.policy
    assert sys.modules["zyroflo.sdlc.policy"] is sys.modules["tools.sdlc.policy"]
    assert sys.modules["zyroflo.sdlc"] is sys.modules["tools.sdlc"]


def test_from_imports_through_the_alias_reach_the_same_objects():
    from zyroflo.sdlc import policy
    from zyroflo.sdlc.policy import destructive

    assert policy is tools.sdlc.policy
    assert destructive is tools.sdlc.policy.destructive


@pytest.mark.parametrize("first, second", [
    ("zyroflo.sdlc.approve", "tools.sdlc.approve"),
    ("tools.sdlc.approve", "zyroflo.sdlc.approve"),
])
def test_either_name_imported_first_gives_one_module(first, second):
    code = (f"import sys, importlib\n"
            f"a = importlib.import_module({first!r})\n"
            f"b = importlib.import_module({second!r})\n"
            f"assert a is b, (a, b)\n"
            f"assert a.__name__ == 'tools.sdlc.approve', a.__name__\n"
            f"assert a.__spec__.name == 'tools.sdlc.approve', a.__spec__.name\n"
            f"print('one module')\n")
    proc = _fresh(code)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "one module"


def test_the_alias_never_executes_a_module_a_second_time():
    """A second execution is a second copy even if `sys.modules` is tidied up
    afterwards: count the module body's runs through its import-time global."""
    code = ("import sys\n"
            "import tools.sdlc.content as t\n"
            "t.MARK = object()\n"
            "import zyroflo.sdlc.content as z\n"
            "assert z.MARK is t.MARK\n"
            "names = sorted(k for k, v in sys.modules.items() if v is t)\n"
            "print(names)\n")
    proc = _fresh(code)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "['tools.sdlc.content', 'zyroflo.sdlc.content']"


def test_a_monkeypatch_through_one_name_is_seen_through_the_other(monkeypatch, tmp_path):
    monkeypatch.setattr("zyroflo.sdlc.content.root", lambda: tmp_path)

    assert tools.sdlc.content.root() == tmp_path


def test_a_class_is_one_class_under_both_names():
    from zyroflo.sdlc.policy import PolicyError

    assert PolicyError is tools.sdlc.policy.PolicyError
    with pytest.raises(PolicyError):
        raise tools.sdlc.policy.PolicyError("raised under one name, caught under the other")


def test_reloading_through_the_alias_reloads_the_one_module():
    import zyroflo.sdlc.content as aliased

    assert importlib.reload(aliased) is tools.sdlc.content


def test_packages_and_plain_modules_across_the_tree_are_aliased():
    for name in ("cli", "graph", "graph.core", "review", "sdlc.stage"):
        aliased = importlib.import_module(f"zyroflo.{name}")
        assert aliased is importlib.import_module(f"tools.{name}"), name


def test_the_top_level_package_hands_out_its_subpackages_as_attributes():
    import tools.sdlc

    assert zyroflo.sdlc is tools.sdlc
    with pytest.raises(AttributeError):
        zyroflo.no_such_module  # noqa: B018 - the access is the assertion


def test_a_name_with_no_tools_module_is_not_found_under_the_alias():
    with pytest.raises(ModuleNotFoundError) as caught:
        importlib.import_module("zyroflo.sdlc.no_such_module")
    assert caught.value.name == "zyroflo.sdlc.no_such_module"


def test_importing_the_alias_alone_imports_nothing_else_of_the_harness():
    """`import zyroflo` must stay as cheap as `import tools`: the graph tier pulls
    in tree-sitter and neo4j, which a caller using only the gate never needs."""
    proc = _fresh("import sys, zyroflo\n"
                  "print(sorted(m for m in sys.modules if m.startswith('tools')))\n")
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "[]"


# ── the CLI, as a process ────────────────────────────────────────────────────


def test_the_console_dispatcher_runs_under_the_alias_as_it_does_under_tools():
    alias = subprocess.run([sys.executable, "-m", "zyroflo.sdlc", "--help"], cwd=ROOT,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=120)
    plain = subprocess.run([sys.executable, "-m", "tools.sdlc", "--help"], cwd=ROOT,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=120)

    assert alias.returncode == plain.returncode == 0, alias.stderr
    assert alias.stdout == plain.stdout


# ── one distribution ships both names ────────────────────────────────────────


def test_the_wheel_ships_both_packages():
    meta = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    packages = meta["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]

    assert packages == ["tools", "zyroflo"]
    assert (ROOT / "zyroflo" / "__init__.py").is_file()
