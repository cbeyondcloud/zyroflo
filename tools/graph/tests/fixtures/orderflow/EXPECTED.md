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

## The chain — six hops, three components

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

## Three further chains — distinct edge classes (item 14)

Item 4's chain walks calls, exposure, messaging and storage. These three walk the edge
classes it does not, **chosen by mechanism rather than by domain**: each one resolves by a
means no hop above uses, so each fails for its own distinct reason. Everything below was
already in the fixture — these sections transcribe what the files say, they add nothing.

### Chain A — the shared-library version chain (`DG_DEPENDS_ON` / `DG_BUILT_FROM`)

The question this class answers: *if I change `orders-shared`, who rebuilds?* The join is
the module node, reached from three different manifests. The mechanism is manifest
identity — groupId + artifactId, with the manifest's **own coordinates subtracted** so a
component can never report itself as its own dependency.

| # | Edge | From → To | Source | Mechanism being tested |
|---|---|---|---|---|
| A1 | `DG_DEPENDS_ON` | `svc:orders-api` → `mod:…:orders-shared` | `orders-api/pom.xml:6` (block opens; version `2.1.0` declared at `:9`) | `<dependency>` block read, own coordinates subtracted |
| A2 | `DG_DEPENDS_ON` | `svc:orders-worker` → `mod:…:orders-shared` | `orders-worker/pom.xml:6` (version `2.1.0` at `:9`) | the same module reached from a second manifest |
| A3 | `DG_BUILT_FROM` | `svc:orders-shared` → `mod:…:orders-shared` | `orders-shared/pom.xml:3` (its own `<artifactId>`) | a manifest's own coordinates become the module it publishes |

A1 and A2 must carry `version: 2.1.0` with `version_source: declared` — both consumers pin
the version the library builds, and that agreement is the version chain. All three edges
must meet at **one** module node; a version bump that split the node would sever the
"who rebuilds?" traversal precisely where it matters.

### Chain B — the spec↔endpoint match (`DG_TESTS`)

A test that names an endpoint is a specification of it, and the mechanism is the **parse
tree**: only a string literal is a claim about behaviour. This file mentions
`/orders/v1/list` twice, and exactly one mention may become an edge.

| # | Edge | From → To | Source | Mechanism being tested |
|---|---|---|---|---|
| B1 | `DG_TESTS` | `test:…/OrderControllerTest.java` → `svc:orders-api` | `OrderControllerTest.java` (whole file; line 0) | a test suite covers its component, by naming convention |
| B2 | `DG_TESTS` | `test:…/OrderControllerTest.java` → `ep:/orders/v1/list` | `OrderControllerTest.java:15` — the string literal | parse-tree literal, confidence 0.8, joining hop 2's endpoint node |

The mention in the comment at `:6` produces **nothing** — that is D5's precision claim,
load-bearing here: read as text this file has two mentions, read as a parse tree it has
one literal, and only the literal is a spec. B2 must join the same endpoint node hop 1
calls and hop 2 exposes, and there must be **no** `DG_CALLS` from this file, ever.

### Chain C — the deployment↔config-key chain (`DG_DEPLOYS` / `DG_CONFIGURES`)

The deployment package is the one place a service's logical name and its published
address are written down together. The mechanism is chart reading: a values field chosen
by precedence names the service, and the configmap beside it publishes the host map.

| # | Edge | From → To | Source | Mechanism being tested |
|---|---|---|---|---|
| C1 | `DG_DEPLOYS` | `deploy:…/charts/orderflow/values.yaml` → `svc:orders-api` | `values.yaml:6` — `name: orders-api` | `service.name` beats the image path in the field precedence; **canonical** per `workspace.toml`, so confidence stays 1.0 |
| C2 | `DG_CONFIGURES` | `cfg:…/templates/configmap.yaml` → `envkey ORDERS_API_HOST` | `configmap.yaml:6` | a ConfigMap `data:` key, line found by forward cursor, value `http://orders-api:8080` |

