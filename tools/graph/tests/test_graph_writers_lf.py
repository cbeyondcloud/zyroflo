"""T546: the graph layer writes LF on every platform.

T543 fixed the review tools, whose outputs are committed run evidence, and found the same
class here: `build.py` (`build_report.json`, `canonical.proposed.toml`), the snapshot
manifest in `core/lib/manifest.py`, `validate.py` (`metrics.json`, `report.md`),
`pack.py` (`pack.d2`, `pack.md`, `pack.json`), `workspace_report.py` and
`impact_ab.py` wrote the platform newline. `.sdlc/graph/` is committed, so a Windows build
wrote CRLF where a Linux rebuild writes LF, and the next rebuild rewrote every line.

Asserted by reading the source rather than by running every writer: several of them sit at
the end of a git-history walk or a full build, and a runtime test on Linux, where the default
newline is already LF, would pass with the defect in place. The rule is structural, so the
check is too - every text write in the graph layer and in `impact_ab.py` names its newline -
and it holds on every platform. One writer is also run, and its bytes read, so the rule is
seen to mean what it says.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

GRAPH = Path(__file__).resolve().parents[1]
ROOT = GRAPH.parents[1]
sys.path.insert(0, str(ROOT))

import tools.graph.build as B  # noqa: E402
import tools.graph.validate as V  # noqa: E402

FIXTURE = GRAPH / "tests" / "fixtures" / "orderflow"

#: The modules whose writes the rule covers: every shipped module of the graph layer, and
#: the A/B harness, which writes its records the same way.
SCOPE = sorted([*(p for p in GRAPH.rglob("*.py")
                  if "tests" not in p.relative_to(GRAPH).parts),
                ROOT / "tools" / "impact_ab.py"])


def _mode(call: ast.Call, position: int) -> str | None:
    """An `open` call's mode, when it is written as a string literal."""
    for kw in call.keywords:
        if kw.arg == "mode":
            node = kw.value
            break
    else:
        if len(call.args) <= position:
            return None
        node = call.args[position]
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def platform_newline_writes(source: str) -> list[int]:
    """The line of every text write in `source` that leaves the newline to the platform:
    a `write_text` with no `newline=`, or an `open` for writing, in text mode, without one.
    `open` takes its mode second and `Path.open` first; a mode that is not a literal is
    not judged."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        named = {kw.arg for kw in node.keywords}
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "write_text":
            if "newline" not in named:
                found.append(node.lineno)
            continue
        if isinstance(func, ast.Name) and func.id == "open":
            mode = _mode(node, 1)
        elif isinstance(func, ast.Attribute) and func.attr == "open":
            mode = _mode(node, 0)
        else:
            continue
        if mode and "b" not in mode and set(mode) & set("wax") and "newline" not in named:
            found.append(node.lineno)
    return found


# ── the detector, seen to work ───────────────────────────────────────────────


@pytest.mark.parametrize("source, flagged", [
    ('p.write_text("x", encoding="utf-8")\n', [1]),
    ('p.write_text("x", encoding="utf-8", newline="\\n")\n', []),
    ('open(p, "w", encoding="utf-8")\n', [1]),
    ('open(p, "a")\n', [1]),
    ('open(p, "wb")\n', []),
    ('open(p)\n', []),
    ('open(p, "w", newline="\\n")\n', []),
    ('p.open("w", encoding="utf-8")\n', [1]),
    ('p.open(mode="x")\n', [1]),
    ('os.open(p, flags)\n', []),
])
def test_the_detector_tells_a_platform_newline_write_from_a_pinned_one(source, flagged):
    """A detector nobody has watched fire passes vacuously; each shape is shown."""
    assert platform_newline_writes(source) == flagged


def test_the_scope_holds_the_writers_this_row_names():
    """An empty or narrowed scope would pass the sweep below by reading nothing."""
    rels = {p.relative_to(ROOT).as_posix() for p in SCOPE}
    for rel in ("tools/graph/build.py", "tools/graph/core/lib/manifest.py",
                "tools/graph/validate.py", "tools/graph/pack.py",
                "tools/graph/workspace_report.py", "tools/impact_ab.py"):
        assert rel in rels, rel


# ── the rule ─────────────────────────────────────────────────────────────────


def test_every_text_write_in_the_graph_layer_names_its_newline():
    offenders = [f"{path.relative_to(ROOT).as_posix()}:{line}"
                 for path in SCOPE
                 for line in platform_newline_writes(path.read_text(encoding="utf-8"))]
    assert not offenders, (
        "these writes use the platform newline, so a Windows build writes CRLF where a "
        "Linux rebuild writes LF - pass newline=\"\\n\": " + ", ".join(offenders))


def test_a_build_and_its_dashboard_write_lf(tmp_path):
    """The rule, run: the committed build report and the dashboard's two documents hold
    no CR byte. Red on Windows before T546; the default newline is LF elsewhere."""
    out = tmp_path / "out"
    assert B.main(["--workspace", str(FIXTURE / "workspace.toml"),
                   "--out", str(out)]) == 0
    assert V.main(["--graph-dir", str(out)]) in (0, 2)     # graded either way
    for name in ("build_report.json", "metrics.json", "report.md"):
        assert b"\r" not in (out / name).read_bytes(), name
