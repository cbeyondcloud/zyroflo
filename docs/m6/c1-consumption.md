# M6 · C1 — the OKF bundle consumed by a non-Claude agent

*The demonstration record. The bar was pre-registered in code before any call
fired, and verdicts here are whatever the mechanical check said — a miss is
recorded, not massaged.*

## The condition, verbatim

> OKF bundle consumed by a non-Claude agent

From the pre-registered M6 kill gate (reference, Part C and the verification
plan). It names no tool — only C2 names one (goose) — so the agent here is a
raw OpenAI model reached through `tools/sdlc/http_runner.py` (item 104): an
endpoint, nothing installed, the key in `.env` and never in argv.

## The demonstration shape

The pantry fixture graph is built and exported (`export_okf.py`, item 54's
exporter half): **26 node pages, 12,960 bytes ≈ 3,341 prompt tokens**. The
model is handed the pages verbatim plus one question — *which nodes does
changing `svc:pantry-api@pantry-api` touch directly, one hop, either
direction?* — and its answer is verified against the graph's own edge rows,
the same expected set `test_a_blast_radius_from_markdown_matches_the_graphs_own_edges`
holds the mechanical consumer to.

**The pre-registered bar** (in the driver, written and shown before the call):
the answer must (a) name ONLY node ids that exist in the bundle, and (b)
include EVERY 1-hop neighbour of the seed. Expected set: 4 nodes — two
endpoints, one topic, one test.

## Attempt 1 — 2026-08-30, gpt-4o-mini, one call

| Measure | Value |
|---|---|
| Runner exit | 0 (the endpoint answered) |
| Ids named | 3 — **all real, zero invented** |
| Expected hit | **0 of 4** |
| Citation-bearing lines | 3 of 3 |
| **Verdict (pre-registered bar)** | **NOT MET** |