C1 is the same claim D4 adjudicates — the canonical tree's edge at full confidence while
`legacy/orders-api/values.yaml:9` stays damped at 0.5. C2's key must resolve through
`ids.relaxed_key`, and its value carries the address other services reach `orders-api`
by — which is exactly what makes a stale configmap a finding rather than noise.

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

*Amended at item 79, without weakening the control:* the semantic layer declares an
[[alias]] onto this component's cache table — a bridge EXTRACTION cannot see and must
never invent. Every file under `legacy-reports/` still references none of the shared
names (the file-level pins stand verbatim), the extractors still emit no linking edge,
and deleting the component still leaves every edge between the others byte-identical —
the alias edge dies with its endpoint and becomes a loud unmatched row. The control
now proves the sharper claim: the island stays an island until a HUMAN declares the
bridge, and cutting the declaration re-islands it.

## The spec chain — item 9a, the spec↔endpoint join

`orders-api/src/main/resources/openapi.yaml` is an OpenAPI 3 document, recognised by its
top-level `openapi:` key — by CONTENT, never by filename, because real estates name spec
files after their services. It declares two paths:

| Edge | From → To | Source | Mechanism being tested |
|---|---|---|---|
| `DG_SPECIFIES` | `cfg:orders-api/src/main/resources/openapi.yaml` → `ep:/orders/v1/list` | `openapi.yaml:6` | the path key's own line; joins hop 2's endpoint node **by id** — this endpoint is spec-matched |
| `DG_SPECIFIES` | `cfg:orders-api/src/main/resources/openapi.yaml` → `ep:/orders/v1/{}/history` | `openapi.yaml:12` | `{orderId}` erased by `norm_path` — `/orders/v1/{orderId}/history` and `/orders/v1/{id}/history` are one endpoint; this one is specified and served nowhere, and MUST NOT gain a `DG_EXPOSES` |

The join is the deliverable: `ep:/orders/v1/list` now carries both `DG_EXPOSES` (hop 2)
and `DG_SPECIFIES`, which is what "spec-matched" means, and the second path is the
precision decoy — a spec is a claim about intent, not evidence that anything serves it.

`broken-mapping.yml` sits in the same directory and starts with a comment and a `topic:`
key: it must NOT be sniffed as a spec, and the openapi extractor must add no second
unresolved row for it — D6 already owns that file's story.

## The module chain in a second build system — item 9b

`orders-notifier` is gradle-built: a root manifest plus one subproject, tied into one
build by `settings.gradle`. Groovy is code, not data — declarations are read at face
value and never evaluated, so every claim below is a line a person can point at.

| Edge | From → To | Source | Mechanism being tested |
|---|---|---|---|
| `DG_BUILT_FROM` | `svc:orders-notifier` → `mod:com.example.orderflow:orders-notifier` | `build.gradle:1` — the `group` declaration; the name is `settings.gradle:1`'s `rootProject.name` | declared group + settings-declared name make a publishable module |
| `DG_BUILT_FROM` | `svc:orders-notifier` → `mod:com.example.orderflow:notifier-core` | `notifier-core/build.gradle` (whole file; line 0) | positional identity — the subproject's name is its directory, its group inherited from the build root, and nothing in the file declares either |
| `DG_DEPENDS_ON` | `svc:orders-notifier` → `mod:com.example.orderflow:orders-shared` (`2.1.0`, `declared`) | `build.gradle:5` | **A4 — Chain A's module node reached from a SECOND build system.** The join is coordinates, not file format: a maven pom and a gradle script naming the same group:artifact must meet at one node, or "who rebuilds?" splits by build tool |
| `DG_DEPENDS_ON` | `svc:orders-notifier` → `mod:org.springframework.kafka:spring-kafka` (version blank, `inherited`) | `build.gradle:7` | a version left to a BOM or platform reads as inherited, never invented |
| `DG_DEPENDS_ON` | `svc:orders-notifier` → `mod:org.apache.kafka:kafka-clients` (`3.7.0`, `declared`) | `notifier-core/build.gradle:2` | a subproject's dependencies belong to the component's one service |

