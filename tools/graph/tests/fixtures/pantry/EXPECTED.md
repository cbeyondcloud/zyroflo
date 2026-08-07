# EXPECTED — the hand-written answer key for the `pantry` fixture

**Written by reading the fixture, before any of its extractors existed. Never regenerated.**

Same discipline as the first fixture, and the same reason: a golden test whose expected
output was produced by running the tool asserts only "the tool still does what it did last
time", and cannot tell a correct extractor from a confidently wrong one. Both produce a
snapshot that matches themselves.

> **If a test fails against this file, the default assumption is that the extractor is
> wrong.** When the expectation really is wrong, change it in its own commit, with the
> reasoning, and never in the same commit as the code that made it fail.

Line numbers are part of the claim.

`svc:pantry-api` here is shorthand for the full `svc:pantry-api@pantry-api` that
`ids.component_service_id` produces; the tests assert the full form.

---

## What this fixture is FOR, which is not what the first one is for

The `orderflow` fixture proves the chain resolves. **This one proves the core did not have
to change to make a different stack resolve.** Rev 7 stated the limitation honestly: one
committed fixture can always be accommodated by the code that reads it, so "core is
generic" is only genuinely proven by the second.

**The gate is therefore not "the chain is green". It is "the chain is green AND
`tools/graph/core/` is byte-identical."** A green chain bought with a core edit is a
failure that looks like a pass — it means the SPI boundary was drawn around one stack, and
every subsequent adopter pays for it.

Three things this workspace deliberately does NOT share with the first one:

* **No profile.** Every format here — pyproject, compose, SQL, Python — is something a
  generic tool can read. The profile layer exists for formats one organisation invented,
  and this workspace invented none. A second fixture that quietly needed a profile would
  prove the opposite of what it exists to prove.
* **Configuration lives OUTSIDE the component that reads it.** In `orderflow` a service's
  values sit in its own `application.properties`. Here they sit in the deployment layer, so
  an extractor confined to one component's files stops one hop short.
* **A different interpolation grammar.** Compose writes `${VAR:-default}`; the first fixture
  writes `${VAR:default}`. A reader that assumes one grammar creates an entity named after
  the placeholder — the exact failure D3 exists to catch, in a syntax that would slip past
  a check written for the other one.

---

## The chain — five hops, three components

| # | Edge | From → To | Source | Mechanism being tested |
|---|---|---|---|---|
| 1 | `DG_CALLS` | `svc:restock-worker` → `ep:/pantry/v1/items` | `client.py:11` + `client.py:13`, host value from `docker-compose.yml:18` | host key + path literal paired, with the host resolved from the **deployment layer** |
| 2 | `DG_EXPOSES` | `svc:pantry-api` → `ep:/pantry/v1/items` | `routes.py:10` + `routes.py:13` | router **prefix** + decorator path **concatenated** |
| 3 | `DG_PRODUCES` | `svc:pantry-api` → `topic:restock-events-v2` | `publisher.py:27` → `:18` → `docker-compose.yml:10` | client call → module constant → env read → compose block → `${VAR:-default}` |
| 4 | `DG_CONSUMES` | `svc:restock-worker` → `topic:restock-events-v2` | `consumer.py:19` → `:13` | a **different key name** whose value comes from an **inline default**, not from compose |
| 5 | `DG_OWNS_TABLE` | `svc:restock-worker` → `table:restock_events` | `0001_create_restock_events.sql:2` | migration ownership, from a **Python-idiomatic migration path** |

**The two sides of the queue meet at the VALUE and nowhere else.** The publisher's name
arrives from the deployment layer; the consumer's from a default written in code. They
share no key, no file and no directory. An extractor that pairs them by key name finds
nothing and reports a queue with a producer and no consumer — which reads as a real
architectural finding rather than as a bug.

**Hop 2 is a genuinely different assembly than the first fixture's.** There, three
annotations concatenate. Here, the prefix is an *argument to a constructor* held in a
module-level variable, and the path is a *decorator argument*. Reading only the decorator
yields `/items`, which matches nothing any caller requests — so the graph would show
endpoints nobody calls beside calls nobody serves, and both halves look like findings.

### Also expected, outside the chain

| Edge | From → To | Source |
|---|---|---|
| `DG_EXPOSES` | `svc:pantry-api` → `ep:/pantry/v1/items/{}` | `routes.py:18` |
| `DG_CALLS` | `svc:restock-worker` → `ep:/pantry/v1/items/{}` | `client.py:14` — see E1 |
| `DG_DEPENDS_ON` | `svc:pantry-api` → `mod:pypi:pantry-shared` (3.4.0) | `pantry-api/pyproject.toml:6` |
| `DG_DEPENDS_ON` | `svc:restock-worker` → `mod:pypi:pantry-shared` (3.4.0) | `restock-worker/pyproject.toml:6` |
| `DG_TESTS` | `test:pantry-api/tests/test_routes.py` → `svc:pantry-api` | `test_routes.py` |

