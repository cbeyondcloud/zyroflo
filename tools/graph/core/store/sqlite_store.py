"""The SQLite `GraphStore` — the Docker-free half of the portability proof. Item 53.

The seam has existed since M0 for exactly this: `graphstore.py`'s own docstring says
the SQLite proof "is only cheap if the seam exists from the first commit". This is
that proof, and its value is entirely in being the SAME graph — so every semantic
below is matched to `neo4j_store` deliberately, and the ones that are easy to get
subtly wrong are named.

**`limit` is PER SEED in `neighbors`, not per call.** Neo4j needed a subquery to
manage that; here it is a loop, which is slower and unmistakable. A single global
limit lets one hub node consume the whole budget and return `[]` for every other
seed — indistinguishable, to the impact engine, from a node that genuinely has no
neighbours.

**A dangling endpoint becomes a STUB, never a dropped edge.** An edge pointing at
something no extractor produced is a coverage finding; `stub_nodes()` is what the
validation report counts, and dropping it silently is how a graph starts lying.

**Props MERGE, they do not replace.** Neo4j's `n += row.props` keeps keys the new
row does not mention. Replacing wholesale would make a second extractor's partial
upsert quietly delete the first one's facts.

**A node upserted for real clears its stub flag.** A stub created by an edge and
later produced by an extractor is not a coverage gap any more, and leaving the flag
set would report a gap that had been closed.

No SQL reaches a caller, and nothing here knows what a `svc` or a `topic` is —
`core/` names no estate, which `test_core_genericity.py` enforces.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from ..model import Edge, Node, Provenance, is_safe_rel_type
from .graphstore import Neighbor

#: `:memory:` is the default because most callers of a Docker-free backend are tests
#: and one-shot builds. A path makes it durable.
DEFAULT_PATH = ":memory:"

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS nodes (
        id    TEXT PRIMARY KEY,
        type  TEXT NOT NULL DEFAULT 'unknown',
        stub  INTEGER NOT NULL DEFAULT 0,
        props TEXT NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS edges (
        src         TEXT NOT NULL,
        type        TEXT NOT NULL,
        dst         TEXT NOT NULL,
        source_file TEXT NOT NULL,
        line        INTEGER NOT NULL,
        extractor   TEXT NOT NULL DEFAULT '',
        confidence  REAL NOT NULL DEFAULT 1.0,
        provenance  TEXT NOT NULL DEFAULT 'EXTRACTED',
        canonical   INTEGER NOT NULL DEFAULT 1,
        run_id      TEXT NOT NULL DEFAULT '',
        props       TEXT NOT NULL DEFAULT '{}',
        PRIMARY KEY (src, type, dst, source_file, line)
    )
    """,
    "CREATE INDEX IF NOT EXISTS dg_node_type ON nodes(type)",
    "CREATE INDEX IF NOT EXISTS dg_edge_src ON edges(src)",
    "CREATE INDEX IF NOT EXISTS dg_edge_dst ON edges(dst)",
    "CREATE INDEX IF NOT EXISTS dg_edge_extractor ON edges(extractor)",
)