**`build.gradle:6` produces nothing.** It names `com.example.orderflow:notifier-core`,
which `settings.gradle:2` declares as this build's own module — the maven trap ("eight
components consuming their own submodules") in gradle spelling. The subtraction must eat
it: Chain A's meeting point grows by exactly one manifest, not two.

## The dependency layer in a third manifest format — item 9c

npm names are REGISTRY-GLOBAL — `jest` names one package everywhere — so unscoped
modules merge across components by design: the deliberate inverse of 9b's scoping,
because a gradle positional name is a directory accident and an npm name is a claim.

| Edge | From → To | Source | Mechanism being tested |
|---|---|---|---|
| `DG_BUILT_FROM` | `svc:web-console` → `mod:…:ui-kit` | `packages/ui-kit/package.json:2` | a workspace package is a module of the component that holds it |
| `DG_BUILT_FROM` | `svc:web-console` → the `web-console` module | `package.json:2` | the app manifest publishes too — `private` hides it from a registry, not from the graph |
| `DG_DEPENDS_ON` | `svc:web-console` → the `jest` module (`29.7.0`, devDependencies) | `package.json:14` | scope is the section name; a dev-only dependency is still a rebuild trigger |
| `DG_DEPENDS_ON` | `svc:web-console` → the `date-fns` module (`^3.6.0`) | `packages/ui-kit/package.json:5` | the spec is kept RAW — `^3.6.0` declares a range, and rewriting it to a version would invent precision |

**`package.json:11` produces nothing.** `@orderflow/ui-kit` is a name this component
itself declares — the maven/gradle trap in npm spelling: a workspace sibling consumed
by name is intra-component structure, not a dependency.

## The Go-templated chart — item 87

`tpl-configmap.yaml` is what real chart templates are: YAML skeletons with `{{ … }}`
spans. De-templating is line-preserving, so line 5's control construct must NOT shift
what follows.

| Edge / row | Detail | Source |
|---|---|---|
| `DG_CONFIGURES` | `envkey NOTIFIER_HOST`, value `http://orders-notifier:8085` — the literal majority a renderer was never needed for | `tpl-configmap.yaml:7` |
| `templated-value` row | `NOTIFIER_TOKEN`'s value is a template span — the key is real, its address is not knowable statically; NO edge, and the raw line is the record | `tpl-configmap.yaml:8` |

The configmap's own `name:` is templated (line 4) — `""` is the honest unnamed state,
never the token.

---

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
| Components | 7 (`orders-api`, `orders-worker`, `orders-shared`, `web-console`, `orders-notifier`, `deploy-charts`, `legacy-reports`) |
| Chain hops | 6, across 3 components — `web-console` (1), `orders-api` (2, 3), `orders-worker` (4, 5, 6), with two component boundaries crossed |
| Further chains (item 14) | 3 — A: 3 edges meeting at one module node (a **4th manifest** joins it in item 9b); B: 2 edges from one test file; C: 2 edges from one chart tree |
| Spec chain (item 9a + 93) | 3 `DG_SPECIFIES` edges from one spec file — one joining a served endpoint by id, one specified-and-unserved |
| Module chain (item 9b) | 2 `DG_BUILT_FROM` + 3 `DG_DEPENDS_ON` from one gradle build of two manifests — one dependency joining Chain A's module node from a second build system, one subtracted as the build's own submodule |
| Dependency layer (item 9c) | 2 `DG_BUILT_FROM` + 2 `DG_DEPENDS_ON` from two npm manifests — unscoped names merge registry-globally, one workspace sibling subtracted |
| Decoys | 8, each mapped to a named contract |
| Inert components | 1 |
| Producer layer (item 91) | 3 `DG_PRODUCES` from one caller file through a wrapper and a config class — see the item 91 section |
| Entries expected in `unresolved` | 5 — D6, D7, D8, item 87's `templated-value`, and item 91's caller-parameter row. *(Recorded transitional states: 6 at item 91's answer key, 9 at item 92's.)* |
| Call pairs (item 92) | 2 `DG_CALLS` — one the VALUES declare, one only the code's concatenation does — see the item 92 section |
| Generated tree (item 78) | 1 stamped `DG_PRODUCES` + 1 stamped `cfg` node with its `DG_IN_REPO` edge, all under `orders-api/gen/`; the P1–P3 siblings stay unstamped — see the item 78 section |
| Semantic layer (item 79) | 2 `entrypoint` node stamps + 1 `DG_ALIAS_OF` edge from the workspace declarations, plus 1 ordinary `DG_OWNS_TABLE` from the legacy cache migration — see the item 79 section |

