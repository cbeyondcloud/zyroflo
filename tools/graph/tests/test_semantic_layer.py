"""Item 79 — the semantic layer: entry points + domain aliases, declared.

The claims live in EXPECTED.md's item 79 section: the [[alias]] block is the ONLY
bridge between the chain and the negative control's cache table — built without it,
no legacy row appears at any hop — and the entry-point stamp rides every serving
surface while changing neither confidence nor canonicality. Every declared fact
cites the declaration file (workspace.toml:line), because a hand-declared fact that
blends into extraction kills the provenance guarantee exactly where a human started
typing.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import tools.graph.build as B  # noqa: E402
import tools.graph.impact as I  # noqa: E402
import tools.graph.pack as P  # noqa: E402
from tools.graph.core.model import Edge, Node  # noqa: E402
from tools.graph.links.semantic import SemanticPass  # noqa: E402
from tools.graph.rules import DEFAULT_RULES, Rules  # noqa: E402
from tools.graph.tests._mutate import build_mutated  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "orderflow"
PRODUCER = ("orders-api", "src/main/java/com/example/orderflow/api/OrderPublisher.java")
CACHE_SQL = "src/main/resources/db/migration/V1__create_order_reports_cache.sql"


@pytest.fixture(scope="module")
def si():
    return B.load_installer()


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("semantic") / "out"
    assert B.main(["--workspace", str(FIXTURE / "workspace.toml"),
                   "--out", str(out)]) == 0
    return out


@pytest.fixture(scope="module")
def graph(built):
    return I.load_graph(built)


# ── the declaration parses, and refuses what it cannot trust ──────────────────


def ws_with(tmp_path: Path, blocks: str, si) -> dict:
    root = tmp_path / "ws"
    (root / "comp-a").mkdir(parents=True, exist_ok=True)
    toml = tmp_path / "sdlc-workspace.toml"
    toml.write_text(
        'schema_version = 1\nroot = "ws"\n\n' + blocks
        + '\n[[component]]\nname = "comp-a"\npath = "comp-a"\n', encoding="utf-8")
    return si.read_workspace(toml)


def test_declarations_parse_with_their_own_citation(tmp_path, si):
    """The block's header line, found in the raw text — tomllib keeps no line
    numbers, and a declared fact that cannot cite where it was typed is
    indistinguishable from extraction."""
    ws = ws_with(tmp_path, '[[entrypoint]]\nnode = "ep:/x"\nnote = "front"\n\n'
                           '[[alias]]\na = "table:x"\nb = "table:y"\n', si)
    (ep,) = ws["entrypoint"]
    assert ep == {"node": "ep:/x", "note": "front",
                  "file": "sdlc-workspace.toml", "line": 4}
    (al,) = ws["alias"]
    assert al["a"] == "table:x" and al["b"] == "table:y"
    assert al["file"] == "sdlc-workspace.toml" and al["line"] == 8


def test_the_fixture_declarations_carry_the_answer_keys_lines(si):
    ws = si.read_workspace(FIXTURE / "workspace.toml")
    assert [e["line"] for e in ws["entrypoint"]] == [97, 101]
    assert ws["alias"][0]["line"] == 111
    assert ws["alias"][0]["file"] == "workspace.toml"


def test_a_node_that_is_not_an_id_is_refused(tmp_path, si):
    with pytest.raises(si.WorkspaceError, match="TYPE:REST"):
        ws_with(tmp_path, '[[entrypoint]]\nnode = "orders"\n', si)
    with pytest.raises(si.WorkspaceError, match="cannot mint one"):
        ws_with(tmp_path, '[[alias]]\na = "table:x"\nb = "nope"\n', si)


def test_unknown_keys_are_rejected_naming_the_known_set(tmp_path, si):
    with pytest.raises(si.WorkspaceError, match="node"):
        ws_with(tmp_path, '[[entrypoint]]\nnodeid = "ep:/x"\n', si)
    with pytest.raises(si.WorkspaceError, match=r"\ba\b"):
        ws_with(tmp_path, '[[alias]]\nfrom_node = "table:x"\nb = "table:y"\n', si)


def test_duplicates_and_self_joins_are_refused(tmp_path, si):
    with pytest.raises(si.WorkspaceError, match="both declare"):
        ws_with(tmp_path, '[[entrypoint]]\nnode = "ep:/x"\n\n'
                          '[[entrypoint]]\nnode = "ep:/x"\n', si)
    with pytest.raises(si.WorkspaceError, match="itself"):
        ws_with(tmp_path, '[[alias]]\na = "table:x"\nb = "table:x"\n', si)
    # The pair is unordered: (a,b) then (b,a) is the same join twice.
    with pytest.raises(si.WorkspaceError, match="both join"):
        ws_with(tmp_path, '[[alias]]\na = "table:x"\nb = "table:y"\n\n'
                          '[[alias]]\na = "table:y"\nb = "table:x"\n', si)


# ── the pass, against synthetic facts ─────────────────────────────────────────


def ctx_with(**config) -> SimpleNamespace:
    return SimpleNamespace(config=config)


def decl(node: str, note: str = "") -> dict:
    return {"node": node, "note": note, "file": "ws.toml", "line": 7}


def alias(a: str, b: str, note: str = "") -> dict:
    return {"a": a, "b": b, "note": note, "file": "ws.toml", "line": 9}


def test_the_stamp_reaches_every_row_of_a_duplicated_node():
    """graph.jsonl keeps one node row per emitting extractor; a stamp that
    landed on one row would make the flag depend on which row a reader picked."""
    nodes = [Node(id="ep:/x", type="ep", props={}),
             Node(id="ep:/x", type="ep", props={"other": 1})]
    SemanticPass().link(ctx_with(entrypoint=[decl("ep:/x", "front")]), nodes, [])
    assert all(n.props.get("entrypoint") is True for n in nodes)
    assert all(n.props.get("entrypoint_note") == "front" for n in nodes)
    assert nodes[1].props["other"] == 1, "the stamp adds; it never rewrites"


def test_an_unmatched_entrypoint_is_a_loud_row_with_the_declarations_cite():
    res = SemanticPass().link(ctx_with(entrypoint=[decl("ep:/missing")]),
                              [Node(id="ep:/x", type="ep", props={})], [])
    (row,) = res.unresolved
    assert row["kind"] == "entrypoint-unmatched"
    assert row["declared_node"] == "ep:/missing"
    assert (row["source_file"], row["line"]) == ("ws.toml", 7)
    assert "search_nodes" in row["why"]


def test_an_alias_side_that_does_not_exist_is_named_per_side():
    res = SemanticPass().link(
        ctx_with(alias=[alias("table:x", "table:missing")]),
        [Node(id="table:x", type="table", props={})], [])
    (row,) = res.unresolved
    assert row["kind"] == "alias-unmatched" and row["side"] == "b"
    assert row["declared_node"] == "table:missing"
    assert res.edges == [], "an alias cannot mint an entity"


def test_an_alias_onto_an_owned_node_is_refused_as_a_row():
    """Entities are the shared nodes. Service identity is [[canonical]]'s job;
    config seams inside a component are the extractors' — and an owned side
    would mint file rows in a component the declaration file does not live in."""
    for node in (Node(id="svc:x@comp-a", type="svc", props={}),
                 Node(id="cfg:comp-a/app.properties", type="cfg",
                      props={"component": "comp-a"})):
        res = SemanticPass().link(
            ctx_with(alias=[alias(node.id, "table:y")]),
            [node, Node(id="table:y", type="table", props={})], [])
        (row,) = res.unresolved
        assert row["kind"] == "alias-owned-node"
        assert row["owner"] == "comp-a"
        assert "[[canonical]]" in row["why"]
        assert res.edges == []


def test_the_alias_edge_carries_the_declaration_not_extraction_cosplay():
    res = SemanticPass().link(
        ctx_with(alias=[alias("table:x", "table:y", "one entity")]),
        [Node(id="table:x", type="table", props={}),
         Node(id="table:y", type="table", props={})], [])
    (e,) = res.edges
    assert (e.src, e.type, e.dst) == ("table:x", "DG_ALIAS_OF", "table:y")
    assert (e.source_file, e.line) == ("ws.toml", 9)
    assert e.confidence == 1.0 and e.canonical is True
    assert e.props == {"declared": True, "note": "one entity"}


# ── traversal: the declarations change reach, or they are decoration ──────────


def test_an_entry_point_spreads_without_hub_attenuation():
    """The front door fans by design. A transitively-reached hub spreads at
    threshold/fan; the same node declared an entry point spreads at full
    strength — this is the traversal half of the item's contract."""
    edges = [{"src": "seed", "dst": "hub", "type": "DG_EXPOSES",
              "source_file": "s", "line": 1}]
    edges += [{"src": "hub", "dst": f"t{i}", "type": "DG_EXPOSES",
               "source_file": "s", "line": 1} for i in range(25)]
    plain = I.traverse({"seed": 1.0}, edges)
    exempted = I.traverse({"seed": 1.0}, edges, exempt=frozenset({"hub"}))
    assert plain["t0"] == pytest.approx(0.9 * 0.9 * (20 / 25))
    assert exempted["t0"] == pytest.approx(0.9 * 0.9)