class SqliteStore:
    """A `GraphStore` over one SQLite file, or memory."""

    def __init__(self, path: str | Path = DEFAULT_PATH, **_ignored: Any) -> None:
        self.path = str(path)
        self._db: sqlite3.Connection | None = None

    # ── lifecycle ──────────────────────────────────────────────────────────────
    def connect(self) -> None:
        if self._db is not None:
            return
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path)
        self._db.row_factory = sqlite3.Row
        # Foreign keys are deliberately NOT on: an edge to an unproduced node is a
        # stub, which is a finding to report rather than a write to refuse.
        self._db.execute("PRAGMA journal_mode=WAL")

    def close(self) -> None:
        if self._db is not None:
            self._db.commit()
            self._db.close()
            self._db = None

    def __enter__(self) -> "SqliteStore":
        self.connect()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    @property
    def db(self) -> sqlite3.Connection:
        if self._db is None:
            self.connect()
        assert self._db is not None
        return self._db

    def ensure_schema(self) -> None:
        for statement in _SCHEMA:
            self.db.execute(statement)
        self.db.commit()

    # ── write side ─────────────────────────────────────────────────────────────
    def upsert_nodes(self, nodes: list[Node], *, batch: int = 1000) -> int:
        if not nodes:
            return 0
        self.ensure_schema()
        for i in range(0, len(nodes), batch):
            for n in nodes[i : i + batch]:
                merged = _merged_props(self._props_of_node(n.id), _scalarize(n.props))
                self.db.execute(
                    "INSERT INTO nodes (id, type, stub, props) VALUES (?, ?, 0, ?) "
                    "ON CONFLICT(id) DO UPDATE SET type = excluded.type, "
                    # stub = 0 on purpose: a stub an edge created is no longer a
                    # coverage gap once an extractor actually produces the node.
                    "stub = 0, props = excluded.props",
                    (n.id, n.type, json.dumps(merged, sort_keys=True)),
                )
            self.db.commit()
        return len(nodes)

    def upsert_edges(self, edges: list[Edge], *, batch: int = 1000) -> int:
        if not edges:
            return 0
        self.ensure_schema()
        for i in range(0, len(edges), batch):
            for e in edges[i : i + batch]:
                _require_safe_rel_type(e.rel_type)
                for endpoint in (e.src, e.dst):
                    # INSERT OR IGNORE, so an endpoint an extractor already produced
                    # keeps its real type and its stub = 0.
                    self.db.execute(
                        "INSERT OR IGNORE INTO nodes (id, type, stub, props) "
                        "VALUES (?, 'unknown', 1, '{}')", (endpoint,))
                key = (e.src, e.type, e.dst, e.source_file, e.line)
                merged = _merged_props(self._props_of_edge(key), _scalarize(e.props))
                prov = (e.provenance.value if isinstance(e.provenance, Provenance)
                        else str(e.provenance))
                self.db.execute(
                    "INSERT INTO edges (src, type, dst, source_file, line, extractor,"
                    " confidence, provenance, canonical, run_id, props) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(src, type, dst, source_file, line) DO UPDATE SET "
                    "extractor = excluded.extractor, confidence = excluded.confidence, "
                    "provenance = excluded.provenance, canonical = excluded.canonical, "
                    "run_id = excluded.run_id, props = excluded.props",
                    (*key, e.extractor, float(e.confidence), prov,
                     1 if e.canonical else 0, e.run_id,
                     json.dumps(merged, sort_keys=True)),
                )
            self.db.commit()
        return len(edges)

    def delete_by_extractor(self, extractor: str, *, repo: str | None = None) -> int:
        """Every edge one extractor produced. The unit of incremental re-merge.

        Unbatched, and that is not a shortcut: SQLite has no transaction-size cap to
        work around, so the loop `neo4j_store` needs would be a loop that always ran
        once. Its reason - a capped DELETE silently leaving leftovers a rebuild then
        accumulates - cannot arise here.
        """
        self.ensure_schema()
        sql = "DELETE FROM edges WHERE extractor = ?"
        params: list[Any] = [extractor]
        if repo:
            sql += " AND source_file LIKE ? ESCAPE '\\'"
            params.append(_like_prefix(repo))
        cur = self.db.execute(sql, params)
        self.db.commit()
        return cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0

    def clear(self) -> None:
        self.ensure_schema()
        self.db.execute("DELETE FROM edges")
        self.db.execute("DELETE FROM nodes")
        self.db.commit()

    # ── read side ──────────────────────────────────────────────────────────────
    def get_node(self, node_id: str) -> Node | None:
        self.ensure_schema()
        row = self.db.execute(
            "SELECT id, type, stub, props FROM nodes WHERE id = ?", (node_id,)).fetchone()
        return _to_node(row) if row else None

    def find_nodes(self, *, type: str | None = None, id_prefix: str | None = None,
                   limit: int = 500) -> list[Node]:
        self.ensure_schema()
        sql = "SELECT id, type, stub, props FROM nodes"
        where, params = [], []
        if type:
            where.append("type = ?")
            params.append(type)
        if id_prefix:
            where.append("id LIKE ? ESCAPE '\\'")
            params.append(_like_prefix(id_prefix))
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id LIMIT ?"
        params.append(limit)
        return [_to_node(r) for r in self.db.execute(sql, params).fetchall()]

    def neighbors(self, node_ids: list[str], *, edge_types: list[str] | None = None,
                  direction: str = "both", limit: int = 5000) -> dict[str, list[Neighbor]]:
        """One hop from each seed. **`limit` is PER SEED**, which is why this loops.

        A single global LIMIT lets one hub consume the whole budget and hand back an
        empty list for every other seed - which the impact engine cannot tell from a
        node that genuinely has no neighbours, and which would make packs depend on
        what else happened to be in the batch.
        """
        if not node_ids:
            return {}
        self.ensure_schema()
        types: list[str] = []
        if edge_types:
            for t in edge_types:
                # An empty type would filter on nothing and return an empty result
                # that reads like an absence of neighbours - the same silent-empty
                # the Neo4j store refuses, refused here for the same reason.
                if not t:
                    raise ValueError("edge_types contains an empty relationship type")
                _require_safe_rel_type(f"DG_{t}")
                types.append(t)

        out: dict[str, list[Neighbor]] = {nid: [] for nid in node_ids}
        for sid in node_ids:
            clauses, params = [], []
            if direction in ("out", "both"):
                clauses.append(self._hop_sql("out", types))
                params.extend([sid, *types])
            if direction in ("in", "both"):
                clauses.append(self._hop_sql("in", types))
                params.extend([sid, *types])
            if not clauses:                     # an unknown direction behaves as "both"
                clauses = [self._hop_sql("out", types), self._hop_sql("in", types)]
                params = [sid, *types, sid, *types]
            sql = " UNION ALL ".join(clauses) + " LIMIT ?"
            params.append(limit)
            for r in self.db.execute(sql, params).fetchall():
                props = json.loads(r["props"] or "{}")
                out[sid].append(Neighbor(
                    node_id=r["oid"],
                    node_type=r["otype"] or "unknown",
                    edge_type=r["rtype"],
                    direction=r["dir"],
                    confidence=float(r["confidence"]),
                    provenance=r["provenance"] or "EXTRACTED",
                    source_file=r["source_file"] or "",
                    line=int(r["line"] or 0),
                    # Matches the Neo4j store's shape: everything the relationship
                    # carries EXCEPT the four fields Neighbor names in its own right.
                    props={"extractor": r["extractor"],
                           "canonical": bool(r["canonical"]),
                           "run_id": r["run_id"], **props},
                ))
        return out

    @staticmethod
    def _hop_sql(direction: str, types: list[str]) -> str:
        near, far = ("src", "dst") if direction == "out" else ("dst", "src")
        filt = (" AND e.type IN (" + ",".join("?" * len(types)) + ")") if types else ""
        return (
            f"SELECT n.id AS oid, n.type AS otype, e.type AS rtype, "
            f"'{direction}' AS dir, e.confidence AS confidence, e.provenance AS provenance, "
            f"e.source_file AS source_file, e.line AS line, e.extractor AS extractor, "
            f"e.canonical AS canonical, e.run_id AS run_id, e.props AS props "
            f"FROM edges e JOIN nodes n ON n.id = e.{far} "
            f"WHERE e.{near} = ?{filt}"
        )

    def counts(self) -> dict[str, int]:
        self.ensure_schema()
        out: dict[str, int] = {}
        for r in self.db.execute(
                "SELECT COALESCE(type,'unknown') AS t, COUNT(*) AS c FROM nodes GROUP BY t"):
            out[f"node:{r['t']}"] = r["c"]
        for r in self.db.execute(
                "SELECT type AS t, COUNT(*) AS c FROM edges GROUP BY t"):
            out[f"edge:{r['t']}"] = r["c"]
        return dict(sorted(out.items()))

    def stub_nodes(self) -> list[Node]:
        self.ensure_schema()
        return [_to_node(r) for r in self.db.execute(
            "SELECT id, type, stub, props FROM nodes WHERE stub = 1 ORDER BY id")]

    # ── internals ──────────────────────────────────────────────────────────────
    def _props_of_node(self, node_id: str) -> dict[str, Any]:
        row = self.db.execute("SELECT props FROM nodes WHERE id = ?", (node_id,)).fetchone()
        return json.loads(row["props"] or "{}") if row else {}

    def _props_of_edge(self, key: tuple) -> dict[str, Any]:
        row = self.db.execute(
            "SELECT props FROM edges WHERE src=? AND type=? AND dst=? AND source_file=? "
            "AND line=?", key).fetchone()
        return json.loads(row["props"] or "{}") if row else {}


