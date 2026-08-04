"""The GraphStore seam.

Every query in the system goes through this interface, so no Cypher (or SQL) leaks into
extractors, the impact engine, or the MCP tools. Neo4j is the v1 implementation; a SQLite
implementation lands at M6 to prove a Docker-free estate can run the same graph — that
proof is only cheap if the seam exists from the first commit, which is why it is here in M0
rather than retrofitted later.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from ..model import Edge, Node


@dataclass(frozen=True, slots=True)
class Neighbor:
    """One hop away from a seed, with the edge that got there."""

    node_id: str
    node_type: str
    edge_type: str
    direction: str          # "out" | "in"
    confidence: float
    provenance: str
    source_file: str
    line: int
    props: dict[str, Any]


@runtime_checkable
class GraphStore(Protocol):
    """Storage and traversal for the domain graph.

    Traversal is expressed as repeated single-hop expansion rather than a variable-length
    path query: it stays within Neo4j Community (no APOC), it ports to SQLite unchanged, and
    it lets the impact engine apply per-edge damping between hops — which a single
    path query cannot do.
    """

    def connect(self) -> None: ...
    def close(self) -> None: ...

    def ensure_schema(self) -> None:
        """Create indexes/constraints. Idempotent."""

    def upsert_nodes(self, nodes: list[Node], *, batch: int = 1000) -> int: ...

    def upsert_edges(self, edges: list[Edge], *, batch: int = 1000) -> int:
        """Merge edges on (src, type, dst, source_file, line).

        Implementations MUST create a stub node for a dangling endpoint rather than dropping
        the edge: a reference to something the extractors did not resolve is a coverage
        finding, and silently discarding it is how a graph starts lying.
        """

    def delete_by_extractor(self, extractor: str, *, repo: str | None = None) -> int:
        """Remove everything one extractor produced — the unit of incremental re-merge."""

    def clear(self) -> None: ...

    # ── read side ──────────────────────────────────────────────────────────────
    def get_node(self, node_id: str) -> Node | None: ...
    def find_nodes(self, *, type: str | None = None, id_prefix: str | None = None,
                   limit: int = 500) -> list[Node]: ...
    def neighbors(self, node_ids: list[str], *, edge_types: list[str] | None = None,
                  direction: str = "both", limit: int = 5000) -> dict[str, list[Neighbor]]:
        """Single hop from each seed. Returns seed id -> neighbors."""

    def counts(self) -> dict[str, int]:
        """Node and edge totals by type — the validation report's raw input."""

    def stub_nodes(self) -> list[Node]:
        """Nodes created only as dangling-reference placeholders (zero-silent-drops check)."""


def make_store(kind: str, **kwargs: Any) -> GraphStore:
    """Factory: `GRAPH_STORE=neo4j|sqlite` selects the backend.

    Kept as a plain function so a profile or test can pass an in-memory fake without any
    dependency-injection machinery.
    """
    kind = (kind or "neo4j").lower()
    if kind == "neo4j":
        from .neo4j_store import Neo4jStore

        return Neo4jStore(**kwargs)
    if kind == "sqlite":  # pragma: no cover - lands at M6
        raise NotImplementedError(
            "the SQLite backend lands at M6 (tracker item 53); set GRAPH_STORE=neo4j for now"
        )
    raise ValueError(f"unknown GRAPH_STORE {kind!r} (expected 'neo4j' or 'sqlite')")
