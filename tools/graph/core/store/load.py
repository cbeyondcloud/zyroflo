"""Load a built graph into a `GraphStore`. Item 53.

The pipeline is JSONL-first: `build.py` writes `graph.jsonl` and `impact.py` reads it,
and until this module existed **no production code constructed a store at all** — the
seam had two implementations and no caller. That is worth stating rather than quietly
fixing, because it is the shape item 102 found one week earlier in the graph manifest,
and because M6's portability condition is written as though the store were the storage
layer.

This does not make it the storage layer, and does not pretend to. It is the bridge the
condition actually needs: **a real built graph, loaded into a chosen backend, answering
the same questions.** Neo4j-free when the backend is SQLite — no Docker, no server, no
network.

**The reader is `impact.load_graph`, not a second one.** That function already refuses a
damaged graph by line rather than skipping the row, on the grounds that a graph with a
hole produces confidently wrong answers. A loader with its own tolerant parser would
have been a second answer to what a graph IS, and the lenient one would have won by
being the one that never complained.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..model import Edge, Node, Provenance
from .graphstore import GraphStore


def rows_to_model(nodes: list[dict], edges: list[dict], *,
                  where: str = "graph.jsonl") -> tuple[list[Node], list[Edge]]:
    """JSONL rows as model objects, refusing anything the model refuses.

    `Node` and `Edge` validate in `__post_init__` - an id-less node, a confidence
    outside [0,1] - and that validation is the point of going through them rather
    than shovelling dicts into the store. A row the model rejects is a row the graph
    should never have contained, and finding it here is finding it before it is
    queryable.

    **This function used to supply the fields it could not find**, which is the one
    thing that docstring promised it did not do: an absent `provenance` became
    `EXTRACTED` and an absent or null `line` became `0`, and both of those are claims -
    the highest-trust category, and the model's *meaningful* file-level line. So the
    model never saw the defect, because the defect had been filled in before it looked.
    `rows.check_row` is the rule, shared with `lib/emit.read_jsonl` so the two readers
    cannot drift again; `where` locates the offending row for the person who has to open it.
    """
    from ..lib.rows import check_row

    made_nodes = []
    for i, n in enumerate(nodes):
        check_row("node", n, where=f"{where} node[{i}]")
        made_nodes.append(Node(id=n["id"], type=n["type"],
                               props=dict(n.get("props") or {})))
    made_edges = []
    for i, e in enumerate(edges):
        check_row("edge", e, where=f"{where} edge[{i}]")
        prov = e["provenance"]
        made_edges.append(Edge(
            src=e["src"], type=e["type"], dst=e["dst"],
            source_file=e["source_file"], line=int(e["line"]),
            extractor=e["extractor"],
            confidence=float(e.get("confidence", 1.0)),
            provenance=Provenance(prov) if not isinstance(prov, Provenance) else prov,
            canonical=bool(e.get("canonical", True)),
            run_id=e.get("run_id", ""),
            props=dict(e.get("props") or {}),
        ))
    return made_nodes, made_edges


def load(out_dir: Path, store: GraphStore, *, clear: bool = True) -> dict[str, int]:
    """Load `<out_dir>/graph.jsonl` into `store`. Returns what went in and what stubbed.

    **Clears first by default.** Loading a second graph on top of a first without
    saying so would merge two workspaces into one and report the total as though it
    described either - the same silent-union that makes a stale rebuild look complete.

    The returned `stubs` count is not decoration: an edge whose endpoint no extractor
    produced becomes a stub rather than a dropped row, and a load that created stubs
    has found a coverage gap the JSONL alone does not surface.
    """
    from ...impact import GraphError, load_graph   # the ONE reader, refusals included

    # **The caller checks that the graph EXISTS**, which is the convention
    # `GraphError`'s own docstring records for the six callers that predate this one:
    # `load_graph` discriminates damaged from empty, and absent is the caller's to
    # refuse. This loader was the seventh caller and did not - so a load of a
    # directory holding no graph at all reported success with zero rows, which is
    # indistinguishable from a graph that is genuinely empty. Found by the test that
    # asked for the refusal, which is the only reason it is here.
    path = Path(out_dir) / "graph.jsonl"
    if not path.is_file():
        raise GraphError(
            f"{path} does not exist - there is no graph here to load. Build one "
            f"(tools/graph/build.py) rather than loading an empty store and "
            f"querying it as though it described this workspace")

    node_rows, edge_rows = load_graph(Path(out_dir))
    nodes, edges = rows_to_model(node_rows, edge_rows, where=str(path))

    store.connect()
    store.ensure_schema()
    if clear:
        store.clear()
    wrote_nodes = store.upsert_nodes(nodes)
    wrote_edges = store.upsert_edges(edges)
    return {"nodes": wrote_nodes, "edges": wrote_edges,
            "stubs": len(store.stub_nodes())}


def load_into(out_dir: Path, kind: str = "sqlite", **kwargs: Any) -> tuple[GraphStore, dict]:
    """A connected store holding this build's graph, and what the load put in it.

    The store is returned OPEN, because every caller wants to query it next and a
    loader that closed it would make the common case a reconnect.
    """
    from .graphstore import make_store

    store = make_store(kind, **kwargs)
    return store, load(Path(out_dir), store)
