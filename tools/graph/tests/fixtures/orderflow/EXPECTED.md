# EXPECTED — the hand-written answer key for the `orderflow` fixture

**Written by reading the fixture, before any extractor existed. Never regenerated.**

That sentence is the whole point of this file. A golden test whose expected output was
produced by running the tool asserts only "the tool still does what it did last time" —
it cannot tell a correct extractor from a confidently wrong one, because both produce a
snapshot that matches themselves. This file is a *pre-registration*: the claim is made
first, and the extractors are then held to it.

> **If a test fails against this file, the default assumption is that the extractor is
> wrong.** Editing an expectation to match new output is how a gate becomes a snapshot.
> When the expectation really is wrong, change it in its own commit, with the reasoning,
> and never in the same commit as the code that made it fail.

Line numbers are part of the claim. Provenance that points at the wrong line is a
provenance bug, and it is invisible unless someone asserts the number.

---

## The chain — six hops, four components

This is what the M0 kill gate walks. Each hop is a different *resolution mechanism*, not
just a different file, so cutting any one of them fails for its own distinct reason.

| # | Edge | From → To | Source | Mechanism being tested |
|---|---|---|---|---|
| 1 | `DG_CALLS` | `ui:web-console` → `ep:/orders/v1/list` | `web-console/src/config/urls.js:4` | central URL module (highest confidence) |
| 2 | `DG_EXPOSES` | `svc:orders-api` → `ep:/orders/v1/list` | `OrderController.java:9` + `:12` | context-path + class mapping + method mapping **concatenated** |
| 3 | `DG_PRODUCES` | `svc:orders-api` → `topic:order-events-v1` | `OrderPublisher.java:9` → `application.properties:5` | `@Value` → property → `${VAR:default}` interpolation |
| 4 | `DG_CONSUMES` | `svc:orders-worker` → `topic:order-events-v1` | `OrderConsumer.java:8` → `application.properties:5` | a **different property name** resolving to the same value |
| 5 | `DG_WRITES_TABLE` | `topic:order-events-v1` → `table:order_events` | `orders-worker/config/write-mapping.yml:5-6` | mapping file keyed on filename + topic set |
| 6 | `DG_OWNS_TABLE` | `svc:orders-worker` → `table:order_events` | `V1__create_order_events.sql:2` | migration ownership |

**The property names deliberately do not match.** `orders.publish.topic` and
`orders.consume.topic` are different keys with the same *value*. An extractor that pairs
producers to consumers by key name finds nothing here and reports a topic with a producer
and no consumer — which reads as a real architectural finding rather than as a bug.

### Also expected, outside the chain

| Edge | From → To | Source |
|---|---|---|
| `DG_EXPOSES` | `svc:orders-api` → `ep:/orders/v1/detail/{}` | `OrderController.java:17` |
| `DG_CALLS` | `ui:web-console` → `ep:/orders/v1/detail/{}` | `urls.js:5` **and** `OrderDetail.jsx:4` — see D1 |
| `DG_DEPENDS_ON` | `svc:orders-api` → `mvn:orders-shared` (2.1.0) | `orders-api/pom.xml:6-9` |
| `DG_DEPENDS_ON` | `svc:orders-worker` → `mvn:orders-shared` (2.1.0) | `orders-worker/pom.xml:6-9` |
| `DG_CALLS` | `svc:orders-api` → `ep:/inventory/v1/reserve` | `application.properties:16-17` (host key + path key pair) |

`inventory-api` is **not** a component of this workspace. The call is still an edge — to a
stub node — because "we call something we do not own" is exactly the fact blast radius
must not lose.

---

## Decoys — each one maps to a named core contract

A fixture without decoys measures recall only. These measure precision, and each names the
function whose contract it exercises, so a failure points at code rather than at a mood.

| ID | Decoy | Contract | Required behaviour |
|---|---|---|---|
| **D1** | `/orders/v1/detail/{orderId}` in `urls.js:5` vs `/orders/v1/detail/{id}` in `OrderDetail.jsx:4` | `ids.norm_path` | **ONE** endpoint node. `{orderId}` and `{id}` both normalise to `{}`; two nodes would split every impact query on this endpoint. |
| **D2** | `orders.publish.topic`, `ORDERS_PUBLISH_TOPIC`, `orders-publish-topic` — `application.properties:5,9,10` | `ids.relaxed_key` | **ONE** key. Three spellings of one property must not become three keys, or the graph grows two phantom topics beside the real one. |
| **D3** | `${ORDER_TOPIC:order-events-v1}` — `application.properties:5` | `props.interpolate` | Resolves to `order-events-v1`. The chain is broken if the topic node is named `${ORDER_TOPIC:order-events-v1}`. |
| **D4** | Two chart trees declaring service `orders-api` — `charts/orderflow/values.yaml:6` and `legacy/orders-api/values.yaml:9` | canonical resolution | The collision is **reported**, and `workspace.toml`'s `[[canonical]]` block decides the owner. Detection may never guess: code edges attach to their own variant, config and UI edges to the canonical one, and the non-canonical variant is **damped, not dropped**. |
| **D5** | `/orders/v1/list` in a comment and a mock — `OrderControllerTest.java:6,15` | precision | **No** inbound `DG_CALLS` edge from the test. A `DG_TESTS` edge is correct and expected; a phantom caller is not, and once created it appears in every blast radius forever. |
| **D6** | Malformed YAML — `orders-api/src/main/resources/broken-mapping.yml` | fail-loud | Recorded in `unresolved` with the parse error. The build **does not abort** (one bad file must not kill a workspace scan) and **does not skip silently** (a dropped file drops real edges with no trace). |
| **D7** | Mapping for `shipment-events-v1`, which nothing produces — `orphan-mapping.yml:6` | zero silent drops | Appears in `unresolved`. The topic and table nodes may exist as stubs; what must never happen is the mapping vanishing without a record. |
| **D8** | `${AUDIT_TOPIC}` with no default — `application.properties:14` | fail-loud | Reported unresolved. Never taken literally as the string `${AUDIT_TOPIC}`, which would create a topic node named after a placeholder. |

---

## P5 — the negative control

`legacy-reports` is a real component: it has a build manifest, a properties file, and a
context path. It participates in **nothing** — no shared topic, no shared table, no
endpoint anyone calls, no dependency on `orders-shared`.

**Deleting it must change the node count and leave every edge between other components
byte-identical.** If removing an inert component changes the rest of the graph, the
extractor is inventing links from proximity — the failure mode that makes a graph
plausible and useless, because every query returns something and nothing returned is
trustworthy.

## P6 — no allow-comment

Nothing in this fixture needs an entry in the leak guard's exemption lists. That is a
constraint on the naming, deliberately: a fixture that required an exemption would be
using a name too close to a real one.

---

## Counts

Asserted loosely on purpose. Exact totals would have to be edited on every legitimate
addition, and a number nobody can justify gets updated reflexively until it means nothing.

| | |
|---|---|
| Components | 6 (`orders-api`, `orders-worker`, `orders-shared`, `web-console`, `deploy-charts`, `legacy-reports`) |
| Chain hops | 6, across 4 components |
| Decoys | 8, each mapped to a named contract |
| Inert components | 1 |
| Entries expected in `unresolved` | 3 — D6, D7, D8 |
