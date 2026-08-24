"""Item 62: the zero-dependency parity gate — the drop-in promise as a test, not a claim.

The promise: single-repo mode completes with **no Neo4j, no Docker, no uv, no network, no
third-party packages**. Recurring, required green at every milestone M1–M6, so it lives in
the ordinary suite where every CI run re-asserts it.

The environment is not simulated by hoping the machine lacks packages — that would pass on
one laptop and mean nothing on the next. A `sitecustomize.py` import gate is injected into
the subprocess: any import whose top-level module is neither stdlib nor the harness itself
is refused, and socket construction raises. The gate is itself under test (the control
tests), because a permissive gate would turn every assertion here vacuous.

What the gate found on first contact: **PyYAML was a hard import in `core/lib/props.py`**,
so a zero-dependency build died at extractor discovery — `properties: failed to import` —
before any extractor could say what it was about to lose. The fix is `srcfacts`' own
tree-sitter idiom applied to the sibling module: the import is soft, `yaml_available()` is
the gate, and the yaml-reading extractors report one `grammar-unavailable` row per
component with `language: "yaml"`, exactly as the source extractors do for a missing
grammar. Degraded and saying so — never dead, and never silently short.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "sdlc-init.py"

#: The import gate. `sys.stdlib_module_names` is the interpreter's own account of the
#: standard library (3.10+), so the allowlist cannot rot as Python evolves. `tools` is the
#: harness; `sitecustomize` is the gate itself. Everything else is refused AT IMPORT, which
#: is deterministic on any machine regardless of what happens to be installed.
GATE = textwrap.dedent("""\
    import sys

    _ALLOWED = set(sys.stdlib_module_names) | {"tools", "sitecustomize"}

    class _ZeroDependencyGate:
        def find_spec(self, name, path=None, target=None):
            top = name.partition(".")[0]
            if top not in _ALLOWED:
                raise ImportError(
                    f"zero-dependency gate: {name!r} is not stdlib and not the harness")
            return None

    sys.meta_path.insert(0, _ZeroDependencyGate())

    import socket

    class _NoNetwork(socket.socket):
        def __init__(self, *a, **k):
            raise OSError("zero-dependency gate: the network is blocked")

    socket.socket = _NoNetwork
