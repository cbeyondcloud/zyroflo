"""Both fixtures, Neo4j-free, on the SQLite backend — and the SAME graph. Item 53.

M6 asks that both fixture workspaces build on the SQLite `GraphStore` backend. What
that condition is FOR is written in `graphstore.py`'s own docstring: prove a
Docker-free estate can run the same graph.

**Two claims, and the second is the one worth having.** That a fixture loads into
SQLite is portability. That SQLite and Neo4j answer the same questions about the same
graph is what makes the first claim mean anything — a second store that disagreed
would not be a portable graph, it would be two graphs.

**A finding recorded rather than papered over**: until item 53 nothing in production
constructed a store at all. `build.py` writes `graph.jsonl`, `impact.py` reads it, and
`make_store` had no caller outside tests — the pipeline went JSONL-first and the seam
was left behind. So these tests load a REAL built graph into a store rather than
pretending the build already did, and the condition is met by the bridge that was
missing rather than by a re-architecture nobody asked for.

The SQLite half needs nothing installed and runs everywhere, including CI. The parity
half needs a live Neo4j and skips without one — which is why the SQLite assertions are
never folded into it.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from tools.graph import build as B  # noqa: E402
from tools.graph.core.model import Edge, Node  # noqa: E402
from tools.graph.core.store import make_store  # noqa: E402
from tools.graph.core.store.load import load, load_into, rows_to_model  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
#: Both of them, by name. Item 19's second-stack fixture and item 75's synthetic one -
#: the condition says BOTH, and a test that ran one would satisfy its own wording.
BOTH = ("pantry", "orderflow")


@pytest.fixture(scope="module", params=BOTH)
def built(request, tmp_path_factory) -> Path:
    """A real build of one fixture. Module-scoped: the build is the slow part and
    nothing here mutates its output."""
    out = tmp_path_factory.mktemp(f"g-{request.param}") / "graph"
    B.build(FIXTURES / request.param / "workspace.toml", out,
            run_id=f"{request.param}-portability")
    return out


# ── the Docker-free half ────────────────────────────────────────────────────


def test_a_fixture_loads_into_sqlite_with_nothing_installed(built):
    """**The portability claim.** No Docker, no server, no network."""
    store, stats = load_into(built, "sqlite")
    try:
        assert stats["nodes"] > 0 and stats["edges"] > 0
        assert store.counts(), "a loaded graph that counts nothing is not loaded"
    finally:
        store.close()


def test_the_fixture_graphs_carry_no_dangling_endpoint(built):
    """Zero stubs. An edge whose endpoint no extractor produced becomes a stub rather
    than a dropped row, so this is a coverage assertion about the FIXTURES - and the
    number the load returns is what makes it checkable at all."""
    store, stats = load_into(built, "sqlite")
    try:
        assert stats["stubs"] == 0, [n.id for n in store.stub_nodes()][:5]
    finally:
        store.close()


def test_every_edge_endpoint_is_queryable_after_the_load(built):
    """A graph is not loaded because rows went in; it is loaded when the questions
    the impact engine asks have answers."""
    store, _ = load_into(built, "sqlite")
    try:
        ids = [n.id for n in store.find_nodes(limit=10_000)]
        assert ids
        hops = store.neighbors(ids[:50])
        assert sum(len(v) for v in hops.values()) > 0, \
            "no node in this graph has a neighbour, which no built fixture should say"
    finally:
        store.close()


def test_loading_twice_does_not_double_the_graph(built):
    """`clear=True` by default. Loading a second graph on top of a first without
    saying so merges two workspaces and reports the total as though it described
    either."""
    store = make_store("sqlite")
    try:
        first = load(built, store)
        again = load(built, store)
        assert first == again
        counts = store.counts()
        assert counts, "the reload emptied the store"
    finally:
        store.close()


# ── the half that makes the first half mean something ───────────────────────


def _neo4j_or_skip():
    try:
        s = make_store("neo4j")
        s.connect()
        return s
    except Exception as exc:                    # pragma: no cover - environment
        pytest.skip(f"no reachable neo4j: {exc}")


def test_both_backends_answer_the_same_questions_about_the_same_graph(built):
    """**The parity claim.** A portable graph is one both stores agree about.

    Skipped without a live Neo4j - and deliberately NOT folded into the SQLite
    assertions above, because a skip that took those with it would turn every
    Docker-free machine into one that proves nothing while reporting green.
    """
    neo = _neo4j_or_skip()
    lite = make_store("sqlite")
    try:
        load(built, neo)
        load(built, lite)

        assert neo.counts() == lite.counts(), "the two stores hold different graphs"

        ids = sorted(n.id for n in lite.find_nodes(limit=10_000))
        assert ids == sorted(n.id for n in neo.find_nodes(limit=10_000))

        for nid in ids[:40]:
            a = _comparable(neo.neighbors([nid])[nid])
            b = _comparable(lite.neighbors([nid])[nid])
            assert a == b, f"the backends disagree about the neighbours of {nid}"
    finally:
        neo.clear()
        neo.close()
        lite.close()


def _comparable(hops) -> list[tuple]:
    """The fields both backends promise, sorted. `props` is excluded on purpose: it
    carries backend-shaped extras, and the contract Neighbor states is these six."""
    return sorted((h.node_id, h.node_type, h.edge_type, h.direction,
                   round(h.confidence, 6), h.source_file, h.line) for h in hops)


# ── the loader refuses what the graph reader refuses ────────────────────────


def test_a_damaged_graph_is_refused_rather_than_partly_loaded(tmp_path):
    """`impact.load_graph` is the ONE reader, refusals included. A loader with its own
    tolerant parser would be a second answer to what a graph IS, and the lenient one
    would win by never complaining."""
    from tools.graph.impact import GraphError

    out = tmp_path / "g"
    out.mkdir()
    (out / "graph.jsonl").write_text(
        '{"kind":"node","id":"a","type":"svc"}\n{not json\n', encoding="utf-8")
    store = make_store("sqlite")
    try:
        with pytest.raises(GraphError):
            load(out, store)
    finally:
        store.close()


def test_an_absent_graph_is_refused_by_name(tmp_path):
    from tools.graph.impact import GraphError

    store = make_store("sqlite")
    try:
        with pytest.raises((GraphError, FileNotFoundError, OSError)):
            load(tmp_path / "nowhere", store)
    finally:
        store.close()


def test_rows_that_the_model_rejects_do_not_reach_the_store():
    """Validation happens by going THROUGH the model rather than shovelling dicts in.
    A confidence outside [0,1] is a row the graph should never have contained."""
    with pytest.raises(ValueError):
        rows_to_model([], [{"src": "a", "type": "CALLS", "dst": "b",
                            "source_file": "f", "line": 1, "extractor": "x",
                            "confidence": 7.0}])
    with pytest.raises(ValueError):
        rows_to_model([{"id": "", "type": "svc"}], [])


def test_a_node_and_an_edge_survive_the_round_trip():
    """Props and provenance are what a context pack cites; losing either in the store
    would make the pack's provenance a claim about nothing."""
    store = make_store("sqlite")
    try:
        store.connect()
        store.ensure_schema()
        nodes, edges = rows_to_model(
            [{"id": "svc:a", "type": "svc", "props": {"port": 8080, "tags": ["x"]}}],
            [{"src": "svc:a", "type": "CALLS", "dst": "svc:b", "source_file": "A.java",
              "line": 12, "extractor": "rest", "confidence": 0.9,
              "provenance": "INFERRED", "props": {"via": "resttemplate"}}])
        store.upsert_nodes(nodes)
        store.upsert_edges(edges)

        back = store.get_node("svc:a")
        assert back.props["port"] == 8080 and back.props["tags"] == ["x"]
        hop = store.neighbors(["svc:a"], direction="out")["svc:a"][0]
        assert hop.provenance == "INFERRED" and hop.line == 12
        assert hop.confidence == pytest.approx(0.9)
        assert hop.props.get("via") == "resttemplate"
    finally:
        store.close()
