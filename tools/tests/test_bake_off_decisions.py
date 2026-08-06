"""The M0 bake-off decisions, as facts rather than as prose.

`docs/m0/bake-offs.md` records two "do not adopt" decisions. A decision document nobody can
check drifts from the code the moment someone has a free afternoon — which is the same failure
the de-scope register exists to prevent, one layer up. Each test here pins the criterion that
actually decided one of them, so reversing a decision means deleting a test and saying so.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from tools.graph.core.spi import NullSymbolLayer, RepoRef, SymbolLayer  # noqa: E402


def imported_modules(path: Path) -> set[str]:
    """Top-level module names a file imports, including inside functions."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.add(node.module.split(".")[0])
    return out


# ── bake-off 2, criterion C1: the one that decided it ─────────────────────────


def test_the_installer_imports_nothing_outside_the_standard_library():
    """Tier 0's headline promise, and the criterion that disqualified the alternative.

    The challenger is a PyPI package whose CLI must exist before its installer runs. That is
    only a disqualification if the incumbent genuinely has the property it is being compared
    on — so the property is checked here rather than asserted in a docstring.

    Walked with `ast` rather than by reading the import block, because this file imports
    `tomllib` INSIDE a function on purpose: a top-level import would break the single-repo
    drop-in on 3.10 for a feature it never uses.
    """
    outside = imported_modules(REPO / "sdlc-init.py") - set(sys.stdlib_module_names)
    assert outside == set(), (
        f"the installer imports {', '.join(sorted(outside))}, which is not stdlib. Tier 0 "
        f"promises no packages and no network, and item 62 re-checks it at every milestone")


def test_the_installer_owns_the_workspace_reader_the_graph_tools_use():
    """The fact that dissolved the installer bake-off's premise.

    It was framed as two interchangeable skill installers. Since item 69 this one also owns
    `read_workspace` / `discover_components`, and the graph tools import them from it — so
    "which directories are components" has exactly one implementation rather than two that
    drift. Swapping the installer would mean keeping it anyway, or porting the workspace layer
    into a third-party CLI on the one path gated as dependency-free.
    """
    source = (REPO / "sdlc-init.py").read_text(encoding="utf-8")
    assert "def read_workspace(" in source and "def discover_components(" in source

    consumers = [p for p in (REPO / "tools" / "graph").glob("*.py")
                 if "read_workspace" in p.read_text(encoding="utf-8")]
    assert {p.name for p in consumers} >= {"build.py", "workspace_report.py"}


# ── bake-off 1: what actually ships ───────────────────────────────────────────


SHIPPED_LAYERS = ("extractors", "links", "profiles")


def shipped_modules() -> list[Path]:
    return sorted(p for layer in SHIPPED_LAYERS
                  for p in (REPO / "tools" / "graph" / layer).rglob("*.py")
                  if "__pycache__" not in p.parts)


def test_the_shipped_extraction_layers_use_no_symbol_layer():
    """The measured half of the symbol-layer decision.

    Not "we decided not to adopt it" but "nothing here needs it": every hop of the chain that
    gates M0 is a configuration seam or an annotation, and hop 3 is the sharpest case — a call
    graph can see the send call and still cannot say what topic it names.

    If this ever fails, something has started depending on a layer that ships as a null
    implementation, and the M2 gate is overdue rather than optional.
    """
    offenders = [p.relative_to(REPO).as_posix() for p in shipped_modules()
                 if "SymbolLayer" in p.read_text(encoding="utf-8")]
    assert offenders == [], f"a shipped extraction layer reaches for the symbol layer: {offenders}"


def test_no_symbol_layer_package_is_a_declared_dependency():
    """Adopting is an adapter, not a rewrite — which is only true while nothing is installed."""
    pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8").lower()
    for package in ("codegraphcontext", "graphifyy", "graphify"):
        assert package not in pyproject, (
            f"{package} is declared as a dependency, which reverses a recorded bake-off "
            f"decision. Update docs/m0/bake-offs.md in the same commit or not at all")


def test_the_null_layer_is_a_complete_implementation_not_a_stub():
    """The seam has to be real for "adopting later is an adapter" to be true. A protocol
    nothing satisfies is a design note; one the shipping default satisfies is a seam."""
    layer = NullSymbolLayer()
    assert isinstance(layer, SymbolLayer)
    ref = RepoRef(alias="anything", path=REPO)
    assert layer.index(ref) is False
    assert layer.callers_of(ref, "anything") == []


@pytest.mark.parametrize("doc", ["bake-offs.md", "de-scope-register.md", "judging-protocol.md"])
def test_the_m0_decision_records_are_committed(doc: str):
    """Three decision artifacts M0 owes: what was deliberately left out, how the dogfood is
    judged, and what was evaluated and not adopted. A decision nobody wrote down gets
    re-litigated every sprint or rebuilt as an emergency six months later."""
    path = REPO / "docs" / "m0" / doc
    assert path.is_file() and path.read_text(encoding="utf-8").strip()
