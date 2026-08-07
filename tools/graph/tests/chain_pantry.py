"""The second fixture's chain, in machine-readable form. Not a test module.

The counterpart of `chain.py`, and it exists for the same reason: one definition imported by
both the golden test and the mutation test, because two copies drift and a drifted copy
weakens the gate silently — the golden test would keep asserting the old chain while the
mutation test cut seams in a chain nobody checks.

**This file is a transcription, never a source.** `fixtures/pantry/EXPECTED.md` is the answer
key, written by hand before any of these extractors existed, and every row here is copied
from its tables with the line numbers included. If the two disagree, the answer key is right.
Changing a row here to make a test pass is editing the answer key somewhere nobody is looking
for it.
"""
from __future__ import annotations

from tools.graph.core.lib import ids

API = ids.component_service_id("pantry-api")
WORKER = ids.component_service_id("restock-worker")

#: (hop, edge type, source, target, file suffix, line) — one row per hop of `EXPECTED.md`'s
#: chain table. The line is the one a reader is sent to, and it is part of the claim.
CHAIN: list[tuple[int, str, str, str, str, int]] = [
    (1, "DG_CALLS", WORKER, "ep:/pantry/v1/items", "client.py", 13),
    (2, "DG_EXPOSES", API, "ep:/pantry/v1/items", "routes.py", 13),
    (3, "DG_PRODUCES", API, "topic:restock-events-v2", "publisher.py", 18),
    (4, "DG_CONSUMES", WORKER, "topic:restock-events-v2", "consumer.py", 13),
    (5, "DG_OWNS_TABLE", WORKER, "table:restock_events",
     "0001_create_restock_events.sql", 2),
]

#: The OTHER coordinates `EXPECTED.md` names for a hop.
#:
#: Four of the five hops are assembled from more than one place — a prefix and a decorator, a
#: path and an address, a call and a binding and a deployment value — and the answer key lists
#: every one. The edge carries a single `line`, so the rest ride in props, and asserting only
#: `line` would let a hop resolve to the right node while losing the trail a human follows to
#: change it. Keyed by hop; values are (prop name, expected) where a string is matched as a
#: suffix so component-relative paths compare cleanly.
CHAIN_PROVENANCE: dict[int, dict[str, object]] = {
    1: {"key_line": 11, "host_file": "docker-compose.yml", "host_line": 18},
    2: {"prefix_line": 10, "prefix": "/pantry/v1"},
    3: {"call_line": 27, "resolved_from": "docker-compose.yml:10"},
    4: {"call_line": 19, "resolved_from": "consumer.py:13"},
}

#: Which component declares each hop, in order.
CHAIN_OWNERS = ["restock-worker", "pantry-api", "pantry-api",
                "restock-worker", "restock-worker"]

#: `EXPECTED.md` P5 — the component that participates in nothing.
INERT_COMPONENT = "label-printer"

#: The component no extractor matches at all. A coverage hole is a finding, not a failure,
#: and "nothing matched this" is a different answer from "this has no couplings".
UNCLAIMED_COMPONENT = "ops-runbook"


def find(edges: list[dict], type_: str, src: str, dst: str) -> list[dict]:
    """Every edge matching one hop's identity — type, source and target."""
    return [e for e in edges if e["type"] == type_ and e["src"] == src and e["dst"] == dst]


def present(edges: list[dict], hop: tuple) -> bool:
    """Whether this hop exists in the graph, ignoring where it was declared."""
    _, type_, src, dst, _, _ = hop
    return bool(find(edges, type_, src, dst))
