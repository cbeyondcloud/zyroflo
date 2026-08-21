# M2 gate records

Append-only, per `docs/m0/judging-protocol.md` section 7.

## M2 gate — dogfood review of a real merge — 2026-08-21

**Protocol version:** 2026-08-05, amended 2026-08-06 and **2026-08-21**. The
2026-08-21 amendment (recorded in the protocol's §7 with its direction of bias
stated) added documentation-only changes to criterion 2's exclusions after the
walk's first qualifying pick was a merge adding 31 README files across 23
modules — a vacuous trial either way. The amendment was committed before any
condition ran and before the next candidate was known; git order proves the
sequence. No threshold moved.

**Commit judged:** one merge commit of the authoritative component with the
highest module count (145 modules; census on the record). SHA, subject,
component name and file paths are the adopter's data (Rev 12) and live in the
adopter-side records (`.sdlc/m2-dogfood/` in the workspace root: selection.json
with 12 rejections logged, prediction.md, diff-only.md, pack-assisted.md,
classification.md, plus the prepass and pack outputs). Selection was
mechanical: newest-first first-parent walk, window 200; the operational reading
that section 2's four criteria apply without M1's extra seed-exists filter
(a §3.1 ground-truth need, not a §2 criterion) is recorded here. Criterion 4
by construction, as in M1: the graph was rebuilt with the judged component
extracted at the merge's first parent (283.6s); sibling components enter the
scratch workspace as links at their current state.

**The change, generically:** a "build fix" merge adding forced
transitive-dependency version pins to three build manifests of a frontend
monorepo — content-wise a dependency-pin change that the subject-based
criterion-2 proxy admitted ("fix" matches no exclusion word). Recorded as a
limitation; the review found real substance regardless.

**Prediction on file:** yes — written after selection, before any condition
ran, quoted in full from the pre-run record: *"H2 — I expect 1-2 non-obvious
findings (threshold is >= 2: borderline, leaning FAIL). Confidence:
low-medium. Surprises: >= 4 non-obvious findings, or 0 non-obvious findings
WITH an empty pack."* The result (3, PASS) **exceeds the prediction** — a
contradicted lean-FAIL prior, which per §5 is stronger evidence than a
confirmed one, though the miss is one finding, not a sweep.

**Procedure and raw results (order per §4.1, stamps on the adopter-side
record):**

| Phase | Box | Actual | Findings |
|---|---|---|---|
| Diff-only review (no pack, no graph; ordinary repo browsing allowed — recorded reading) | 20 min | 3.2 min | 6 (build-process and pin-hygiene defects, incl. the build script deleting its own lockfile) |
| Pre-pass (machine) | — | ~1s | 0 (both gates fired; a manifest diff carries no secrets or debug statements) |
| Pack-assisted review (SOP, CLI tier) | 20 min | 4.0 min session | 3 graph findings + the 6 carried |

**Primary metric vs threshold: 3 non-obvious findings vs ≥ 2 → PASS.** All
three per the four criteria of §4.2 (full grid in the adopter-side
classification.md): (1) the same published-package registry name is declared
by five components — parallel forks — and the pin fix reached one of them;
a sibling fork verified to carry NO pins at all; (2) where the fix did
propagate (two newer forks), it mutated — one pin dropped, variant key forms
added — three versions of "the same" fix now live in the estate; (3) one
registry identity carries three live package versions across the forks with
no single publishing owner. Criteria 1–3 checked mechanically or by opening
the cited files; criterion 4's reasoning is logged per finding, with the
third finding's actionability flagged as the softest call — the gate clears
on the first two alone.

**The winning mechanism, named honestly** (as the M1 record named module-scoped
provenance concentration): the value came from the npm layer's
registry-global name identity — the graph merging same-named package
declarations across components into one node, so the changed manifest's
package node exposed its parallel declarers in other repositories. One query
class, not the pack page's top rows: the page's dominant content was the
whole-service contract fan, because two of the three changed manifests seeded
in whole-file fallback (no extracted fact cites a `resolutions` block), and
the Seeds block said so — the stated-limits doctrine is what kept the fan
honestly discountable. Manifest-pin lines as first-class facts are named
future extraction work, not claimed capability.

**False findings: 0 cited.** One phantom coupling was identified during the
review and excluded before citing: an empty-id topic node (a producer whose
topic property resolves to an empty value) merges every empty-resolved
producer across components into one node, manufacturing a cross-component
coupling that does not exist. Recorded as a tool defect to fix (empty-id
entity nodes should be refused or quarantined by the extractor), and the
underlying empty-valued property is itself a real estate config smell now on
the adopter-side record.

**Limitations encountered beyond §6:**

1. **The selection amendment is pro-tool in direction** (it removed a commit
   the tool would have scored zero on). Bounded as stated in the protocol §7
   entry: committed before any condition ran, next candidate unknown, the
   excluded commit identified.
2. **Sibling components enter at current state**, so the three graph findings
   cite sibling files as they are today, not as of the judged merge's date.
   They are true and actionable today; as evidence the pack "would have
   helped on the day", they are weaker, and the M1 construction (only the
   judged component is rewound) is the reason.
3. **The diff-only phase's end stamp was first written as an estimate**
   (~12.7 min) instead of a measurement; corrected same day to the file's
   mtime (3.2 min) with the correction preserved in the file. The findings
   were untouched. Estimated timestamps are now a named operator error.
4. One declarer count in the pack-assisted record was transcribed wrong
   (7 → 8) and corrected on the record after the verification recount.
5. **The MCP tier was not exercised** — the review ran the SOP's CLI tier
   (headless runner, no MCP client). The tier doctrine exists for exactly
   this; the MCP surface has its own tests and estate demo (item 23).
6. The M1 A/B's full records, which the M1 gate record says live adopter-side,
   were found in the session scratchpad and copied to `.sdlc/m1-ab/` in the
   workspace root this run. The M1 numbers are unchanged; the storage claim
   is now true.

**Instrumentation:** this run produced the first real `reviews.jsonl` rows
(item 27): one completed review — verdict rework, 4.0 min, 9 findings,
acceptance by source {agent 6, graph 3}. The diff-only baseline was
deliberately not instrumented (a protocol artifact, not an impact-review;
its duration would bias the median M4 reads) — its timing lives in the
record's stamps.

**Verdict: PASS.**

**Decision taken: proceed.** The M2 kill gate is met; the M3 GO/NO-GO
(tracker item 30) — whether this evidence funds the pipeline build — is the
project owner's decision, made on this record.