---

## Item 92 — a host is what holds an address, a path is what starts with a slash

Written before the extractor. The measured defect this transcribes: key-suffix
classification is value-blind — on a real estate a third of "unpaired hosts" were PATH
values living under `.url`/`.endpoint` keys, unpaired precisely because their real host's
partner had been mislabelled a host itself. Values decide; suffixes remain a hint only
where a value cannot speak (a `${...}` placeholder).

| # | edge | source → target | file:line | conf | mechanism |
|---|---|---|---|---|---|
| H1 | `DG_CALLS` | `svc:orders-api` → `ep:/billing/v1/_search` | `application.properties:27` | 0.9 | value-shape pair: `billing.core.host` holds an address, `billing.core.search.url` holds a PATH despite its suffix, same prefix |
| H2 | `DG_CALLS` | `svc:orders-api` → `ep:/reports/v1/list` | `ReportsClient.java:20` | 0.9 | getter-concat: `reportingHost` (same-class `@Value`, `${reporting.host}`) + `pathCfg.getSearchListPath()` (`${search.list.path}` via item 91's binding layer) — prefixes unrelated, paired only where the code joins them |

**Negatives.** `cache.store.url` holds `redis://…`: not a rest host — excluded from this
fact class entirely, no edge and NO row (a scheme this extractor does not speak is not a
dangling address). `reporting.host` gains no properties-pair row: the concat pair is its
resolution. Four transitional rows at the answer-key commit (properties lines 26–29) are
the pre-extractor state of exactly these facts.

---

## Item 93 — a spec writes the path context-free; the controller serves it under one

The measured gap: on a real estate 195 of 471 spec-only paths equal an exposed path
minus its one leading segment — the context path the controller bakes in and the spec
leaves to `servers:`. The fixture transcribes it: `openapi.yaml:18` declares
`/v1/detail/{orderId}` context-free, and the serving controller exposes
`/orders/v1/detail/{}` under the `/orders` context (`application.properties:2`).

| # | edge | source → target | file:line | conf | mechanism |
|---|---|---|---|---|---|
| S1 | `DG_SPECIFIES` | spec → `ep:/orders/v1/detail/{}` | `openapi.yaml:18` | 0.8 | context-suffix alignment: the exposed path is exactly ONE segment (`/orders`) plus the spec path, and no other exposed endpoint matches |

**Negatives, both load-bearing.** `/orders/v1/{}/history` (line 12) stays spec-only —
no exposed path is one-segment-plus-it, and aligning it anyway would erase the fixture's
specified-and-unserved decoy, which exists to catch exactly that overreach. The
alignment is ONE segment and UNIQUE: a spec path whose suffix matches two exposed
endpoints is a recorded row, never a guess. The original `ep:/v1/detail/{}` node and its
edge remain — the spec really declares that string; alignment adds the join, it never
rewrites the claim.

---

## Item 91 — production through a wrapper and a config class

Written before the extractor, like everything here. The estate idiom this transcribes:
services call a component-local wrapper, not the client, and topic names live in
`@Value`-bound config classes reached through getters. Three files:
`EventGateway.java` (the wrapper — its `push(destination, payload)` FORWARDS a parameter
into `kafkaTemplate.send`, and that forwarding is what identifies it; the parameter is
deliberately not named "topic"), `NotifyConfig.java` (two bindings: `notifyTopic` with a
written-out getter, `streamTopic` reachable only through Lombok's class-level `@Getter`,
so the mapping is the `getX -> x` convention), and `AuditTrail.java` (the callers).

| # | edge | source → target | file:line | conf | resolution chain |
|---|---|---|---|---|---|
| P1 | `DG_PRODUCES` | `svc:orders-api` → `topic:order-notify-v1` | `AuditTrail.java:18` | 0.9 | wrapper call → `getNotifyTopic` → `notifyTopic` ← `${orders.notify.topic}` (`NotifyConfig.java:15`) → `application.properties:20` |
| P2 | `DG_PRODUCES` | `svc:orders-api` → `topic:order-audit-raw` | `AuditTrail.java:19` | 1.0 | wrapper call with a string literal |
| P3 | `DG_PRODUCES` | `svc:orders-api` → `topic:order-stream-v1` | `AuditTrail.java:23` | 0.9 | direct client send → `getStreamTopic` (convention only) → `${orders.stream.topic}` (`NotifyConfig.java:18`) → `application.properties:21` |

Getter-mediated edges carry confidence 0.9: the `getX -> x` name convention is the one
link in the chain that is convention rather than declaration. Everything else is 1.0.

**And two negatives, both load-bearing.** `EventGateway.java:15` — the wrapper's own
forwarding send — is NOT an unresolved row once the extractor lands: it is consumed as
the fact that defines the wrapper, and its former noise (one junk row per wrapper per
component, measured at scale on a real estate) moves to the caller sites where the
answer actually is. `AuditTrail.java:24` passes the caller's own method parameter into
the wrapper: resolvable only by whole-program dataflow, which this extractor does not
do — ONE level of forwarding is the stated bound — so it stays a recorded
`unresolved-topic` row, raw `routed`.

---

## Item 78 — generated code: the declaration changes what facts mean, never whether they exist

Measured before designed: on a real estate 577 files carry generated-by markers across
21 of 26 components, ZERO follow the name conventions the plan text imagined
(`*_pb2.py`, `*.pb.go`, `.min.js`), and the graph facts citing them are dominated by
generated deployment config (758 of 931 marker-cited edges), not client code. So
nothing is detected and nothing is dropped: the workspace DECLARES its generated
trees, extraction reads them exactly as before, and a link pass stamps every fact
whose citing file lies under a declared tree.

| # | fact | file:line | stamp | mechanism |
|---|---|---|---|---|
| G1 | `DG_PRODUCES` `svc:orders-api` → `topic:order-audit-raw` | `gen/EventsRelay.java:17` | `generated: true` + `regenerate_from` | the edge an extractor reads beautifully from a file no human edits — confidence untouched, meaning stamped |
| G2 | the `cfg` node for `gen/generated.properties` and its `DG_IN_REPO` edge | `gen/generated.properties` | same stamp | the measured dominant estate case is generated CONFIG, so the fixture's generated tree holds config too |

`regenerate_from` is `orders-api/src/main/resources/openapi.yaml` — recorded verbatim
from the declaration, validated to exist at discovery, and rendered by impact as
"regenerate, don't edit" instead of naming a file nobody should touch.

**Negatives, all load-bearing.** `AuditTrail.java`'s producer edges (P1–P3) carry NO
`generated` key — a sibling outside the declared subtree is untouched, and the key is
absent, not false. The DO-NOT-EDIT header inside `EventsRelay.java` is inert: delete
the `[[generated]]` block and every stamp vanishes while the header stays — the
declaration decides, markers never do. And the stamp changes neither confidence nor
canonicality: a generated fact is not a doubtful fact, it is a fact whose edit site
is elsewhere.

---

## Item 79 — the semantic layer: declared facts a scanner cannot reach

Written before the pass existed. Two declared-fact classes, each obeying the item's
contract: it changes a ranking or a traversal (or it would be decoration), and it is
distinguishable in every serving surface from an extracted fact — `props.declared`
plus a citation into the DECLARATION file, because a hand-declared edge that blends
into extraction kills the provenance guarantee exactly where a human started typing.

| # | fact | source → target | file:line | conf | mechanism |
|---|---|---|---|---|---|
| S1 | node stamp | `ep:/orders/v1/list` gains `entrypoint: true` + the note | `workspace.toml:97` | — | `[[entrypoint]]` names the node id; the stamp changes props only |
| S2 | node stamp | `ui:web-console@web-console` gains `entrypoint: true` | `workspace.toml:101` | — | same; a ui surface is a front door extraction cannot rank |
| S3 | `DG_ALIAS_OF` | `table:order_events` → `table:order_reports_cache` | `workspace.toml:111` | 1.0 | `[[alias]]` — one business entity, two names, the bridge is an out-of-git ETL job; EXTRACTED (read from the declaration file), `declared: true` |
| S4 | `DG_OWNS_TABLE` | `svc:legacy-reports` → `table:order_reports_cache` | `V1__create_order_reports_cache.sql:5` | 1.0 | ordinary extraction — the cache table is a real table; only the JOIN to the entity is declared |

**What the declarations must change.** The chain's radius (any golden-chain seed)
must reach `table:order_reports_cache`, the legacy migration file, and
`svc:legacy-reports` ONLY while the `[[alias]]` block exists — built with the block
removed, no legacy row appears at any hop: the declaration is load-bearing, not
annotation. Entry-point stamps must ride every serving surface (ranked services,
pack contracts, node listings) as a visible flag, and stamped nodes spread through
traversal without hub attenuation — the front door fans by design.

**Negatives.** The alias edge itself mints NO file row — its citation lives in the
declaration file, which belongs to no component. The stamps change neither
confidence nor canonicality (item 78's rule: a declared fact is not a doubtful
fact). P5's file-level pins hold verbatim: no file under `legacy-reports/` names any
shared token — the bridge exists ONLY as the declaration. A declaration naming a
node absent from the graph stamps nothing and creates nothing: it becomes an
unresolved row naming the side that missed, because a typo that silently does
nothing is how a front door goes undeclared. An alias side that resolves to a
component-OWNED node is refused as a row too — entities are the shared nodes;
service identity is `[[canonical]]`'s job and config seams are the extractors'.

---

## Item 39 — the dependent-build closure: who rebuilds when the shared library changes

**Read off the manifests by hand, before the engine that computes it existed.** Nothing
here was produced by running `suite.py`.

`orders-shared` publishes one module, `com.example.orderflow:orders-shared`
(`orders-shared/pom.xml:2-3`). Three components declare it, from three manifests in **two
build systems** — the property that makes this cross-*repo* rather than cross-module:

| Dependent | Declared at | Build system |
|---|---|---|
| `orders-api` | `orders-api/pom.xml:6` | maven |
| `orders-worker` | `orders-worker/pom.xml:6` | maven |
| `orders-notifier` | `orders-notifier/build.gradle:5` | gradle |

A change to `orders-shared` must build and test **exactly** `orders-shared`, `orders-api`,
`orders-worker`, `orders-notifier` — the publisher **first**, because a dependent built
before what it depends on is not a dependent build.

**What it must NOT reach is as much of the claim.** `legacy-reports` publishes
`com.example.orderflow:legacy-reports` (`pom.xml:2-3`) and nobody declares it;
`web-console` publishes `web-console` and `orderflow:ui-kit`, declared by nobody;
`deploy-charts` publishes nothing. A closure that reaches everything has computed nothing.

**Three negatives, each for its own reason.** (1) `build.gradle:6` declares
`notifier-core`, published by `notifier-core/build.gradle` — the same component both ends,
so it must not become its own dependent. (2) `kafka-clients`, `spring-kafka`, `date-fns`
and `jest` are consumed here and published by nobody here: they must drop out because the
join finds no publisher, and if a vendor prefix list is ever needed the join is wrong.
(3) Module identity is groupId + artifactId, so bumping `orders-shared`'s `<version>`
(`pom.xml:4`) must leave this table byte-identical — a version bump is the commonest
shared-library change there is, and an engine that recomputed the closure from one would
answer a different question every release.
