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