def test_reach_crosses_a_declared_alias_undamped_in_both_directions():
    edges = [{"src": "table:x", "dst": "table:y", "type": "DG_ALIAS_OF",
              "source_file": "ws.toml", "line": 9, "confidence": 1.0,
              "props": {"declared": True}}]
    assert I.traverse({"table:x": 1.0}, edges)["table:y"] == pytest.approx(1.0)
    assert I.traverse({"table:y": 1.0}, edges)["table:x"] == pytest.approx(1.0)


def test_rank_services_carries_the_entrypoint_flag():
    nodes = [{"id": "svc:gw@gw", "type": "svc",
              "props": {"component": "gw", "entrypoint": True}},
             {"id": "svc:plain@p", "type": "svc", "props": {"component": "p"}}]
    rows = {r["id"]: r for r in
            I.rank_services({"svc:gw@gw": 0.5, "svc:plain@p": 0.4}, nodes)}
    assert rows["svc:gw@gw"]["entrypoint"] is True
    assert "entrypoint" not in rows["svc:plain@p"]


# ── E2E on the fixture, against EXPECTED.md's item 79 section ─────────────────


def test_the_alias_edge_cites_the_declaration_file_and_line(graph):
    nodes, edges = graph
    (e,) = [e for e in edges if e["type"] == "DG_ALIAS_OF"]
    assert (e["src"], e["dst"]) == ("table:order_events", "table:order_reports_cache")
    assert (e["source_file"], e["line"]) == ("workspace.toml", 111)
    assert e["props"]["declared"] is True


