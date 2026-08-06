"""The chain of `EXPECTED.md`, in machine-readable form. Not a test module.

One definition, imported by both the golden test and the mutation test. Two copies would
drift, and a drifted copy weakens the gate silently: the golden test would keep asserting
the old chain while the mutation test cut seams in a chain nobody checks.

**This file is a transcription, never a source.** `EXPECTED.md` is the answer key — written
by hand before any extractor existed — and every row here is copied from its table, line
numbers included. If the two disagree, the answer key is right. Changing a row here to make
a test pass is the same act as editing the answer key, done somewhere nobody is looking for
it.
"""
from __future__ import annotations

from tools.graph.core.lib import ids

#: (hop, edge type, source node, target node, file suffix, line) — one row per hop of
#: `EXPECTED.md`'s chain table, in order. The line is part of the claim: provenance pointing
#: at the wrong line is wrong in a way nothing notices unless someone asserts the number.
CHAIN: list[tuple[int, str, str, str, str, int]] = [
    (1, "DG_CALLS", "ui:web-console@web-console", "ep:/orders/v1/list",
     "config/urls.js", 4),
    (2, "DG_EXPOSES", ids.component_service_id("orders-api"), "ep:/orders/v1/list",
     "OrderController.java", 12),
    (3, "DG_PRODUCES", ids.component_service_id("orders-api"), "topic:order-events-v1",
     "OrderPublisher.java", 9),
    (4, "DG_CONSUMES", ids.component_service_id("orders-worker"), "topic:order-events-v1",
     "OrderConsumer.java", 8),
    (5, "DG_WRITES_TABLE", "topic:order-events-v1", "table:order_events",
     "config/write-mapping.yml", 5),
    (6, "DG_OWNS_TABLE", ids.component_service_id("orders-worker"), "table:order_events",
     "V1__create_order_events.sql", 2),
]

#: Which component declares each hop, in order. `source_file` is component-relative and
#: carries no component name, so this cannot be read off the paths.
CHAIN_OWNERS = ["web-console", "orders-api", "orders-api",
                "orders-worker", "orders-worker", "orders-worker"]

#: The component that participates in nothing (`EXPECTED.md` P5, the negative control).
INERT_COMPONENT = "legacy-reports"


def find(edges: list[dict], type_: str, src: str, dst: str) -> list[dict]:
    """Every edge matching one hop's identity — type, source and target."""
    return [e for e in edges if e["type"] == type_ and e["src"] == src and e["dst"] == dst]


def present(edges: list[dict], hop: tuple) -> bool:
    """Whether this hop exists in the graph, ignoring where it was declared."""
    _, type_, src, dst, _, _ = hop
    return bool(find(edges, type_, src, dst))
