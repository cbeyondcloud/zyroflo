# Bake-offs (M0, tracker item 5)

**Status:** decided at M0 · **Decided:** 2026-08-06 · **Both decisions are "do not adopt"**

Two adoption questions the plan left open, each with a defined alternative that already exists
in this repository. The bar in both cases is **"beats what exists"** — not "beats a design" —
because in both cases something is already working and covered by tests.

A bake-off recorded without its losing evidence is a press release. Each entry below states
what was measured, **what was not measured and why**, and the trigger that re-opens it.

> **Honesty about pre-registration.** `judging-protocol.md` is pre-registered: its criteria
> were fixed before any evidence existed. **Only one of the two bake-offs below has that
> property.** The symbol-layer gate was pre-registered in the plan and is reported against its
> own stated rule, including the part where it could not be run. The installer bake-off had no
> criterion beyond "beats what exists", so the criteria in §2 are stated *at decision time* —
> after seeing the incumbent. That is weaker evidence and it is labelled rather than dressed up.

---

## 1. Symbol layer: adopt CodeGraphContext, or ship `NullSymbolLayer`?

**Decision: ship `NullSymbolLayer`. CGC is neither adopted nor rejected — its gate moves to M2,
where it can be run against a question that has an answer.**

### The pre-registered criterion, and the fact that it did not run

The plan fixed this gate in advance:

> Index the largest single component in the developer's workspace in under 10 minutes, with
> its main controller class queryable and at least one CALLS edge present; **fail → ship
> `NullSymbolLayer`** (no tree-sitter rewrite in v1).

**It was not run.** It requires three things M0 does not have: a real workspace of a size where
a 10-minute budget means anything, a running graph store, and the dependency installed. Under
the rule as written, an unrun gate is not a pass — the fallback is what ships.

**It was deliberately not run against the fixture.** The fixture's largest component is six
small source files; indexing it in under ten minutes proves nothing about the budget the gate
exists to test, and reporting it as a pass would be the fixture tautology this project has
already written down twice. A criterion satisfied by an input chosen to satisfy it is not a
criterion.

### What M0 *did* measure, and it is the stronger evidence

The six-hop chain that gates M0 resolves with **no symbol-layer fact whatsoever**:

| Hop | Edge | Produced by | A call graph could find it? |
|---|---|---|---|
| 1 | `DG_CALLS` | `frontend-urls` | No — a literal in a URL table |
| 2 | `DG_EXPOSES` | `spring-controllers` | No — annotations plus a properties file |
| 3 | `DG_PRODUCES` | `kafka-producers` | Partly — the *call* is visible, the *topic* is not |
| 4 | `DG_CONSUMES` | `kafka-consumers` | No — an annotation argument resolved through config |
| 5 | `DG_WRITES_TABLE` | `orderflow-write-mappings` | No — a config format, no code involved |
| 6 | `DG_OWNS_TABLE` | `sql-migrations` | No — a migration script |

Mechanically checked: **no extractor, link pass or profile in the shipped tree references the
symbol layer at all.** This is the plan's config-seam thesis stopping being a claim — the
impact truth in the chain lives in the seams, and hop 3 is the sharpest illustration, because
a call graph *can* see `template.send(topic, payload)` and still cannot say what `topic` is.

### What adopting would cost, now

* A pinned dependency and a second extraction core, which the plan already rejected on
  accuracy grounds ("a second extraction core is an accuracy risk").
* **A bridge that no longer matches the design.** Part B's `bridge_symbols` pass assumed CGC
  shares this project's Neo4j, so a `:DG` label namespace would keep the two apart and a
  post-load pass could stamp `dg_service_id` onto its file nodes. CGC's current default is an
  **embedded** store — Neo4j is one optional backend among several — so the bridge is now a
  cross-*store* join rather than a cross-label one. That is a different piece of work than the
  one the plan costed, and nobody had noticed.
* Confirmed against the project directly: it exposes **no API for custom node types**, only
  its own symbol and call graph. The plan's research verdict holds.

### What is NOT being claimed

CGC is a healthy, actively maintained MIT project covering 23 languages, and none of the above
says it is bad or useless. It says its value here is **intra-service seed refinement at M2** —
a diff hunk to a function to its callers — and that value cannot be measured before the impact
engine that would consume it exists. Adopting now would be buying a dependency on the strength
of an argument rather than a number.

**The seam costs nothing to keep.** `SymbolLayer` is a two-method protocol and
`NullSymbolLayer` is eight lines that answer nothing and break nothing. Adopting later is an
adapter, not a rewrite — which is the entire reason the seam was built before the decision.

### Re-open triggers