def test_both_declared_entry_points_are_stamped(graph):
    nodes, _ = graph
    assert I.entrypoints(nodes) == frozenset(
        {"ep:/orders/v1/list", "ui:web-console@web-console"})


def test_the_chain_reaches_the_negative_control_only_through_the_declaration(
        graph, tmp_path):
    """The load-bearing negative. The island stays an island until a human
    declares the bridge — and cutting the declaration re-islands it."""
    nodes, edges = graph
    result = I.impact(nodes, edges, *PRODUCER)
    legacy = {f["path"] for f in result["files"] if f["component"] == "legacy-reports"}
    assert CACHE_SQL in legacy, "the declared bridge must carry reach"

    _, _, unres = build_mutated(FIXTURE, tmp_path, "workspace.toml",
                                'a = "table:order_events"',
                                'a = "table:no_such_entity"')
    m_nodes, m_edges = I.load_graph(tmp_path / "out")
    mutated = I.impact(m_nodes, m_edges, *PRODUCER)
    assert not [f for f in mutated["files"] if f["component"] == "legacy-reports"], \
        "without the declaration no legacy row may appear at any hop"
    assert any(u.get("kind") == "alias-unmatched" and u.get("side") == "a"
               for u in unres), "the doomed declaration must be a loud row"


def test_the_declaration_file_never_becomes_a_ranked_file(graph):
    """The alias edge's citation lives in no component; a row claiming
    `<component>/workspace.toml` would send a reviewer to a file that is not
    there. Structural, via the entity rule: unowned endpoints, no owner ladder
    hit, no row."""
    nodes, edges = graph
    result = I.impact(nodes, edges, *PRODUCER)
    assert not [f for f in result["files"] if "workspace.toml" in f["path"]]


def test_the_stamp_changes_no_confidence_and_no_canonicality(graph):
    """Item 78's rule holds here too: a declared fact is not a doubtful fact,
    and a front door's extracted edges are exactly as trustworthy as before."""
    nodes, edges = graph
    exposing = [e for e in edges if e["dst"] == "ep:/orders/v1/list"
                and e["type"] == "DG_EXPOSES"]
    assert exposing and all(e.get("confidence", 1.0) == 1.0
                            and e.get("canonical", True) is True for e in exposing)


def test_the_entrypoint_flag_rides_the_pack_and_its_render(built, graph):
    nodes, edges = graph
    pack = P.build_pack(nodes, edges, PRODUCER[0], [(PRODUCER[1], None)],
                        graph_dir=built)
    by_id = {c["id"]: c for c in pack["contracts"]}
    assert by_id["ep:/orders/v1/list"]["entrypoint"] is True
    assert "table:order_reports_cache" in by_id, \
        "the aliased entity joins the contract surface"
    assert "entrypoint" not in by_id["topic:order-events-v1"]
    md = P.render_md(dict(pack), budget=100_000)
    assert "[entry point]" in md


def test_the_declared_join_is_attributable_over_the_serving_layer(built):
    from tools.graph.mcp_server import GraphService
    svc = GraphService(built)
    result = svc.node_neighbors("table:order_events")
    joins = [r for r in result["out"] + result["in"] if r["type"] == "DG_ALIAS_OF"]
    (join,) = joins
    assert join["declared"] is True
    assert join["source_file"] == "workspace.toml" and join["line"] == 111


def test_a_declaration_change_invalidates_the_config_digest(si):
    ws = si.read_workspace(FIXTURE / "workspace.toml")
    base = {"workspace_name": "", "canonical": [], "generated": [],
            "entrypoint": [], "alias": [], "components": [], "profile": ""}
    with_alias = {**base, "alias": ws["alias"]}
    assert B.config_digest(base) != B.config_digest(with_alias), \
        "freshness stamps must go stale when a declaration changes"
