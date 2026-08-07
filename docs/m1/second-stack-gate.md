# Item 19 — the second-stack gate record

**Date:** 2026-08-07 · **Fixture:** `tools/graph/tests/fixtures/pantry` (Python / ASGI /
compose) · **Baseline:** `e145be4`, the commit that added the answer key.

## The claim under test, and why only a second fixture can test it

Rev 7 recorded the limitation the first fixture can never escape: **one committed fixture can
always be accommodated by the code that reads it.** The `orderflow` fixture and its extractors
were written by the same author, so a green golden chain there proves the extractors parse
their author's idea of the input — item 4's mutation test converts that into a falsifiable
claim about *reading the fixture*, but it says nothing about whether the extraction SPI was
shaped around one technology.

So the gate here is **not** "the chain is green". It is:

> the chain is green **AND** `tools/graph/core/` is byte-identical.

A green chain bought with a core edit is a failure wearing a pass's clothes: it means the SPI
boundary was drawn around the first stack, and every later adopter pays for it.

## Result

**Both halves hold.** Five hops across three components resolve against the hand-written
answer key, by line; `git diff e145be4 -- tools/graph/core/` is empty; and
`test_the_core_was_not_touched` asserts that continuously rather than leaving it as a claim in
this document.

The chain deliberately shares no resolution mechanism with the first fixture:

| Hop | Edge | Mechanism, and why it is new |
|---|---|---|
| 1 | `DG_CALLS` | a path literal paired with an address read from the environment, whose **value lives in the deployment layer** — the component's own files never contain it |
| 2 | `DG_EXPOSES` | a router **prefix** held in a module variable, concatenated with a **decorator** argument |
| 3 | `DG_PRODUCES` | a client call → module constant → env read → compose block → `${VAR:-default}`, a grammar the first fixture does not use |
| 4 | `DG_CONSUMES` | a **different key name** whose value comes from an **inline default in code**, not from any config file |
| 5 | `DG_OWNS_TABLE` | migration ownership through the ordinary numbered layout |

Hops 3 and 4 meet at the topic's **value and nowhere else** — different key, file, component
and resolution layer. That is the property that makes the pairing rule falsifiable rather than
decorative.

## What it cost, by layer

| Layer | Change |
|---|---|
| `core/` | **nothing** |
| `extractors/` (stack) | new `http_calls`, `kafka_python`; changed `compose`, `env_keys`, `http_routes`; `flyway` renamed to `sqlmigrations` |
| `profiles/` | **none — the workspace declares `profile = ""`**, and that is a claim: every format here is one a generic tool can read, so a second fixture that quietly needed a profile would prove the opposite of what this item exists to prove |

## Findings

Four, each recorded where it was found and each with a regression test verified to fail
against the pre-fix source.

**1. `props.find_line` is a substring search and is wrong for YAML.** A service `db` declared
on line 22 was cited at line 11, because `postgresql://db:5432/pantry` appears in a value
there. Fixed in the stack layer with a key-aware finder. **`extractors/helm` carries the same
latent defect** — recorded as item 81 rather than fixed here, because a defect fixed outside
the item that found it arrives without a regression test.

**2. The migration extractor was misnamed, and the name was making itself true.** Named for
one runner, its globs had grown to match that runner's file conventions alone, so a workspace
using the ordinary numbered layout got zero edges and no explanation. Renamed to
`sqlmigrations`; nothing in it was ever specific to a tool — it reads `CREATE TABLE`, which is
SQL.

**3. Configuration layer precedence was wrong, and produced a node named nothing.** The shared
resolver checked the inline-default layer before the deployment layer, on the reading that the
two are disjoint: a key read *with* a default never needs the deployment, a key read *without*
one has no default to offer. **True per read site, false per key** — relaxed binding collapses
`RESTOCK_QUEUE`, `restock.queue` and `restock-queue` into one key, so a default written beside
one spelling answered for all three. In this fixture that default is the empty string, and the
topic an entire component publishes to resolved to `""`. The chain looked healthy at every
step. Order now matches what a running process does: the deployment value wins.

**4. Canonical resolution happens in the link layer; resolved *values* are consumed in the
extract layer.** This is the deepest of the four. `mark-canonical` correctly resolved the
contested service **node** — winner canonical at 1.0, loser damped to 0.5 and not dropped —
while the resolved-value view was still being merged under a deterministic *last-wins* rule.
`legacy/docker-compose.yml` sorts after `docker-compose.yml`, so a file whose own header says
it is dead supplied the value, and `pantry-api` was recorded as publishing to
`restock-events-v1` — one version number off, entirely plausible, invisible to anyone not
checking it against the workspace's own `[[canonical]]` block.

Last-wins is correct for **layering** and catastrophic for a **rival claim**, and the two are
the same shape on disk. Only the workspace can tell them apart, because neither file contains
the intent.

> **Why this needed no core change, which is itself the evidence.** `ExtractContext.config`
> already carried the workspace's `[[canonical]]` blocks, and the pipeline already populated
> it — the seam existed before anything used it. Had it not, the honest outcome would have
> been a core edit and a **recorded failure of this gate**, not a workaround. Stating that in
> advance is what makes the pass meaningful.
>
> **Suspected, not verified:** `extractors/helm` merges chart values the same way and is
> likely to have the same defect. Not investigated here, for the reason finding 1 gives.

## What this does NOT prove

**Recall.** The fixture's author chose what reality looks like in it. Item 76 — a build
against a real workspace, output to `.tmp/`, with its own dated record — is the separate and
equally required other half, and it is **still open**. Saying either substitutes for the other
is the failure this arrangement exists to prevent.

It also does not prove the extractors are *complete* for these technologies. Several
limitations are deliberate and pinned by tests so their absence is a decision rather than an
oversight: `include_router`'s second prefix layer, hardcoded hosts, clients bound outside
module scope, and paths assembled inside function bodies. Each needs a real workspace to
design against, which is item 9.