**What actually happened is the interesting part.** The model did not
hallucinate — every id it named exists in the bundle, with real citations and
real edge types. It answered the wrong question: asked about
`svc:pantry-api@pantry-api`, it answered about **`svc:pantry-api@deploy-compose`**
— the *second claimant of the same service name*, which the pantry fixture
carries deliberately, because two spellings of one service silently becoming
two nodes is the expensive failure class the harness's identity layer
(`core/lib/ids.py`, item 10's canonical curation) exists to prevent.

**So the first live consumption reproduced, in an agent, the exact defect
class the graph machinery was built to guard against in extractors.** A
mechanical consumer never makes this mistake (`reference_consumer.py` matches
ids exactly and passes this same check); a language model reading the same
pages matched on the human-salient half of the id and dropped the
disambiguating suffix. That is evidence about agents consuming graphs — the
thing M6 exists to learn — and it argues *for* the format's explicit
frontmatter ids rather than against them: the information needed to avoid the
trap was on every page.

**The bar is not re-ruled after the fact.** Attempt 1 stands NOT MET.

### An accidental replication — same day, same prompt, operator error

The operator (Claude) chained the second call behind a prompt-amendment patch
without checking the patch had applied; the patch failed and the call fired
anyway, so a second UNAMENDED call ran. Recorded as what it is, because it
carries information: **the model made the same mistake again** — 4 real ids, 0
of 4 expected, 0 invented, every line citing `source_file:line`, provenance and
extractor from the pages — again centred on `svc:pantry-api@deploy-compose`,
and this time listing the seed itself among the nodes it "touches".
**The conflation is reproducible, not a sampling fluke**, which strengthens
attempt 1's finding. Verdict under the bar: NOT MET, unchanged.

## The amended attempt (third call) — amendment stated before it ran

*Recorded 2026-08-30, before the second call, per the M2 discipline: an
amendment between attempts states its own bias direction.* The question gains
one neutral sentence — **"Match the seed id EXACTLY, character for character,
including everything after `@`"** — and nothing else changes: same model, same
pages, same bar. **Bias direction: pro-pass.** The amendment does not name the
trap or any node; it restates an instruction the output half of the prompt
already carried. If the model still misses, that stands too.

### Result — 2026-08-30, gpt-4o-mini

| Measure | Value |
|---|---|
| Runner exit | 0 |
| Ids named | 2 |
| Expected hit | **0 of 4** |
| Invented | 1 — a page FILENAME (`svc:pantry-api-deploy-compose.…md`) mistaken for an id |
| **Verdict (same bar)** | **NOT MET** |

**The amendment worked and exposed a second failure mode.** The model found
the right page this time — its remaining lines cite `src/pantry_api/routes.py:13`
and `:18`, which are exactly the seed page's own two `DG_EXPOSES` entries —
and then named the wrong END of each edge: the seed itself in the answer
column, where the question asks for the neighbour. The seed's page lists all
four expected nodes plainly, outgoing and incoming, each with its citation.

## The gpt-4o-mini ruling

**Three calls, three NOT MET, two distinct failure modes — identity
conflation (twice, reproducible) and edge-end inversion (once) — and neither
is the format's.** The information needed to answer was on the seed's page in
every attempt, unambiguously. C1 asks for *a* non-Claude agent; a stronger
model is a different agent against the same pre-registered bar.

## The gpt-4o attempt (fourth call) — 2026-08-30, SAME bar

| Measure | Value |
|---|---|
| Runner exit | 0 |
| Ids named | 4 |
| Expected hit | **4 of 4** |
| Invented | 0 |
| Beyond one hop | 0 |
| **Verdict (the unchanged pre-registered bar)** | **CONSUMED** |

The answer, verbatim:

```
ep:/pantry/v1/items | DG_EXPOSES | src/pantry_api/routes.py:13
ep:/pantry/v1/items/{} | DG_EXPOSES | src/pantry_api/routes.py:18
test:pantry-api/tests/test_routes.py | DG_TESTS | tests/test_routes.py
topic:restock-events-v2 | DG_PRODUCES | src/pantry_api/publisher.py:18
```

All four neighbours — both endpoints, the incoming test, the produced topic —
each with its edge type and the citation copied from the page **verbatim,
including the test's file-only citation exactly as the page gives it**. Every
row matches the graph's own edge rows.

## Ruling

**C1 is MET.** The OKF bundle was consumed by a non-Claude agent — gpt-4o over
a bare OpenAI-compatible endpoint through `http_runner.py`, nothing installed —
under a bar pre-registered before any call fired and never moved: name only
real ids, name every 1-hop neighbour. **The capability-floor finding stands
beside it**: gpt-4o-mini failed the same task three times in two distinct
ways, so a consumer of this bundle below some capability line needs either a
stronger model or a tool loop — which is exactly what C2's goose E2E
demonstrates from the other side.

## What C1's closure claims, and what it does not

**Claimed:** the OKF bundle is consumable by a non-Claude agent with
no harness, no Neo4j, no MCP and nothing installed — a raw model over an
OpenAI-compatible endpoint read the pages and produced a verifiable,
citation-carrying blast radius that matches the graph.

**Not claimed:** that an agent *workflow* ran (the endpoint runner is
`chat`-capability: it writes, it cannot act — C2's goose E2E remains the
workflow evidence); that the pass generalises below gpt-4o's capability line
(the three mini failures say it does not); or that the identity-conflation
finding is fixed anywhere — it is a property of language-model reading,
recorded here as what a consumer of this format must handle, which the
format's exact frontmatter ids already make possible.

**Cost, measured:** four calls, ≈ 15,000 tokens, ≈ $0.02 total — dominated by
the one gpt-4o call.

**Reproduction:** build + export the pantry fixture, then hand the pages and
the question to any OpenAI-compatible endpoint through
`tools/sdlc/http_runner.py --model <name> "<prompt>"` with the key in `.env`.
The driver's verification is mechanical: expected = both directions of the
graph's edge rows at the seed; the answer's ids are matched exactly.