""")


@pytest.fixture(scope="module")
def gate_dir(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("gate")
    (d / "sitecustomize.py").write_text(GATE, encoding="utf-8")
    return d


def gated(args: list[str], gate: Path, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Run the interpreter under the gate, with uv and docker off PATH.

    PATH is stripped to the interpreter's own directory — `uv` never installs itself into
    a venv, so capability detection finds nothing, which IS the environment the promise
    describes. Everything else in the environment is kept: the gate constrains imports and
    sockets, not the operating system's ability to start a process.
    """
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{gate}{os.pathsep}{ROOT}"
    env["PYTHONNOUSERSITE"] = "1"
    env["PATH"] = str(Path(sys.executable).parent)
    return subprocess.run([sys.executable, *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env, cwd=cwd,
                          timeout=600)


def make_repo(tmp_path: Path) -> Path:
    """A synthetic single repo touching both halves: formats stdlib reads (properties,
    pom, python) and formats that need an absent library or grammar (yaml, java)."""
    repo = tmp_path / "solo"
    files = {
        "src/main/resources/application.properties":
            "orders.publish.topic=order-events-v1\nserver.servlet.context-path=/orders\n",
        "pom.xml": textwrap.dedent("""\
            <project>
              <groupId>com.example.solo</groupId>
              <artifactId>solo-api</artifactId>
              <version>1.0.0</version>
            </project>
            """),
        "src/main/java/com/example/L.java": textwrap.dedent("""\
            package com.example;
            public class L {
                @KafkaListener(topics = "${orders.publish.topic}", groupId = "g")
                public void on(String p) { }
            }
            """),
        "docker-compose.yml": "services:\n  api:\n    environment:\n      Q: order-events-v1\n",
        "deploy/values.yaml": "service:\n  name: solo-api\n",
        "deploy/Chart.yaml": "name: solo-api\n",
        "deploy/templates/cm.yaml": "kind: ConfigMap\ndata:\n  API_HOST: http://solo-api\n",
        "worker/app.py": "import os\nQUEUE = os.environ.get(\"RESTOCK_QUEUE\", \"restock\")\n",
    }
    for rel, body in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    (repo / ".git").mkdir()
    return repo


# ── the gate guards the gate ──────────────────────────────────────────────────


def test_the_gate_blocks_third_party_imports(gate_dir):
    """Without this, every assertion below could pass against a permissive gate."""
    for module in ("yaml", "tree_sitter", "neo4j"):
        proc = gated(["-c", f"import {module}"], gate_dir)
        assert proc.returncode != 0, f"the gate let {module} through"
        assert "zero-dependency gate" in proc.stderr


def test_the_gate_blocks_the_network(gate_dir):
    proc = gated(["-c", "import socket; socket.socket()"], gate_dir)
    assert proc.returncode != 0
    assert "network is blocked" in proc.stderr


def test_the_gate_removes_uv_and_docker_from_path(gate_dir):
    """The stripped PATH is an assumption about where uv lives; assumptions get asserted.
    If a uv or docker binary ever appears beside the interpreter, this names it."""
    proc = gated(["-c", "import shutil, sys; "
                        "sys.exit(3 if shutil.which('uv') or shutil.which('docker') else 0)"],
                 gate_dir)
    assert proc.returncode == 0, "uv or docker is reachable on the stripped PATH"


# ── the promise ───────────────────────────────────────────────────────────────


def test_the_harness_imports_clean_under_the_gate(gate_dir):
    """The headline: every module discovery will import, importable with nothing installed.

    The extractor packages are the surface that matters — `build.py` itself imports no
    extractor until discovery runs, which is exactly why the defect this found lived so
    long: PyYAML was a hard import in `core/lib/props.py`, reached only when discovery
    imported the properties package, and every environment that ran the suite had PyYAML.
    """
    proc = gated(["-c",
                  "from tools.graph import build as B\n"
                  "seen = {}\n"
                  "regs, links = B.discover(None, 'stack', seen)\n"
                  "_, generic_links = B.discover(B.LINK_ROOT, 'link', seen)\n"
                  "assert regs, 'stack discovery registered nothing'\n"
                  "assert links or generic_links, 'no link pass registered'\n"],
                 gate_dir, cwd=ROOT)
    assert proc.returncode == 0, proc.stderr


def test_the_pipeline_modules_import_and_run_under_the_gate(gate_dir):
    """The lifecycle machinery is what governs a machine that has nothing.

    Item 35 made the policy reader stdlib-only on that ground, and item 36 added
    three modules to the same surface — the stage contract, the review loop, and
    a pre-pass that now reaches across packages into `policy`. A parity claim
    checked by reading the imports is a parity claim nobody checked.
    """
    proc = gated(["-c", "; ".join([
        "from tools.sdlc import stage as S",
        "from tools.sdlc import worktree as WT",
        "from tools.sdlc import slice as SL",
        "from tools.review import loop as LP",
        "from tools.review import prepass as PP",
        "from pathlib import Path",
        "assert len(S.STAGES) == 12",
        "assert LP.assess([], ['critical'])['open'] == []",
        "assert PP.excluded_by(Path('.')) == []",
        "assert WT.MODES == ('worktree', 'in-place')",
        "assert SL.LAST_GATE == 'G4'",
    ])], gate_dir, cwd=ROOT)
    assert proc.returncode == 0, proc.stderr


def test_init_completes_in_a_zero_dependency_environment(gate_dir, tmp_path):
    """INIT scaffolds and, finding no uv, says so and exits 0 — item 80's absent-
    environment outcome, now pinned in the environment the promise describes."""
    repo = make_repo(tmp_path)
    proc = gated([str(INSTALLER), "--target", str(repo)], gate_dir, cwd=ROOT)
    assert proc.returncode == 0, proc.stderr
    assert (repo / ".sdlc").is_dir(), "INIT wrote no scaffold"
    assert "uv" in proc.stdout, "the absent environment should be named, not skipped over"


def test_the_build_completes_and_says_what_it_could_not_read(gate_dir, tmp_path):
    """Exit 0, real facts from the stdlib extractors, and one `grammar-unavailable` row
    per component from each reader whose library or grammar is absent — for java AND for
    yaml. A build that is short must say where; a build that dies says nothing."""
    repo = make_repo(tmp_path)
    proc = gated(["-m", "tools.graph.build", "--target", str(repo)], gate_dir, cwd=ROOT)
    assert proc.returncode == 0, proc.stderr + proc.stdout

    out = repo / ".sdlc" / "graph"
    graph = [json.loads(line) for line in
             (out / "graph.jsonl").read_text(encoding="utf-8").splitlines()]
    unresolved = [json.loads(line) for line in
                  (out / "unresolved.jsonl").read_text(encoding="utf-8").splitlines()]

    # The stdlib extractors produced real facts — the graph is degraded, not empty.
    assert any(r.get("kind") == "node" and r.get("type") == "cfg" for r in graph), \
        "the properties extractor read nothing"
    assert any(r.get("kind") == "edge" for r in graph), "no edge at all — vacuously green"

    missing = {(r["extractor"], r["language"]) for r in unresolved
               if r["kind"] == "grammar-unavailable"}
    for expected in (("compose-services", "yaml"), ("helm-deployments", "yaml"),
                     ("helm-configmaps", "yaml")):
        assert expected in missing, (
            f"{expected[0]} read nothing and reported nothing — the loss is invisible. "
            f"Reported: {sorted(missing)}")
    assert any(lang == "java" for _, lang in missing), \
        "the java sources were skipped without a row saying so"


def test_snapshot_publish_works_zero_dependency(gate_dir, tmp_path):
    """Item 13's committed artifact is part of single-repo mode, so the promise covers
    it: the shard and its manifest land beside the resolved view with no dependency."""
    repo = make_repo(tmp_path)
    proc = gated(["-m", "tools.graph.build", "--target", str(repo), "--publish-snapshot"],
                 gate_dir, cwd=ROOT)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    graph_dir = repo / ".sdlc" / "graph"
    assert (graph_dir / "shard.jsonl").exists()
    assert (graph_dir / "snapshot.json").exists()


def test_the_mcp_server_degrades_loudly_without_fastmcp(gate_dir):
    """Item 23's server rides the optional `serve` extra, so the promise covers its
    ABSENCE: the module must import with nothing installed (the guard, not a crash at
    discovery — the PyYAML lesson), and `main` must refuse with the install line
    rather than die at an ImportError. Degraded and saying so."""
    proc = gated(["-c",
                  "import sys\n"
                  "from tools.graph import mcp_server as M\n"
                  "assert M.FastMCP is None, 'the gate let fastmcp through'\n"
                  "sys.exit(M.main(['--graph-dir', '.']))\n"],
                 gate_dir, cwd=ROOT)
    assert proc.returncode == 2, proc.stderr
    assert "fastmcp" in proc.stdout and "--extra serve" in proc.stdout, \
        "the refusal must name the missing layer and how to install it"
