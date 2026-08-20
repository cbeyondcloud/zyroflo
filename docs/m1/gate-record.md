# M1 gate records

Append-only, per `docs/m0/judging-protocol.md` section 7.

## M1 gate — impact vs grep (blind A/B) — 2026-08-19

**Protocol version:** 2026-08-05 (amended 2026-08-06). No further amendment: no
threshold moved. Two operational readings the protocol left open are recorded here,
neither of which loosens anything:

1. *"any three components with `role: authoritative`"* — taken in the workspace's own
   deterministic component order (discovery sorts by name), one commit per component; a
   component yielding no qualifying merge in the window is recorded and passed over.
2. *"each changed symbol / property key / topic name"* — operationalized mechanically:
   identifiers (length >= 4) from the seed file's own hunks, frequency-ordered, capped
   at 40, written into the run record per commit.

**Commits judged:** C1 / C2 / C3, one per component (component-1/2/3). SHAs, subjects,
component names and file paths are the adopter's data (Rev 12) and live in the
adopter-side records (`selection.json` and per-case records produced by
`tools/impact_ab.py`), not in this repository. Selection was mechanical: walking back
newest-first over first-parent merge history (window 200 merges/component), **29
rejections recorded with the rule each failed**; one component yielded no qualifying
merge and is on the record. Criterion (4) is met by construction — for each commit the
graph was rebuilt with the judged component extracted at the commit's **first parent**
(`git archive` into scratch; the other components enter as links; nothing is written
into the adopter's tree), so the tool was never shown the answer.

Seed classes, for the mechanism's sake (generic descriptors, not names): C1 a
TypeScript API-client file (TRUTH 14 files), C2 a Dockerfile (TRUTH 96), C3 a Spring
application class (TRUTH 55).

**Prediction on file:** yes — written after selection and before any condition ran,
quoted here in full from the pre-run record: *"B beats A on 1 of 3, confidence
low-medium, expected gate outcome FAIL. Surprises: B sweeping 3/3, or B losing all
three by more than 0.20."* The result (2 of 3, PASS) **contradicts the prediction**,
which per section 5 makes it stronger evidence than a confirmed prior would have been.

**Raw results (recall@20 primary; secondaries reported, not gating):**

| commit | recall@20 A (grep) | recall@20 B (impact) | first true positive A / B | precision@10 A / B |
|---|---|---|---|---|
| C1 | 0.0 | 0.0 | 21 / 2113 | 0.0 / 0.0 |
| C2 | 0.0 | **0.0938** | 1466 / **1** | 0.0 / 0.2 |
| C3 | 0.0 | **0.0727** | 273 / **1** | 0.0 / 0.3 |

Wall-clock, recorded: grep 58.3s / 22.6s / 20.8s (box: 15 min — never approached);
impact's graph rebuild at parent 253s / 226s / 220s per commit, ranking itself <1s.
Blinding: outputs reduced to bare ranked path lists, coin-flip X/Y per commit
(flips 0/1/0), scores computed on the anonymous lists before the mapping was revealed.

**Primary metric vs threshold:** B strictly beats A on 2 of 3; worst loss 0.0
(<= 0.20). Ties count as not beating (C1 was a tie at zero and counts for nothing).

**Verdict: PASS.**

**False findings:** n/a for this gate — a ranked-list metric; the M2 gate tallies
those.

**Limitations encountered beyond section 6, stated so the pass cannot overclaim:**

1. **The baseline drowned in estate-wide common tokens.** Hit-count ranking over two
   dozen repositories floods the top-20 with high-frequency files; A's first true
   positive sat at rank 21 / 1466 / 273. The baseline is exactly what the protocol
   pre-registered (section 3.2), and its term lists are on the record — but a
   repo-scoped grep would be stronger, and the pass should be read against the
   baseline as registered, not against the best grep imaginable.
2. **B wins by being nonzero, not by being good.** Absolute recall@20 of 0.07–0.09
   against wide merges names the M2 work (hunk-level seeds, damped traversal tuning,
   semantic layer) rather than validating current reach.
3. **C1 was nearly a loss.** A's first true positive at rank 21 — one slot outside the
   scoring window — against B's at 2113. On seeds whose blast radius lives in sibling
   files of the same package, token overlap beats config-seam reach today.
4. The winning mechanism on C2/C3 is **module-scoped provenance concentration** (the
   seed's own module's graph-cited files rank first), not deep cross-repo traversal.
   That is the graph working as designed at one hop, and it is honest to name it.
5. The per-case seed *mode* (provenance / directory / component fallback) was not
   stamped into the case record in v1 — derivable, but it should be recorded.

**Decision taken: proceed.** The impact-beats-grep condition of the M1 kill gate is
met; the M1 gate as a whole still awaits the validation-target condition (measured and
missed by item 11's dashboard — the misses name extraction work).

## M1 gate — validation targets: the one recalibration, spent — 2026-08-20

**The provision, quoted from the plan:** *"Coverage targets (recalibrate once with
rationale): ≥85% host keys resolved · ≥60% topics with producer+consumer · ≥50%
endpoints spec-matched · ≥80% of write-mapping targets owned by a migration in the same
variant group · ≥90% UI URLs resolved · orphans <5% · zero silent drops."* One
recalibration is allowed, with rationale. This record spends it.

**History.** The dashboard's first workspace verdict (item 11) was MISSED: hosts 30.8%,
topics 4.1%, spec 18.6% — and the recalibration was deliberately NOT spent then,
because the misses named extraction work. Three items did that work, each moving one
target by a measured, declared-link mechanism: item 91 (config-class binding layer +
producer call-site resolution) took topics 4.1% → 35.0%; item 92 (host pairing through
the binding layer + call-site concatenation) took hosts 30.8% → 67.8%; item 93
(spec-align link pass) took spec 18.6% → 31.8%. All three still miss the original
numbers.

**Rationale — the ceilings are measured, not asserted.** Each residue was decomposed
before this ruling, and each names something other than missed extraction:

1. **Topics (35.0% vs 60%).** All 320 topics carry at least one declared side: 240 have
   a producer, 192 a consumer, 112 both. The original 60% target is *exactly* the
   perfect case in which every consumed topic also gets its producer found — a target
   equal to the best case is an aspiration, not a floor. Both sides' residue is call
   sites passing locals and parameters (~580 producer sites measured at item 91's
   close-out; 177 recorded unresolved rows), resolvable only by dataflow inference,
   which the extraction doctrine excludes as guessing.
2. **Hosts (67.8% vs 85%).** The residue is exactly the 696 recorded `unpaired-host`
   rows: 443 syntactic addresses whose partner component does not exist in the
   workspace (external services, infrastructure endpoints) plus 253 bare-word values
   that name nothing resolvable. Zero of the 696 are reachable from declared facts.
3. **Spec (31.8% vs 50%).** The spec side is exhausted: every spec-declared path now
   either matches an exposed endpoint directly, aligns uniquely through item 93's
   one-segment rule (213 endpoints carry alignment edges; 176 newly matched, 37 already
   matched directly through a second spec), stands recorded as one of 20
   `ambiguous-spec-alignment` rows, or names an endpoint the workspace does not expose
   (471 paths). The unmatched 903 endpoints belong to services with **no spec
   written** — absent facts, not missed extraction. Curating all 20 ambiguous rows
   would add at most ~1.5 points.

**The recalibrated targets** are regression floors set just below the measured
declared-fact yield, with small headroom for estate drift — not grades, and not new
aspirations:

```toml
host_keys_resolved = 60.0
topics_with_producer_and_consumer = 30.0
endpoints_spec_matched = 28.0
```

The other four targets are unchanged (write-mappings 80 · UI 90 · orphans <5 · zero
silent drops). Nothing in the product changed: `DEFAULT_TARGETS` in
`tools/graph/validate.py` still ships the original aspirational numbers for any new
workspace; the recalibration reaches the tool only through `--targets`, exactly as
item 11 built it.

**The ruling run (2026-08-20, workspace graph after item 93):**

```
PASS host_keys_resolved: 67.8% (1464/2160, target 60.0%)
PASS topics_with_producer_and_consumer: 35.0% (112/320, target 30.0%)
PASS endpoints_spec_matched: 31.8% (422/1325, target 28.0%)
N/A  write_targets_migration_owned: N/A (0/0, target 80.0%)
PASS ui_urls_resolved: 100.0% (5540/5540, target 90.0%)
PASS orphans: 1.8% (158/8831, target 5.0%)
zero_silent_drops: PASS
verdict: met (exit 0)
```

**Verdict: MET.** With this, all four M1 kill-gate conditions are met: validation
targets (this record) · impact beats grep (record above) · second-stack fixture with
zero core edits (item 19) · zero-dependency parity (item 62, re-asserted on every CI
run).

**What this pass does not claim:**

1. The floors grade the extractor set's declared-fact reach on one real workspace, not
   that workspace's integration health — 35% of topics having both sides visible is a
   true and low number, and raising *reach* (dataflow, semantic layer) is M2 work, to
   be judged by M2's own gate.
2. The recalibration is now **spent**. A future run under these floors that misses is a
   regression to be fixed, not a number to be renegotiated.
3. The write-mappings target was neither met nor recalibrated — no component of this
   workspace produces that fact class, so it is N/A by the dashboard's own rule and
   stays at 80% for workspaces that do.

**Decision taken: proceed.** The M1 kill gate is passed; item 78 (generated code
declared — an M1 line item, not a gate condition) remains before M1 closes.
