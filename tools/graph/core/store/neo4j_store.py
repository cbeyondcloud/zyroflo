"""Neo4j 5 Community implementation of GraphStore. All Cypher in the system lives here.

Community-safe by construction: no APOC, no variable-length path queries, no GDS. Traversal
is single-hop expansion driven by the impact engine, which is also what makes the damping
model possible and the SQLite port mechanical.
"""
from __future__ import annotations

import os
from typing import Any

from neo4j import GraphDatabase

from ..model import NAMESPACE, Edge, Node, Provenance, is_safe_rel_type
from .graphstore import Neighbor

#: Applied to every domain node so the layer is separable from an optional symbol layer.
BASE_LABEL = NAMESPACE

_env_loaded = False


def _load_env() -> None:
    """Read `.env` once, so credentials come from the same file docker-compose reads.

    Only the test suite called `load_dotenv()`, which meant the tests passed against a
    developer's real `.env` while any production caller silently got `NEO4J_PASSWORD=""`
    and an authentication failure from a database that was plainly running. Never
    overrides a variable already exported, and degrades to a no-op without python-dotenv
    so `core` stays importable in a zero-dependency install.
    """
    global _env_loaded
    if _env_loaded:
        return
    _env_loaded = True
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv()


class Neo4jStore:
    """GraphStore backed by Neo4j 5 Community."""

    def __init__(self, uri: str | None = None, user: str | None = None,
                 password: str | None = None, database: str = "neo4j") -> None:
        _load_env()
        self.uri = uri or os.environ.get("NEO4J_URI", "bolt://localhost:7687")
        self.user = user or os.environ.get("NEO4J_USER", "neo4j")
        self.password = password or os.environ.get("NEO4J_PASSWORD", "")
        self.database = database
        self._driver: Any = None

    # ── lifecycle ──────────────────────────────────────────────────────────────
    def connect(self) -> None:
        if self._driver is None:
            self._driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))
            self._driver.verify_connectivity()

    def close(self) -> None:
        if self._driver is not None:
            self._driver.close()
            self._driver = None

    def __enter__(self) -> "Neo4jStore":
        self.connect()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _run(self, cypher: str, **params: Any) -> list[dict]:
        self.connect()
        with self._driver.session(database=self.database) as s:
            return [r.data() for r in s.run(cypher, **params)]

    # ── schema ─────────────────────────────────────────────────────────────────
    def ensure_schema(self) -> None:
        self._run(
            f"CREATE CONSTRAINT dg_node_id IF NOT EXISTS "
            f"FOR (n:{BASE_LABEL}) REQUIRE n.id IS UNIQUE"
        )
        self._run(f"CREATE INDEX dg_node_type IF NOT EXISTS FOR (n:{BASE_LABEL}) ON (n.type)")

    # ── write side ─────────────────────────────────────────────────────────────
    def upsert_nodes(self, nodes: list[Node], *, batch: int = 1000) -> int:
        if not nodes:
            return 0
        rows = [{"id": n.id, "type": n.type, "props": _scalarize(n.props)} for n in nodes]
        cypher = (
            f"UNWIND $rows AS row "
            f"MERGE (n:{BASE_LABEL} {{id: row.id}}) "
            f"SET n.type = row.type, n.stub = false, n += row.props"
        )
        for i in range(0, len(rows), batch):
            self._run(cypher, rows=rows[i : i + batch])
        return len(rows)

    def upsert_edges(self, edges: list[Edge], *, batch: int = 1000) -> int:
        """Merge edges, creating stub endpoints for dangling references.

        The stub is deliberate: an edge pointing at something no extractor produced is a
        coverage gap that must stay visible. `stub_nodes()` is what the validation report
        counts, and `zero silent drops` is checked against it.
        """
        if not edges:
            return 0
        by_type: dict[str, list[dict]] = {}
        for e in edges:
            by_type.setdefault(e.rel_type, []).append(
                {
                    "src": e.src, "dst": e.dst,
                    "source_file": e.source_file, "line": e.line,
                    "extractor": e.extractor, "confidence": e.confidence,
                    "provenance": e.provenance.value if isinstance(e.provenance, Provenance)
                    else str(e.provenance),
                    "canonical": e.canonical, "run_id": e.run_id,
                    "props": _scalarize(e.props),
                }
            )
        total = 0
        for rel_type, rows in by_type.items():
            # Relationship types cannot be parameterized in Cypher, so this one string has
            # to be interpolated. `Edge.__post_init__` already rejects anything that is not
            # a bare identifier; re-checking here means the guarantee belongs to the store
            # rather than to an assumption about its callers.
            _require_safe_rel_type(rel_type)
            cypher = (
                f"UNWIND $rows AS row "
                f"MERGE (s:{BASE_LABEL} {{id: row.src}}) "
                f"ON CREATE SET s.stub = true, s.type = 'unknown' "
                f"MERGE (d:{BASE_LABEL} {{id: row.dst}}) "
                f"ON CREATE SET d.stub = true, d.type = 'unknown' "
                f"MERGE (s)-[r:{rel_type} {{source_file: row.source_file, line: row.line}}]->(d) "
                f"SET r.extractor = row.extractor, r.confidence = row.confidence, "
                f"    r.provenance = row.provenance, r.canonical = row.canonical, "
                f"    r.run_id = row.run_id, r += row.props"
            )
            for i in range(0, len(rows), batch):
                self._run(cypher, rows=rows[i : i + batch])
            total += len(rows)
        return total

    def delete_by_extractor(self, extractor: str, *, repo: str | None = None,
                            batch: int = 50000) -> int:
        """Delete every edge an extractor produced. Returns how many were removed.

        Batched *and looped*. A single capped DELETE bounded the transaction but also
        bounded the contract: on an estate with more edges than the cap, the leftovers
        survived a re-extract and the return value was indistinguishable from a complete
        run — which is how a rebuild silently accumulates duplicate, stale edges.
        """
        total = 0
        while True:
            res = self._run(
                f"MATCH ()-[r]->() WHERE r.extractor = $extractor "
                f"{'AND r.source_file STARTS WITH $repo ' if repo else ''}"
                f"WITH r LIMIT $batch DELETE r RETURN count(*) AS n",
                extractor=extractor, batch=batch, **({"repo": repo} if repo else {}),
            )
            n = res[0]["n"] if res else 0
            total += n
            if n < batch:
                return total

    def clear(self) -> None:
        self._run(f"MATCH (n:{BASE_LABEL}) DETACH DELETE n")

    # ── read side ──────────────────────────────────────────────────────────────
    def get_node(self, node_id: str) -> Node | None:
        rows = self._run(f"MATCH (n:{BASE_LABEL} {{id: $id}}) RETURN n", id=node_id)
        return _to_node(rows[0]["n"]) if rows else None

    def find_nodes(self, *, type: str | None = None, id_prefix: str | None = None,
                   limit: int = 500) -> list[Node]:
        where = []
        params: dict[str, Any] = {"limit": limit}
        if type:
            where.append("n.type = $type")
            params["type"] = type
        if id_prefix:
            where.append("n.id STARTS WITH $prefix")
            params["prefix"] = id_prefix
        clause = f"WHERE {' AND '.join(where)} " if where else ""
        rows = self._run(
            f"MATCH (n:{BASE_LABEL}) {clause}RETURN n ORDER BY n.id LIMIT $limit", **params
        )
        return [_to_node(r["n"]) for r in rows]

    def neighbors(self, node_ids: list[str], *, edge_types: list[str] | None = None,
                  direction: str = "both", limit: int = 5000) -> dict[str, list[Neighbor]]:
        """Expand one hop from each seed. `limit` is PER SEED, not per call.

        A single global LIMIT let one hub node consume the whole budget and return `[]` for
        every other seed in the same batch — indistinguishable, to the impact engine, from
        "this node genuinely has no neighbors". A per-seed cap makes each seed's result
        depend only on that seed, which is also what makes packs reproducible.
        """
        if not node_ids:
            return {}
        rel_filter = ""
        if edge_types:
            for t in edge_types:
                # An empty type would build the filter `:DG_`, which is syntactically legal
                # and matches nothing — another silent-empty result rather than an error.
                if not t:
                    raise ValueError("edge_types contains an empty relationship type")
                _require_safe_rel_type(f"{NAMESPACE}_{t}")
            rel_filter = ":" + "|".join(f"{NAMESPACE}_{t}" for t in edge_types)
        pattern = {
            "out": f"(s)-[r{rel_filter}]->(o)",
            "in": f"(s)<-[r{rel_filter}]-(o)",
        }.get(direction, f"(s)-[r{rel_filter}]-(o)")
        # `CALL { WITH s, sid ... }` rather than the newer `CALL (s, sid) { ... }`: the
        # scope-clause form needs server 5.23+, and the compose file pins `neo4j:5-community`
        # without a minor. The old form works on every 5.x and only emits a deprecation
        # notice — a deliberate trade, revisited when the SQLite backend lands at M6.
        rows = self._run(
            f"UNWIND $ids AS sid MATCH (s:{BASE_LABEL} {{id: sid}}) "
            f"CALL {{ WITH s, sid MATCH {pattern} "
            # properties(r), not r: the driver renders a relationship object as a tuple
            # through .data(), and a plain property map is what ports to SQLite anyway.
            f"  RETURN o.id AS oid, o.type AS otype, type(r) AS rtype, "
            f"         startNode(r).id = sid AS outgoing, properties(r) AS rel "
            f"  LIMIT $limit }} "
            f"RETURN sid, oid, otype, rtype, outgoing, rel",
            ids=node_ids, limit=limit,
        )
        out: dict[str, list[Neighbor]] = {nid: [] for nid in node_ids}
        for r in rows:
            rel = r.get("rel") or {}
            out.setdefault(r["sid"], []).append(
                Neighbor(
                    node_id=r["oid"],
                    node_type=r.get("otype") or "unknown",
                    edge_type=_strip_ns(r["rtype"]),
                    direction="out" if r["outgoing"] else "in",
                    confidence=float(rel.get("confidence", 1.0)),
                    provenance=rel.get("provenance", "EXTRACTED"),
                    source_file=rel.get("source_file", ""),
                    line=int(rel.get("line", 0)),
                    props={k: v for k, v in rel.items()
                           if k not in {"confidence", "provenance", "source_file", "line"}},
                )
            )
        return out

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self._run(
            f"MATCH (n:{BASE_LABEL}) RETURN coalesce(n.type,'unknown') AS t, count(*) AS c"
        ):
            out[f"node:{r['t']}"] = r["c"]
        for r in self._run("MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS c"):
            out[f"edge:{_strip_ns(r['t'])}"] = r["c"]
        return dict(sorted(out.items()))

    def stub_nodes(self) -> list[Node]:
        rows = self._run(f"MATCH (n:{BASE_LABEL}) WHERE n.stub = true RETURN n ORDER BY n.id")
        return [_to_node(r["n"]) for r in rows]


