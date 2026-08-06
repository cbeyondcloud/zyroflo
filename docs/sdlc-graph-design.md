# Design: `sdlc-graph` — Code Intelligence & Cross-Service Impact for the SDLC Harness

## Status

| Field | Value |
|-------|-------|
| Created | 2026-07-13 |
| Status | 🟡 Design — awaiting approval before build |
| Depends on | The base SDLC harness (`sdlc-init.py`, `.sdlc/` layout) |
| Confirmed inputs | Services connect via **gRPC/protobuf**, **OpenAPI/REST**, and **shared client packages** (no raw-HTTP-only) |

---

## 1. Context & goals

The base harness is a process orchestrator with **no persistent model of code**. It re-explores cold each run and is single-repo scoped, so it cannot answer: *"I changed service1 — do service2/service3 also need changes?"*

`sdlc-graph` adds a **code-intelligence layer** that makes the harness:

1. **Learn as you write** — maintain an always-current structural index (incremental re-parse of changed files). *This is structural indexing, not ML training — stated plainly so nobody expects a self-tuning model.*
2. **Have an ontology** — a **typed property graph** of the code (the pragmatic ontology; RDF/OWL is out of scope).
3. **Expose a dynamic graph** — persisted, queryable, renderable connections within and across repos.
4. **Correlate microservices** — given a change in a provider service, find every downstream consumer that must change, using shared **contracts** and **client packages** as the join keys.

### Non-goals
- Not a runtime tracer (no live traffic capture). Purely static.
- Not a type-perfect call graph for dynamic languages — call resolution is heuristic (name-based). It surfaces **candidates** the harness's requirements phase confirms; it is an aid, not a proof.
- Not a replacement for the LLM's reasoning — it feeds Phase 0.5 with a scoped impact set.

---

## 2. The ontology (typed property graph)

The "ontology layer" is a typed property graph. Nodes and edges:

| Node type | Key attributes | Example |
|---|---|---|
| `Service` | name, path, language, role | service1 |
| `File` | path, lang, content_hash | orders/handler.go |
| `Function`/`Method` | name, signature, file, line | CreateOrder() |
| `Class`/`Type` | name, fields | Order |
| `Endpoint` (provider) | kind(grpc/rest/gql), identity, file:line | `orders.OrderService/CreateOrder`, `POST /orders` |
| `CallSite` (consumer) | target_identity, file:line | service2 calling CreateOrder |
| `Client` | package name, generated? | service1-client |
| `Contract` | kind, path, hash | proto/orders.proto |
| `Config` / `Test` | path | |

| Edge type | Meaning |
|---|---|
| `defines` | File → Function/Class |
| `imports` | File → File/Module |
| `calls` | Function → Function (intra-repo, heuristic) |
| `provides` | Service → Endpoint (server route / rpc impl) |
| `consumes` | CallSite → Endpoint (**cross-service**) |
| `declares` | Contract → Endpoint (proto rpc / OpenAPI operation) |
| `depends_on` | Service → Client package (**cross-service**) |
| `exposes_via` | Endpoint → Client method (generated stub) |
| `tested_by` | Symbol → Test |

This schema is materialised as SQLite tables (`nodes`, `edges`, `files`) — see §4.

---

## 3. Architecture

```
                         sdlc-graph (new tool in the harness repo)
 ┌──────────────┐   index    ┌───────────────┐   link    ┌────────────────────┐
 │ tree-sitter  │──────────▶ │ per-repo graph │─────────▶ │  workspace graph    │
 │ extractors   │            │  .sdlc/graph.db │           │ (unifies services)  │
 │ + contract   │            │  (SQLite)       │           │ via contracts +     │
 │   parsers    │            └───────────────┘           │ client packages      │
 └──────────────┘                    ▲                     └─────────┬──────────┘
        ▲                            │ incremental (hook)             │ impact query
        │ .proto / openapi / gql     │ on changed files               ▼
   repo source files                 │                        ┌────────────────┐
                                      └────────────────────────│ impact report  │→ Phase 0.5
                                                               └────────────────┘
```

**Components (all in the harness repo, isolated deps):**
1. **Extractors** — tree-sitter per-file parse → symbols/imports/calls; plus contract parsers for `.proto`, OpenAPI, GraphQL SDL; plus manifest readers (`go.mod`, `package.json`, `pom.xml`) for package deps.
2. **Provider/consumer taggers** — framework-aware pattern matchers: server routes (FastAPI/Flask/Express/Spring/gRPC impl) → `Endpoint`+`provides`; client call sites (generated stubs, OpenAPI clients, imported client packages) → `CallSite`+`consumes`.
3. **Store** — SQLite (`sqlite3`, stdlib). Per-file `content_hash` enables incremental updates.
4. **Linker** — joins service graphs into a workspace graph using contract identity + package deps.
5. **Impact engine** — maps a diff → changed nodes → reverse-reachability over `consumes`/`depends_on`/`calls`.
6. **CLI** — `sdlc-graph index | impact | query | render`.

