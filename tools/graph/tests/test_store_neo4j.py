"""GraphStore contract tests against a live Neo4j.

Skipped when no local instance is reachable, so the suite still runs on a machine without
Docker. The graph is derived and rebuildable, so these tests are free to clear it.
"""
from __future__ import annotations

import os

import pytest
from dotenv import load_dotenv

from tools.graph.core.model import Edge, Node, Provenance
from tools.graph.core.store import make_store

load_dotenv()


def _store_or_skip():
    try:
        s = make_store(os.environ.get("GRAPH_STORE", "neo4j"))
        s.connect()
        return s
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"no reachable graph store: {exc}")


@pytest.fixture()
def store():
    s = _store_or_skip()
    s.ensure_schema()
    s.clear()
    yield s
    s.clear()
    s.close()


def test_node_roundtrip(store):
    store.upsert_nodes([Node(id="svc:a@r1", type="svc", props={"logical": "a", "port": 8080})])
    got = store.get_node("svc:a@r1")
    assert got is not None and got.type == "svc"
    assert got.props["logical"] == "a" and got.props["port"] == 8080


def test_nested_props_are_stored_as_json(store):
    """Neo4j rejects nested maps; the adapter flattens them rather than losing the value."""
    store.upsert_nodes([Node(id="cfg:x", type="cfg", props={"mapping": {"a": [1, 2]}})])
    assert "mapping" in store.get_node("cfg:x").props


def test_edge_carries_provenance(store):
    store.upsert_nodes([Node(id="svc:a@r", type="svc"), Node(id="svc:b@r", type="svc")])
    store.upsert_edges([
        Edge(src="svc:a@r", type="CALLS", dst="svc:b@r", source_file="A.java", line=42,
             extractor="rest_calls", confidence=0.9, provenance=Provenance.INFERRED,
             props={"via": "resttemplate"}),
    ])
    n = store.neighbors(["svc:a@r"], direction="out")["svc:a@r"]
    assert len(n) == 1
    hop = n[0]
    assert (hop.node_id, hop.edge_type, hop.line) == ("svc:b@r", "CALLS", 42)
    assert hop.source_file == "A.java" and hop.confidence == pytest.approx(0.9)
    assert hop.provenance == "INFERRED" and hop.props.get("via") == "resttemplate"


def test_dangling_reference_creates_a_visible_stub(store):
    """Zero silent drops: an unresolved endpoint becomes a stub, never a discarded edge."""
    store.upsert_nodes([Node(id="svc:a@r", type="svc")])
    store.upsert_edges([
        Edge(src="svc:a@r", type="CALLS", dst="ep:/unresolved/thing", source_file="A.java",
             line=1, extractor="rest_calls"),
    ])
    stubs = {n.id for n in store.stub_nodes()}
    assert "ep:/unresolved/thing" in stubs
    assert "svc:a@r" not in stubs, "a node produced by an extractor must not be marked a stub"


def test_same_edge_from_two_files_stays_two_rows(store):
    """Humans need every declaration site, not one collapsed summary."""
    store.upsert_nodes([Node(id="s", type="svc"), Node(id="d", type="svc")])
    store.upsert_edges([
        Edge(src="s", type="CALLS", dst="d", source_file="f1.java", line=1, extractor="x"),
        Edge(src="s", type="CALLS", dst="d", source_file="f2.java", line=7, extractor="x"),
    ])
    assert len(store.neighbors(["s"], direction="out")["s"]) == 2


def test_upsert_is_idempotent(store):
    store.upsert_nodes([Node(id="s", type="svc"), Node(id="d", type="svc")])
    edge = Edge(src="s", type="CALLS", dst="d", source_file="f.java", line=1, extractor="x")
    store.upsert_edges([edge])
    store.upsert_edges([edge])
    assert len(store.neighbors(["s"], direction="out")["s"]) == 1


def test_direction_filtering(store):
    store.upsert_nodes([Node(id="s", type="svc"), Node(id="d", type="svc")])
    store.upsert_edges([Edge(src="s", type="CALLS", dst="d", source_file="f", line=1, extractor="x")])
    assert store.neighbors(["d"], direction="out")["d"] == []
    assert len(store.neighbors(["d"], direction="in")["d"]) == 1
    assert len(store.neighbors(["d"], direction="both")["d"]) == 1


def test_edge_type_filtering(store):
    store.upsert_nodes([Node(id="s", type="svc"), Node(id="d", type="svc")])
    store.upsert_edges([
        Edge(src="s", type="CALLS", dst="d", source_file="f", line=1, extractor="x"),
        Edge(src="s", type="PRODUCES", dst="d", source_file="f", line=2, extractor="x"),
    ])
    only = store.neighbors(["s"], edge_types=["PRODUCES"], direction="out")["s"]
    assert [h.edge_type for h in only] == ["PRODUCES"]


def test_delete_by_extractor_is_the_incremental_unit(store):
    store.upsert_nodes([Node(id="s", type="svc"), Node(id="d", type="svc")])
    store.upsert_edges([
        Edge(src="s", type="CALLS", dst="d", source_file="f", line=1, extractor="keep"),
        Edge(src="s", type="CALLS", dst="d", source_file="g", line=2, extractor="drop"),
    ])
    store.delete_by_extractor("drop")
    remaining = store.neighbors(["s"], direction="out")["s"]
    assert [h.source_file for h in remaining] == ["f"]


def test_find_nodes_by_type_and_prefix(store):
    store.upsert_nodes([
        Node(id="svc:a@r", type="svc"), Node(id="svc:b@r", type="svc"),
        Node(id="topic:t", type="topic"),
    ])
    assert len(store.find_nodes(type="svc")) == 2
    assert [n.id for n in store.find_nodes(id_prefix="topic:")] == ["topic:t"]


def test_counts_report_by_type(store):
    store.upsert_nodes([Node(id="svc:a@r", type="svc"), Node(id="topic:t", type="topic")])
    store.upsert_edges([
        Edge(src="svc:a@r", type="PRODUCES", dst="topic:t", source_file="f", line=1, extractor="x")
    ])
    c = store.counts()
    assert c["node:svc"] == 1 and c["node:topic"] == 1 and c["edge:PRODUCES"] == 1


def test_unknown_backend_rejected():
    with pytest.raises(ValueError):
        make_store("mongodb")


def test_sqlite_backend_is_declared_but_not_yet_built():
    """The seam exists now; the M6 backend is an explicit NotImplementedError, not a stub."""
    with pytest.raises(NotImplementedError):
        make_store("sqlite")