# ── helpers ────────────────────────────────────────────────────────────────────
def _require_safe_rel_type(rel_type: str) -> None:
    """Gate every relationship type that reaches Cypher by interpolation.

    `edge_types` is an ordinary function parameter reached from MCP tools and the impact
    engine, so "it never comes from user input" was an assumption, not a property. A value
    like `calls]-() RETURN 1 //` would close the pattern and append attacker-chosen clauses.
    """
    if not is_safe_rel_type(rel_type):
        raise ValueError(f"unsafe relationship type for Cypher interpolation: {rel_type!r}")


def _strip_ns(rel_type: str) -> str:
    prefix = f"{NAMESPACE}_"
    return rel_type[len(prefix):] if rel_type.startswith(prefix) else rel_type


def _to_node(raw: dict) -> Node:
    props = {k: v for k, v in raw.items() if k not in {"id", "type"}}
    return Node(id=raw["id"], type=raw.get("type", "unknown"), props=props)


def _scalarize(props: dict[str, Any]) -> dict[str, Any]:
    """Neo4j properties must be scalars or scalar lists; nested structures are JSON-encoded.

    Done here rather than at the model layer so the JSONL snapshot keeps full fidelity and
    only the storage representation is flattened.
    """
    import json

    out: dict[str, Any] = {}
    for k, v in props.items():
        if v is None:
            continue
        if isinstance(v, (str, int, float, bool)):
            out[k] = v
        elif isinstance(v, (list, tuple)) and all(isinstance(x, (str, int, float, bool)) for x in v):
            out[k] = list(v)
        else:
            out[k] = json.dumps(v, sort_keys=True, ensure_ascii=False)
    return out