* **M2 (primary).** With hunk-level seeds live, run the pre-registered gate against a real
  workspace and score it on the question that matters: does function-level refinement change
  `precision@k` against corpus ground truth? A yes there is a number; a yes here would be a
  preference.
* A workspace whose impact truth genuinely *is* the call graph — a monolith with few config
  seams — where the chain this gate walks would be unbuildable.
* Any need for intra-service path enrichment that `srcfacts` cannot supply. Note this bar rose
  since the plan was written: the fallback was "no tree-sitter rewrite in v1", and tree-sitter
  shipped anyway at item 2, so structured source facts are already available without CGC.

---

## 2. Skill installer: adopt graphify's, or keep the fan-out `sdlc-init.py` already ships?

**Decision: keep the incumbent. Do not adopt.** Its platform breadth is a real gap and is
recorded as one.

### Criteria — stated at decision time, not pre-registered

The first is a gate the project already has; the rest are properties the incumbent demonstrably
has, so they are scored fairly only with that caveat attached.

| # | Criterion | Why it is not a preference |
|---|---|---|
| C1 | **Zero dependencies, no network, no runtime beyond stdlib Python** | Tier 0's headline promise and a *recurring* gate (item 62, required green M1–M6). Not negotiable. |
| C2 | **Never clobbers a developer's own files** | It writes into `CLAUDE.md` and `AGENTS.md`, which are theirs. |
| C3 | **Separable from any extraction engine** | The plan already rejected vendoring a second extraction core. |
| C4 | Platform breadth | More targets is straightforwardly better. |
| C5 | Single-file distribution | `--bundle` exists so the installer can be dropped into a repo with nothing else. |

### Measured

| | Incumbent (`sdlc-init.py`) | Challenger (graphify) |
|---|---|---|
| C1 | **Stdlib only** — verified by parsing its imports: `argparse, base64, datetime, difflib, json, pathlib, re, sys, tomllib`, zero third-party | **Fails.** A PyPI install (`uv tool install graphifyy`), Python 3.10+, and the CLI must exist before `graphify install` runs |
| C2 | Idempotent `BEGIN/END` block merge; a re-run stages `profile.yml.new` rather than overwriting an edited one | No documented edit preservation; registration writes `CLAUDE.md`, `AGENTS.md`, per-platform rule files |
| C3 | **No engine** — it is an installer and a config reader | **Not separable.** `graphify install` is a subcommand of the engine; adopting the installer means adopting the engine |
| C4 | 6 targets: claude, cursor, windsurf, copilot, gemini, aider | **20+ targets.** The challenger's genuine advantage |
| C5 | `--bundle` emits one self-contained file with base64 templates | Not applicable — it is a package |

C1 and C3 are each independently disqualifying, and neither is close.

### The decision the bake-off's own premise missed

The bake-off was framed as two interchangeable skill installers. **They stopped being
interchangeable at item 69.** `sdlc-init.py` now owns `read_workspace()` and
`discover_components()` — the workspace config seam — and both `tools/graph/build.py` and
`tools/graph/workspace_report.py` import them. It is the reason there is exactly one
implementation of "which directories are components" rather than two that drift.

So swapping the installer would mean either keeping `sdlc-init.py` anyway (and saving nothing)
or porting the workspace layer into a third-party CLI (and inheriting its dependency chain on
the one path gated as dependency-free). The question "which installer wins" no longer has the
shape it had when it was asked.

### What is borrowed, and what is not

* **Borrowed: the breadth target.** 20+ platforms against 6 is the one place the challenger is
  plainly ahead, and adding a target here is a template plus a fan-out entry.
* **Not borrowed: any code.** Apache-2.0 would permit it; C1 and C3 forbid it.

### Re-open triggers

* A team standardises on a platform outside the incumbent's six **and** adding it to the
  fan-out proves harder than expected. (Adding one is currently a template plus a list entry,
  so the second half of this trigger is what matters.)
* The incumbent's fan-out grows a per-provider quirk that makes one-source-many-targets stop
  being a fair description — the point at which a purpose-built compiler earns its keep.
* Tier 0's zero-dependency promise is dropped, which would take a `policy.yml` change through
  G3 and retire C1 along with it.

---

## What both decisions have in common

Both alternatives are healthy, well-licensed, actively developed projects, and in both cases
the answer is "not here, not yet". That is worth stating plainly, because the failure mode a
register like this guards against is not adopting too little — it is **adopting on the
strength of an argument and discovering afterwards that nobody had a number**. The symbol
layer has a seam and no evidence; the installer has evidence and no gap. Neither shape
justifies a dependency at M0.