# ── helpers ────────────────────────────────────────────────────────────────────
def _require_safe_rel_type(rel_type: str) -> None:
    """The same gate the Neo4j store applies, for a different reason.

    Nothing here interpolates a type into SQL - every one is a bound parameter - so
    this is not an injection guard. It is a PARITY guard: a type one backend refuses
    and the other accepts would make the two stores disagree about what a valid graph
    is, and the whole worth of this backend is being the same graph.
    """
    if not is_safe_rel_type(rel_type):
        raise ValueError(f"unsafe relationship type: {rel_type!r}")


def _like_prefix(prefix: str) -> str:
    """A LIKE pattern matching exactly this prefix, with wildcards neutralised.

    `%` and `_` are wildcards in LIKE, and a `source_file` or node id containing
    either would silently match more than the caller asked for - a prefix filter
    returning extra rows, which reads as the graph having more in it than it does.
    """
    escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return escaped + "%"


def _merged_props(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """Neo4j's `+=`: incoming wins, keys it does not mention SURVIVE.

    Replacing wholesale would let a second extractor's partial upsert quietly delete
    the first one's facts, which is a silent drop wearing a write's clothes.
    """
    return {**existing, **incoming}


def _to_node(row: Any) -> Node:
    """`stub` rides in props, matching the Neo4j store, whose `_to_node` keeps every
    field but `id` and `type` - so the validation report reads one shape."""
    props = json.loads(row["props"] or "{}")
    if row["stub"]:
        props = {**props, "stub": True}
    else:
        props = {**props, "stub": False}
    return Node(id=row["id"], type=row["type"] or "unknown", props=props)


def _scalarize(props: dict[str, Any]) -> dict[str, Any]:
    """The Neo4j store's rule, applied here so both backends store the same shape.

    It is not needed for SQLite - JSON holds anything - and that is exactly why it is
    here: a graph that round-trips a nested dict on one backend and a JSON string on
    the other is two graphs, and this backend exists to be the same one.
    """
    out: dict[str, Any] = {}
    for k, v in props.items():
        if v is None:
            continue
        if isinstance(v, (str, int, float, bool)):
            out[k] = v
        elif isinstance(v, (list, tuple)) and all(
                isinstance(x, (str, int, float, bool)) for x in v):
            out[k] = list(v)
        else:
            out[k] = json.dumps(v, sort_keys=True, ensure_ascii=False)
    return out