---

## 4. Data & storage

- **Store facade:** everything above the store talks to a `GraphStore` interface (`upsert_nodes`, `upsert_edges`, `neighbors`, `nodes_by_kind`, `purge_file`). **One implementation ships: SQLite.** Other backends (Neo4j for very large estates) are a future implementation of the same interface — see §11 for the re-add trigger.
- **Canonical store:** `.sdlc/graph.db` (SQLite) per repo — single file, zero-install, git-ignored (derived artifact).
- **Workspace store:** `.sdlc/workspace.db` at the workspace root, or rebuilt on demand from per-repo DBs.
- **Traversal:** plain-Python BFS over edges by default (zero extra deps); **NetworkX** optional for advanced algorithms/centrality.
- Recursive queries (transitive consumers) via SQLite recursive CTEs or Python BFS.

---

## 5. Cross-service linkage — the microservice correlation

A workspace manifest declares the services and their contracts:

```yaml
# sdlc-workspace.yml (at the multi-repo root)
workspace: my-platform
services:
  - name: service1
    path: ../service1
    provides:
      grpc:    proto/orders.proto          # OrderService/CreateOrder, ...
      openapi: api/openapi.yaml
    client_package: service1-client         # what consumers import
  - name: service2
    path: ../service2
  - name: service3
    path: ../service3
```

Linking rules (why "local-feeling" client calls become visible edges):
1. **Contract identity join** — service1 `declares` `orders.OrderService/CreateOrder`. Consumer taggers in service2/3 emit `CallSite(target_identity="orders.OrderService/CreateOrder")` for generated-stub calls. Match identity → add `consumes` edge across repos.
2. **Package join** — service2's `go.mod`/`package.json` requires `service1-client` → `depends_on` edge; the client's methods `exposes_via` service1's endpoints, so a call into the client resolves to the provider operation.
3. **OpenAPI path join** — REST client calls to `POST /orders` match service1's OpenAPI operation identity.

Result: one **workspace graph** with `provides`/`consumes`/`depends_on` edges spanning service1↔service2↔service3.

---

## 6. Impact algorithm (answers your example)

**Scenario:** you change `CreateOrder` in service1.

1. **Diff → changed nodes.** Map the git diff (`--since` ref) to changed `Function`/`Endpoint`/`Type` nodes via the file:line index.
2. **Contract breaking-change classification** (if a contract changed), using OSS CLIs when present, else the tree-sitter contract parse:
   - protobuf → `buf breaking --against <baseline>`
   - OpenAPI → `oasdiff breaking`
   - GraphQL → `graphql-inspector diff`
   Each changed operation/field is tagged **breaking** or **safe**.
3. **Reverse reachability.** From the changed provider endpoints/fields, traverse `consumes` / `depends_on` / `exposes_via` edges into service2/service3, and intra-repo `calls` edges up to public surfaces.
4. **Impact report:**
   ```
   Change: service1 orders.OrderService/CreateOrder — signature changed (BREAKING)
   Impact:
     service2  clients/orders_client.go:40   calls CreateOrder            → BREAKING, must update
     service3  order/sync.py:88              reads Order.total (int→money) → BREAKING, must update
     service2  tests/test_orders.py:20       fixture uses old CreateOrder  → update test
   ```
5. **Harness wiring — new Phase 0.5 (Impact).** After Phase 0 maps the local change, run `sdlc-graph impact`; fold the impact set into Phase 1 requirements + Phase 2 tracker rows (one per affected consumer), and optionally spawn coordinated `/sdlc` runs in service2/service3.

---

## 7. Harness integration

- **New tool:** `sdlc-graph.py` (harness repo) — `index`, `impact <ref>`, `query "<who calls X>"`, `render`.
- **SKILL:** new **Phase 0.5 Impact** (runs only when `.sdlc/graph.db` or a workspace manifest exists) + a capability-table row.
- **Incremental "learning" hook:** PostToolUse / pre-commit runs `sdlc-graph index --changed` so the graph tracks new code as it is written.
- **profile.yml gains:**
  ```yaml
  graph:
    enabled: true
    db: .sdlc/graph.db
    workspace: ../sdlc-workspace.yml     # blank for single-repo mode
    frameworks: [fastapi, grpc]          # hints for provider/consumer taggers
  ```
- `.sdlc/graph.db` added to `.gitignore` (derived).

---

## 8. Dependency policy (honest footprint)

The base harness stays **stdlib-only**. `sdlc-graph` is an **optional module** with an isolated `requirements-graph.txt`:

| Dependency | Purpose | License | Required? |
|---|---|---|---|
| `tree-sitter` + `tree-sitter-languages` | multi-language parsing (one wheel, ~10 grammars) | MIT/Apache | required |
| `sqlite3` | storage | stdlib | — |
| `buf` (CLI) | protobuf breaking-change detection | Apache-2.0 | optional (fallback: tree-sitter proto diff) |
| `oasdiff` (CLI) | OpenAPI breaking-change detection | Apache-2.0 | optional |
| `graphql-inspector` (CLI) | GraphQL schema diff | MIT | optional |
| `networkx` | advanced graph algorithms | BSD | optional (fallback: plain BFS) |

All permissive, all free. Optional tools are used only if on PATH; the module degrades gracefully.

---

## 9. Honest limitations

- **Dynamic-language call resolution is heuristic** (name-based). High value for impact hints, not a formal proof.
- **Provider/consumer detection is framework-pattern-based** — new frameworks need pattern additions (start with FastAPI, Flask, Express, Spring, gRPC; extend over time).
- **Best fidelity via generated clients** (your gRPC/OpenAPI case) — the generated client is a clean join key. Hand-rolled clients over a shared package still link via the package dep; only raw-HTTP-with-no-spec would be heuristic (you confirmed you don't rely on that).
- **The graph augments, not replaces, the LLM** — Phase 0.5 proposes an impact set; requirements/plan confirm it.

---

## 10. Build plan (tiered) — tracker

| # | Tier | Item | Depends-on | Status |
|---|------|------|-----------|--------|
| 0 | T1 | `GraphStore` facade + SQLite implementation (only backend shipped) | — | ⏳ Pending |
| 1 | T1 | `sdlc-graph.py` skeleton + SQLite schema (nodes/edges/files) + `index` for one language (Python via tree-sitter) | 0 | ⏳ Pending |
| 2 | T1 | Extend extractor to JS/TS, Go, Java; imports + heuristic calls | 1 | ⏳ Pending |
| 3 | T1 | Provider/consumer taggers (FastAPI/Flask/Express/Spring/gRPC) | 2 | ⏳ Pending |
| 4 | T1 | Incremental `index --changed` + PostToolUse/pre-commit hook | 1 | ⏳ Pending |
| 5 | T1 | Intra-repo `impact <ref>` + report; SKILL **Phase 0.5** wiring | 3 | ⏳ Pending |
| 6 | T2 | `sdlc-workspace.yml` + multi-repo linker (contract identity + package deps) | 3 | ⏳ Pending |
| 7 | T2 | Contract breaking-change (buf/oasdiff/graphql-inspector, graceful fallback) | 6 | ⏳ Pending |
| 8 | T2 | Cross-service impact report + coordinated-run proposal | 5,6,7 | ⏳ Pending |
| 9 | T3 | `query` CLI + `render` (Mermaid/Graphviz dependency view) | 5 | ⏳ Pending |
| 10 | — | Fixture workspace (3 tiny gRPC services) + unit tests; validate CreateOrder impact | 8 | ⏳ Pending |
| 11 | 📝 | Docs: README section + `profile.yml graph:` schema + AGENTS note | 8 | ⏳ Pending |

**Status key:** ⏳ Pending · 🔄 In Progress · ✅ Done · ❌ Skipped

---

## 11. Decisions

### Resolved (2026-08-04)

| # | Decision | Choice | Rationale |
|---|---|---|---|
| 1 | **Graph store** | `GraphStore` **facade** with exactly **one** implementation: SQLite | The facade is cheap insurance and keeps the meta-model, impact engine and pack format store-independent. A *second* backend is not cheap — two schemas, two query dialects, two test matrices — and nothing today needs it. Neo4j stays a documented future implementation, not a built one. |
| 1a | Neo4j re-add trigger | An estate where the SQLite store misses its budget: **>100k edges** or **>2s** median impact query | Written down so the choice is revisited on evidence, not taste |
| 2 | **Extractor strategy** | tree-sitter universal | One dependency, many languages; per-language native tools stay an optional accuracy upgrade behind the same extractor SPI |
| 3 | **Scope of first build** | **T1 only** | Prove single-repo impact before spending on cross-service linkage |
| 4 | **Languages first** | Python, then JS/TS | Python is the harness's own language and the second fixture workspace's; JS/TS is the most common second stack |

**Store facade (decision 1) in one line:** `GraphStore` exposes `upsert_nodes/upsert_edges/neighbors/nodes_by_kind/purge_file` — the impact engine calls only these, so swapping SQLite for anything else never touches traversal, packs, or the SKILL.

### Still open

- **Portability proof point.** Settled at Rev 7: the proof is a **second committed fixture workspace of a different stack** (Python/ASGI/compose), not a profile aimed at some particular repository on the author's machine. A real second workspace is still run read-only into `.tmp/` as corroboration, and whether it forced a core edit is recorded — but the *committed* proof has to be something any adopter can re-run, or the portability claim rests on a tree nobody else can see. Open question narrowed to timing: does the second fixture land at the first milestone, or after the first chain is green on the first fixture?
