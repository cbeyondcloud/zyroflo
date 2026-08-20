"""Item 78 — generated code: declared, and indexed differently rather than excluded.

The fixture's claims live in EXPECTED.md (item 78 section): facts extracted from under
`orders-api/gen/` are stamped `generated: true` + `regenerate_from`, siblings stay
untouched, and the DECLARATION is load-bearing while the DO-NOT-EDIT header is inert.
Measured before designed: on a real workspace the marker files followed zero of the
classic name conventions, so nothing here detects — the workspace declares.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import tools.graph.build as B  # noqa: E402
from tools.graph.core.model import Edge, Node  # noqa: E402
from tools.graph.links.generated import GeneratedPass  # noqa: E402
from tools.graph.tests._mutate import build_mutated  # noqa: E402
import tools.graph.impact as I  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "orderflow"
SPEC = "orders-api/src/main/resources/openapi.yaml"


@pytest.fixture(scope="module")
def si():
    return B.load_installer()


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("generated") / "out"
    assert B.main(["--workspace", str(FIXTURE / "workspace.toml"),
                   "--out", str(out)]) == 0
    return out


def rows_of(out: Path) -> tuple[list[dict], list[dict]]:
    rows = [json.loads(line) for line
            in (out / "graph.jsonl").read_text(encoding="utf-8").splitlines()]
    return ([r for r in rows if r.get("kind") == "node"],
            [r for r in rows if r.get("kind") == "edge"])


# ── the declaration parses, and refuses what it cannot trust ──────────────────


def ws_with(tmp_path: Path, generated_toml: str, si) -> dict:
    root = tmp_path / "ws"
    (root / "comp-a").mkdir(parents=True, exist_ok=True)
    (root / "comp-a" / "input.yaml").write_text("x: 1\n", encoding="utf-8")
    toml = tmp_path / "sdlc-workspace.toml"
    # The [[generated]] block comes BEFORE [[component]]: in TOML, a bare key after a
    # table header belongs to that table, and these tests are about the top level.
    toml.write_text(
        'schema_version = 1\nroot = "ws"\n\n' + generated_toml
        + '\n[[component]]\nname = "comp-a"\npath = "comp-a"\n', encoding="utf-8")
    return si.read_workspace(toml)


def test_a_generated_block_parses_into_the_workspace(tmp_path, si):
    ws = ws_with(tmp_path, '[[generated]]\ncomponent = "comp-a"\npath = "src/gen"\n'
                           'from = "comp-a/input.yaml"\n', si)
    assert ws["generated"] == [
        {"component": "comp-a", "path": "src/gen", "from": "comp-a/input.yaml"}]


def test_from_is_optional_and_absent_parses_to_empty(tmp_path, si):
    ws = ws_with(tmp_path, '[[generated]]\ncomponent = "comp-a"\npath = "gen"\n', si)
    assert ws["generated"][0]["from"] == ""


def test_a_block_missing_component_or_path_is_refused(tmp_path, si):
    with pytest.raises(si.WorkspaceError, match="claim the whole component silently"):
        ws_with(tmp_path, '[[generated]]\ncomponent = "comp-a"\n', si)
    with pytest.raises(si.WorkspaceError, match="claim the whole component silently"):
        ws_with(tmp_path, '[[generated]]\npath = "gen"\n', si)


def test_an_unknown_key_is_refused_with_a_spelling_hint(tmp_path, si):
    with pytest.raises(si.WorkspaceError, match="Did you mean `path`"):
        ws_with(tmp_path, '[[generated]]\ncomponent = "comp-a"\npth = "gen"\n', si)


def test_two_blocks_claiming_one_tree_are_refused(tmp_path, si):
    with pytest.raises(si.WorkspaceError, match="both claim comp-a/gen"):
        ws_with(tmp_path, '[[generated]]\ncomponent = "comp-a"\npath = "gen"\n\n'
                          '[[generated]]\ncomponent = "comp-a"\npath = "gen/"\n', si)


def test_generated_must_be_written_as_blocks(tmp_path, si):
    with pytest.raises(si.WorkspaceError, match=r"\[\[generated\]\] blocks"):
        ws_with(tmp_path, 'generated = "src/gen"\n', si)


def test_an_inferred_single_repo_workspace_declares_nothing(tmp_path, si):
    repo = tmp_path / "solo"
    (repo / ".git").mkdir(parents=True)
    assert si.workspace_for_repo(repo)["generated"] == []


def test_a_component_nobody_discovered_is_refused_at_discovery(tmp_path, si):
    ws = ws_with(tmp_path, '[[generated]]\ncomponent = "comp-b"\npath = "gen"\n', si)
    with pytest.raises(si.WorkspaceError, match="not a discovered component"):
        si.discover_components(ws)


def test_a_missing_from_file_is_refused_at_discovery(tmp_path, si):
    ws = ws_with(tmp_path, '[[generated]]\ncomponent = "comp-a"\npath = "gen"\n'
                           'from = "comp-a/never-written.yaml"\n', si)
    with pytest.raises(si.WorkspaceError, match="does not exist under the workspace"):
        si.discover_components(ws)


def test_a_from_directory_is_as_legal_as_a_file(tmp_path, si):
    """A generated spec's input is often a source TREE, not one file — measured on the
    real workspace, where generated specs dominate the spec-side markers."""
    ws = ws_with(tmp_path, '[[generated]]\ncomponent = "comp-a"\npath = "gen"\n'
                           'from = "comp-a"\n', si)
    si.discover_components(ws)  # must not raise


# ── the pass stamps facts, in place, and only under the declared tree ─────────


def scene():
    nodes = [
        Node(id="svc:a", type="svc", props={"component": "comp-a"}),
        Node(id="cfg:a/gen/x.properties", type="cfg",
             props={"component": "comp-a", "path": "gen/x.properties"}),
        Node(id="cfg:a/src/gencode.properties", type="cfg",
             props={"component": "comp-a", "path": "src/gencode.properties"}),
        Node(id="topic:t", type="topic", props={}),
    ]
    edges = [
        Edge(src="svc:a", type="DG_PRODUCES", dst="topic:t",
             source_file="gen/Relay.java", line=7, extractor="x"),
        Edge(src="svc:a", type="DG_PRODUCES", dst="topic:t",
             source_file="src/Hand.java", line=3, extractor="x"),
    ]
    return nodes, edges


def run_pass(nodes, edges, decls):
    ctx = SimpleNamespace(config={"generated": decls})
    return GeneratedPass().link(ctx, nodes, edges)


def test_facts_under_the_tree_are_stamped_and_nothing_else_changes(tmp_path):
    nodes, edges = scene()
    res = run_pass(nodes, edges, [{"component": "comp-a", "path": "gen", "from": "spec.yaml"}])
    assert edges[0].props == {"generated": True, "regenerate_from": "spec.yaml"}
    assert edges[0].confidence == 1.0 and edges[0].canonical is True, \
        "a generated fact is not a doubtful fact — its edit site is elsewhere"
    assert nodes[1].props["generated"] is True
    assert nodes[1].props["regenerate_from"] == "spec.yaml"
    assert res.unresolved == [], "a matching declaration produces no row"


def test_a_sibling_outside_the_tree_carries_no_key_at_all(tmp_path):
    nodes, edges = scene()
    run_pass(nodes, edges, [{"component": "comp-a", "path": "gen", "from": ""}])
    assert "generated" not in edges[1].props, "absent, never false"
    assert "generated" not in nodes[0].props


def test_the_tree_boundary_is_a_path_segment(tmp_path):
    """`gen` covers `gen/…` and not `gencode.properties` — a bare prefix would stamp a
    sibling that merely shares letters."""
    nodes, edges = scene()
    run_pass(nodes, edges, [{"component": "comp-a", "path": "src/gen", "from": ""}])
    assert "generated" not in nodes[2].props
    assert "generated" not in edges[1].props


def test_from_is_recorded_only_when_declared(tmp_path):
    nodes, edges = scene()
    run_pass(nodes, edges, [{"component": "comp-a", "path": "gen", "from": ""}])
    assert edges[0].props == {"generated": True}, \
        "no declared input, no regenerate_from — an empty pointer is not advice"


def test_an_edge_with_no_component_prop_resolves_through_its_endpoints(tmp_path):
    nodes, edges = scene()
    run_pass(nodes, edges, [{"component": "comp-a", "path": "gen", "from": ""}])
    assert edges[0].props.get("generated") is True, \
        "the producer edge names no component itself; svc:a's props answer for it"


def test_a_declaration_that_stamps_nothing_is_a_recorded_row(tmp_path):
    nodes, edges = scene()
    res = run_pass(nodes, edges, [{"component": "comp-a", "path": "no/such", "from": ""}])
    (row,) = res.unresolved
    assert row["kind"] == "generated-unmatched"
    assert row["component"] == "comp-a" and row["declared_path"] == "no/such"
    assert "typo" in row["why"]


# ── the fixture, end to end — EXPECTED.md's item 78 section ──────────────────


def test_the_generated_producer_edge_is_stamped_end_to_end(built):
    _, edges = rows_of(built)
    (g1,) = [e for e in edges if e["source_file"] == "gen/EventsRelay.java"]
    assert g1["type"] == "DG_PRODUCES" and g1["line"] == 17
    assert g1["dst"] == "topic:order-audit-raw"
    assert g1["props"]["generated"] is True
    assert g1["props"]["regenerate_from"] == SPEC
    assert g1["confidence"] == 1.0, "a literal send; the stamp never touches confidence"


def test_the_generated_config_node_and_its_edge_are_stamped(built):
    nodes, edges = rows_of(built)
    (cfg,) = [n for n in nodes
              if n.get("props", {}).get("path") == "gen/generated.properties"]
    assert cfg["props"]["generated"] is True
    assert cfg["props"]["regenerate_from"] == SPEC
    (in_repo,) = [e for e in edges if e["source_file"] == "gen/generated.properties"]
    assert in_repo["props"]["generated"] is True


def test_the_hand_written_siblings_stay_unstamped(built):
    _, edges = rows_of(built)
    audit = [e for e in edges if e["source_file"].endswith("AuditTrail.java")]
    assert len(audit) >= 3, "P1-P3 are the item 91 pins; they must still be here"
    assert all("generated" not in e.get("props", {}) for e in audit), \
        "a sibling outside the declared subtree is untouched — absent, not false"


def test_the_declaration_not_the_header_is_load_bearing(tmp_path):
    """Point the declaration at a path that holds nothing: every stamp vanishes while
    the DO-NOT-EDIT header stays in the file, and the dead declaration is a row."""
    _, edges, unresolved = build_mutated(FIXTURE, tmp_path, "workspace.toml",
                                         'path = "gen"', 'path = "nah"')
    assert all("generated" not in e.get("props", {}) for e in edges)
    rows = [u for u in unresolved if u["kind"] == "generated-unmatched"]
    assert [(r["component"], r["declared_path"]) for r in rows] == [("orders-api", "nah")]


# ── impact reads the stamp: regenerate, don't edit ────────────────────────────

SEED = "orders-api/src/main/java/com/example/orderflow/api/AuditTrail.java"


def test_impact_flags_the_generated_file_and_only_it(built):
    nodes, edges = I.load_graph(built)
    result = I.impact(nodes, edges, *SEED.split("/", 1))
    by_path = {f["path"]: f for f in result["files"]}
    relay = by_path["gen/EventsRelay.java"]
    assert relay["generated"] is True and relay["regenerate_from"] == SPEC
    hand = [f for f in result["files"] if f["path"].endswith("OrderPublisher.java")]
    assert hand and all("generated" not in f for f in hand), \
        "an unstamped file's row is byte-identical to what it was before the flag existed"


def test_impact_renders_regenerate_dont_edit(built, capsys):
    assert I.main(["--graph-dir", str(built), "--seed", SEED]) == 0
    out = capsys.readouterr().out
    assert f"[generated — regenerate, don't edit; from: {SPEC}]" in out
    assert out.count("regenerate, don't edit") == 1, \
        "the advice marks the generated file, never its neighbours"
