"""The three further chains of `EXPECTED.md` (item 14), in machine-readable form. Not a test.

**This file is a transcription, never a source.** Every row is copied from the answer
key's item-14 tables, line numbers included; if the two disagree, the answer key is right.

The three chains are chosen by MECHANISM, and that choice is asserted structurally: the
extractors below are disjoint from the six that own item 4's chain, so each chain fails
for a reason no existing golden test could have caught.

* **Chain A** — manifest identity. Three edges from three manifests must meet at ONE
  module node, with the manifest's own coordinates subtracted.
* **Chain B** — the parse tree. Of a test file's two mentions of an endpoint, only the
  string literal becomes an edge; the comment is the boundary.
* **Chain C** — chart reading. A values field chosen by precedence names the deployed
  service under canonical resolution, and the configmap beside it publishes the host map.
"""
from __future__ import annotations

from tools.graph.core.lib import ids

TEST_FILE = "src/test/java/com/example/orderflow/api/OrderControllerTest.java"

MOD_SHARED = ids.module_id("com.example.orderflow", "orders-shared")
TEST_ID = ids.test_id("orders-api", TEST_FILE)
DEPLOY_CANONICAL = ids.deployment_id("deploy-charts", "charts/orderflow/values.yaml")
DEPLOY_LEGACY = ids.deployment_id("deploy-charts", "legacy/orders-api/values.yaml")
CFG_ID = ids.config_id("deploy-charts", "charts/orderflow/templates/configmap.yaml")
HOST_KEY = ids.env_key_id("ORDERS_API_HOST")

#: (chain, hop, edge type, source node, target node, file suffix, line) — one row per hop
#: of the answer key's three item-14 tables, in order.
CHAIN_A: list[tuple[str, int, str, str, str, str, int]] = [
    ("A", 1, "DG_DEPENDS_ON", ids.component_service_id("orders-api"), MOD_SHARED,
     "pom.xml", 6),
    ("A", 2, "DG_DEPENDS_ON", ids.component_service_id("orders-worker"), MOD_SHARED,
     "pom.xml", 6),
    ("A", 3, "DG_BUILT_FROM", ids.component_service_id("orders-shared"), MOD_SHARED,
     "pom.xml", 3),
]

CHAIN_B: list[tuple[str, int, str, str, str, str, int]] = [
    ("B", 1, "DG_TESTS", TEST_ID, ids.component_service_id("orders-api"),
     "OrderControllerTest.java", 0),
    ("B", 2, "DG_TESTS", TEST_ID, "ep:/orders/v1/list",
     "OrderControllerTest.java", 15),
]

CHAIN_C: list[tuple[str, int, str, str, str, str, int]] = [
    ("C", 1, "DG_DEPLOYS", DEPLOY_CANONICAL, ids.component_service_id("orders-api"),
     "charts/orderflow/values.yaml", 6),
    ("C", 2, "DG_CONFIGURES", CFG_ID, HOST_KEY,
     "charts/orderflow/templates/configmap.yaml", 6),
]

ALL_HOPS = CHAIN_A + CHAIN_B + CHAIN_C

#: The extractors that own item 4's six hops, copied from its chain: any overlap between
#: these and the item-14 extractors would mean a "further" chain is re-walking a mechanism
#: the kill gate already exercises.
ITEM_4_EXTRACTORS = {"frontend-urls", "spring-controllers", "kafka-producers",
                     "kafka-consumers", "orderflow-write-mappings", "sql-migrations"}


def find(edges: list[dict], type_: str, src: str, dst: str) -> list[dict]:
    """Every edge matching one hop's identity — type, source and target."""
    return [e for e in edges if e["type"] == type_ and e["src"] == src and e["dst"] == dst]


def present(edges: list[dict], hop: tuple) -> bool:
    """Whether this hop exists in the graph, ignoring where it was declared."""
    _, _, type_, src, dst, _, _ = hop
    return bool(find(edges, type_, src, dst))