**On `mod:pypi:pantry-shared`, because it is where a core edit would be forced.**
`ids.module_id(group, artifact)` takes a two-part coordinate, which is a Maven shape. A
PyPI distribution has ONE name. The registry qualifies it — `pypi` is the group — which is
the same move npm scopes already make, so the existing signature holds. **If it had not
held, the honest answer would be a core edit and a recorded failure of this gate, not a
mangled id.** This is stated in advance so that outcome cannot be quietly avoided.

---

## Decoys — each one maps to a named contract

| ID | Decoy | Contract | Required behaviour |
|---|---|---|---|
| **E1** | `/pantry/v1/items/{sku}` at `routes.py:18` vs `/pantry/v1/items/{item_sku}` at `client.py:14` | `ids.norm_path` | **ONE** endpoint node. Two would split every impact query on this endpoint. |
| **E2** | `RESTOCK_QUEUE`, `restock.queue`, `restock-queue` — `settings.py:9,10,11` | `ids.relaxed_key` | **ONE** key, `envkey:restock.queue`. Three spellings must not become three keys. |
| **E3** | `${QUEUE_NAME:-restock-events-v2}` — `docker-compose.yml:10` | interpolation | Resolves to `restock-events-v2`. **Note the `:-`**: this grammar is not the first fixture's, and a reader written for that one produces a topic named after the placeholder. |
| **E4** | Two compose files declaring service `pantry-api` — `docker-compose.yml:7` and `legacy/docker-compose.yml:12` | canonical resolution | Reported, and decided by `workspace.toml`'s `[[canonical]]`. **The point of repeating D4 in a different deployment technology:** a link pass that only resolves collisions expressed as charts was never resolving collisions — it was reading charts. |
| **E5** | `/pantry/v1/items` in a comment and a mock — `test_routes.py:7,16` | precision | **No** inbound `DG_CALLS` from the test. A `DG_TESTS` edge is correct; a phantom caller appears in every blast radius forever. |
| **E6** | Malformed YAML — `deploy-compose/docker-compose.override.yml` | fail-loud | Recorded in `unresolved` with the parse error. The build **does not abort** and **does not skip silently**. |
| **E7** | `LEDGER_ENDPOINT` — `publisher.py:21`, read and set by nothing anywhere | zero silent drops | Appears in `unresolved`. A key read with no value is a real hole in the deployment, and dropping it hides exactly the thing worth knowing. |
| **E8** | Commented-out `CREATE TABLE pantry_items` — `0001_create_restock_events.sql:9` | `sqlmigrations._blank_comments` | **Not** owned. A commented statement is not a creation, and the comment must be blanked **without shifting any line number** — the real `CREATE TABLE` above it is cited at line 2. |

---

## P5 — the negative control

`label-printer` is a real component: build manifest, environment reads, same language as
everything else. Nothing about its **shape** marks it inert. It shares no queue, no table,
no endpoint, no dependency.

**Deleting it must change the node count and leave every edge between other components
byte-identical.**

## The coverage hole, which is a finding and not a failure

`ops-runbook` is matched by **no extractor at all**. It must be named in the report, and the
build must still exit 0. "Nothing matched this component" and "this component has no
couplings" are different answers, and only the second is legitimate.

## P6 — no allow-comment

Nothing here needs an entry in the leak guard's exemption lists.

---

## Pre-registered prediction: which layer will have to change

Recorded **before writing any extractor**, so that "the core held" is a prediction that
survived rather than a description written afterwards. Being wrong here is a finding, not
an embarrassment.

| Prediction | Layer | Why |
|---|---|---|
| Three new extractors: `env_keys`, `compose`, `http_routes` | **stack** | Each is about a technology, not about this workspace. Any adopter using compose inherits the compose one. |
| The SQL-migration extractor must learn `migrations/*.sql` | **stack** | Its globs were `V*__*.sql` and `db/migration/*.sql` — one runner's conventions. This fixture uses the numbered layout nearly everything else uses, and those globs matched **none** of it. Smoothing the fixture to fit the extractor would be the fixture accommodating the code, which is the failure this whole file exists to prevent. **Confirmed: it read zero files, and the globs were widened in 19d.** |
| That extractor is misnamed | **stack** | The package was `flyway/` — one ecosystem's migration runner — but nothing in it is specific to that tool or its language: it reads `CREATE TABLE`, which is SQL. **Confirmed and renamed to `sqlmigrations/` in 19d**, once a second stack made the cost visible: the name told every adopter of a different runner that their migrations were unsupported, and the globs had grown to match, so the claim was quietly making itself true. |
| `tools/graph/core/` | **unchanged** | This is the gate. Any diff under `core/` fails item 19 regardless of whether the chain is green. |

---

## Counts

Asserted loosely on purpose.

| | |
|---|---|
| Components | 6 (`pantry-api`, `restock-worker`, `pantry-shared`, `deploy-compose`, `label-printer`, `ops-runbook`) |
| Chain hops | 5, across 3 components, with two component boundaries crossed |
| Decoys | 8, each mapped to a named contract |
| Inert components | 1 (`label-printer`) |
| Components matched by nothing | 1 (`ops-runbook`) |
| Entries expected in `unresolved` | at least 2 — E6, E7 |
