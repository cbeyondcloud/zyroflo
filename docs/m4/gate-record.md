# M4 gate record

Append-only, per `docs/m0/judging-protocol.md` section 7.

M4's kill gate is a **conditions checklist**, not a bounded-judgment experiment
like M1's and M2's. The template's "primary metric vs threshold" line is
therefore replaced by one row per pre-registered condition; everything else the
template asks for — the protocol text quoted rather than paraphrased, the
evidence, the limitations, the decision and its off-ramp — is here.

---

## M4 kill gate — thin-slice pipeline + hard tests — 2026-08-25

**Result: 8 of 10 pre-registered conditions MET on evidence, 1 PARTIAL, 1 NOT
MET.** The gate is written *"ALL pass — else the pipeline does not go to
breadth"*, so on its own wording it **does not pass today**. What to do about
that — proceed, halt, or recalibrate with rationale as M1 did once — is the
owner's ruling; section 5 states exactly what each open condition needs, and
neither is a matter of writing more code.

### 1. The pre-registered text, quoted

This gate was written down **twice** in `SDLC-harness-reference.md`, in the M4
milestone entry (Part C) and again in the verification plan. The two lists are
not identical, so both are quoted and the gate is judged against their
**union** — a condition pre-registered anywhere is a condition.

> **M4 — Thin-slice pipeline + hard tests (~2 w)** […] **Acceptance/kill gate:
> one small real requirement E2E through G1→G4 on the thin slice (a service,
> its shared library, and its config component) with every diff human-approved;
> halt verified by attempting to skip a gate; tamper test (post-approval
> artifact edit → gate invalidated); CI ledger check blocks a tampered ledger
> from merging; security red-team (a)–(e) incl. prompt injection all
> blocked+logged; concurrency golden test (two runs + mid-run upstream merge +
> concurrent-verdict race) passes; P5 evidence carries measured changed-file
> coverage + baseline-relative (no-new-failures) results; median G3 batch
> review < 15 min; `gate_profile: solo` provably rejected on a multi-component
> workspace, and the resolved regime present in every ledger entry. ALL pass —
> else the pipeline does not go to breadth.**

> **6. M4:** drop a real small requirement at `SDLC_INTAKE_PATH` → `sdlc/run.py`
> on the thin slice → pipeline HALTS at G1/G2/G3/G4 until `approve.py` entries
> exist; tamper-test (post-approval artifact edit → gate invalidated); **CI
> ledger check blocks a hand-edited ledger from merging**; red-team (a)–(e)
> incl. prompt injection all blocked+logged; concurrency golden test (two runs
> + mid-run upstream merge + reject-beats-approve race) passes; golden
> cross-repo build test green+red paths; P5 evidence carries measured
> changed-file coverage + baseline-relative results; median G3 batch review
> < 15 min.

**Three transcription notes, so the judging is against what was written:**

* The verification statement adds **the golden cross-repo build test
  (green+red)**, which the Part C statement omits; the Part C statement adds
  **the `gate_profile: solo` condition**, which the verification statement
  omits. Both are judged. Union: ten conditions.
* Both statements say **red-team (a)–(e)**. Item 43 delivered **(a)–(f)** — (f)
  being the harness's own generated policy. A superset satisfies the condition;
  the extra letter is noted, not credited.
* The verification statement names an entry point, `sdlc/run.py`, that does not
  exist. Item 41 delivered the same mechanism as `tools/sdlc/slice.py`, and
  `SDLC_INTAKE_PATH` is implemented as written (item 31, `tools/sdlc/intake.py`).
  A renamed file is not a missing condition, but the record should not quietly
  substitute one name for another.

### 2. The conditions

| # | Pre-registered condition | Verdict | Where it is proven |
|---|---|---|---|
| C1 | One small **real** requirement E2E G1→G4 on the thin slice (service + shared library + config component), **every diff human-approved** | **PARTIAL** | `tools/sdlc/slice.py`, `test_slice_e2e.py` (22 tests), item 41 `afdea29`. The slice walks all three component classes and reaches G4; the requirement and the artifacts are **synthetic stubs** and the approvals are programmatic. See section 3. |
| C2 | Halt verified by **attempting to skip a gate** | **MET** | `test_every_hard_gate_refuses_before_it_is_taken`, `test_advancing_past_a_hard_gate_without_an_approval_is_refused`, `test_the_walk_stops_if_a_gate_turns_out_to_be_skippable`. Field run below: **4 gates refused before they were taken**. |
| C3 | Tamper test: post-approval artifact edit → **gate invalidated** | **MET** | `test_an_artifact_touched_after_approval_voids_the_gate` (item 32), `test_an_artifact_that_moves_after_its_yes_closes_the_gate_again` (item 41). Field run below: all four gates go void on one edited file. |
| C4 | CI ledger check **blocks a tampered ledger from merging** | **MET** | `tools/sdlc/validate.py --ledger` plus the emitted `.sdlc/ledger_check.py`; `test_ledger_gate.py` (51 tests), item 44 `703ec2e`. Field run below: exit 0 clean, exit 1 on a whole-file rewrite, exit 1 on an edited artifact. Limitation in section 4. |
| C5 | Red-team (a)–(e) incl. prompt injection, **all blocked + logged** | **MET** | `test_red_team.py` (55 tests), item 43 `31f9d56`; `tools/sdlc/guard.py` is the one door that applies, refuses **and records**, with `scrub()` so a blocked exfiltration does not publish what it blocked. (e)'s nuance in section 4. |
| C6 | Concurrency golden test: two runs + mid-run upstream merge + concurrent-verdict race | **MET** | `test_concurrency_golden.py` (20 tests), item 42 `aea5abb`. All three sub-parts are separately covered — two runs, a conflicting upstream merge returning work to G3, and reject-beats-approve in **both arrival orders** converging to the same state. |
| C7 | P5 evidence carries **measured changed-file coverage** + **baseline-relative** (no-new-failures) results | **MET** | `tools/sdlc/suite.py`, `test_suite.py` (84 tests), item 38 `6e24906`. Live `50-suite.json` in section 3 carries a per-component `coverage` block and per-step verdicts; an unmeasurable coverage says so rather than reading as zero. |
| C8 | **Median G3 batch review < 15 min** | **NOT MET** | One completed review session exists on the adopter side: 241 s (4.0 min), verdict `rework`, 9 findings. It is the M2 dogfood **pack-assisted review**, not a G3 batch review of agent-authored diffs. n=1, of the wrong kind. See section 5. |
| C9 | `gate_profile: solo` provably rejected on a multi-component workspace; **resolved regime present in every ledger entry** | **MET** | `test_gate_profiles.py` (55 tests), item 63 `bb0441b`. Solo above one component is structurally unselectable and caught at run time, not merely un-emitted. Field run below: 4 of 4 entries carry declared and resolved regime plus the hard-gate set. |
| C10 | Golden cross-repo build test, **green and red paths** | **MET** | `test_cross_repo_build.py` (16 tests) over a real graph build of the committed fixture, item 39 `3b5ce90`. Green path builds every dependent and owes nothing; red path stops the run and names the repository; seam cuts and two controls separate "resolves" from "is read". |

Every file above is in the suite that gates every push: **3233 tests, zero
skips, green at `3f206ef`** (CI run `32800989751`, verified from the run log
rather than from the plan text).

**The milestone-boundary full local run, which is due at M4's close and is
recorded here because this is where it belongs:** the whole suite on Windows,
`uv run pytest`, **3218 passed + 15 skipped, pytest exit 0, 65m35s**. Collected
total 3233 — the same tests CI runs; the 15 skips are the two documented
local-only absences (no Neo4j on this machine, POSIX permission bits) which CI
supplies, which is why CI reports 3233 *passed* at zero skips for the identical
tree. Per-item gating stays on CI (189s there against 65m here); the Windows run
is a milestone duty precisely because CI is Linux-only and a platform this
project ships on should not go a whole milestone unexercised.



### 3. Field runs — the CLIs, not the citations

Run today against a three-component workspace built from the committed fixture
(`orders-api`, `orders-shared`, `deploy-charts` — a service, its shared library,
its config component). Verbatim:

```
[C1/C2] slice.py exit 0
slice GREEN - run <id>, 4 gate(s) refused before they were taken
Artifacts are STUBS written by this walk, not agent output. A green slice proves
the machinery holds a run together and that its gates refuse; it proves nothing
about an agent.

[C9] 4 ledger entries; declared/resolved/hard per entry:
     G1: declared=team resolved=team hard=G1,G2,G3,G4,G5
     G2: declared=team resolved=team hard=G1,G2,G3,G4,G5
     G3: declared=team resolved=team hard=G1,G2,G3,G4,G5
     G4: declared=team resolved=team hard=G1,G2,G3,G4,G5
     entries missing gate_regime: 0

[C3] edited 00-intake.json after its approval
     gate_state(G1) -> False  G1 approved different bytes - 00-intake.json: changed since G1 approved it
     gate_state(G2) -> False  G2 approved different bytes - 00-intake.json: changed since G2 approved it
     gate_state(G3) -> False  G3 approved different bytes - 00-intake.json: changed since G3 approved it
     gate_state(G4) -> False  G4 approved different bytes - 00-intake.json: changed since G4 approved it
```

The disclaimer in the slice's own output is the tool refusing to overclaim, and
it is the reason C1 is PARTIAL rather than MET.

The merge question, asked three times on one repository whose base branch
already carries the ledger:

```
1. untouched                    exit 0   OK - the record holds.
2. ledger rewritten + re-hashed exit 1   approve.py --verify says INTACT, and the gate says:
                                         "entry 1 differs from the one committed at <base>
                                          - the ledger was rewritten, not appended to
                                          (a rewrite re-hashes cleanly; only the base can see it)"
3. approved artifact edited     exit 1   "70-triage.md: changed since G4 approved it"
                                         "artifact 70-triage.md: changed since it was recorded"
```

Case 2 is item 32's stated limit closed by item 44 — the chain cannot see a
wholesale rewrite, and the base ref can.

The P5 evidence document, live (`50-suite.json`, keys as saved):

```
top-level keys: at, coverage, cycles, dry_run, enforcement, notes, order, run,
                schema, seeds, steps, unscoped, verdict
coverage.<component>: {matched, of, percent, threshold, met, why}
steps[]:              {component, step, command, exit_code, seconds, verdict, source, note}
```

In this sample `percent` is `null` with *"this component's changed files are not
known, so there is nothing to measure coverage OF"* — the fail-loud path the
risk register demanded, where unmeasurable must never read as uncovered.

### 4. Limitations encountered beyond judging-protocol section 6

**The append-only check is only asked where the base branch already carries the
ledger.** Found today, setting up the C4 field run: the first attempt used a
base commit from *before* the run existed, and a whole-file rewrite came back
clean at exit 0 — correctly, because a ledger the base has never seen cannot
have been "appended to". `validate.py` already documents this
(`test_without_a_base_the_append_only_question_is_simply_not_asked`), but the
condition as written — *"CI ledger check blocks a tampered ledger from
merging"* — reads stronger than what is true on a **first push**. On a PR that
introduces its own ledger, a pre-push rewrite is invisible to the append-only
check; what still catches tampering there is the in-place-edit chain and the
artifact pin (case 3 above), both of which held. Recorded rather than fixed:
closing it means comparing against something outside the PR, which is a
different mechanism.

**Red-team (e) is fenced and logged, not refused.** The pre-registered wording
says "all blocked+logged". For prompt injection what is blocked is the
**attack** — injected text cannot forge a value in a stage prompt, and a
specification asking for a gate cannot open one — while the **document** is
admitted, fenced as data, and flagged. Refusing the document would make an
ordinary specification containing the word "approve" unprocessable. Judged MET
on the attack being blocked; the distinction is recorded because it is a real
one.

**C7's measured halves are exercised on fixtures and unit inputs, not on
agent-authored diffs.** The coverage ratio and the baseline-relative verdict are
each tested directly, and item 39's cross-repo test runs a real graph build over
a real fixture, but no P5 run has yet judged a change an agent wrote. This is
the same root as C1 and C8 and is counted once, in section 5, rather than three
times.

### 5. What the two open conditions need — neither is code

**C1 (real requirement, human-approved diffs) and C8 (median G3 < 15 min) share
one blocker: a live agent runner.**

There is no non-Claude agent CLI installed on the build machine and no LLM
credential present, which is the same blocker carried by item 33's codex E2E,
item 55 (goose), and item 29's live provider-swap instance since 2026-08-22.
Until an agent authors real diffs through the pipeline:

* C1 cannot have a *real* requirement or *human-approved agent diffs* — only
  the machine proven exhaustively on synthetic ones, which is what exists.
* C8 cannot have G3 batch reviews to take a median over. The one session on file
  is a dogfood review of a merge commit, recorded before the pipeline existed.

**The act that closes both:** name one runner command and put its key in
`<harness>/.env` (`RUNNER_CONTRACT.md` states what any runner must satisfy, and
`tools/sdlc/reference_runner.py` is that contract as a working program). Then
one small real requirement through the slice, with the reviews recorded through
`tools/review/hitl.py`, produces the evidence both conditions ask for. Three or
more G3 sessions make a median worth reading; one does not.

### 6. Decision

**Recorded, not decided.** The gate's own rule is ALL-pass, and two conditions
are open, so on the pre-registered wording the pipeline does not go to breadth
today. The owner's options, stated so the choice is explicit:

1. **Hold M4 open** until a runner is named, run the real slice, and re-rule.
   Nothing in M5 is blocked by code — only by this ruling.
2. **Recalibrate with rationale**, as M1 did once and spent: rule that the
   machine-level conditions are met and that C1 and C8 are re-scoped to M5's
   first real run, with the direction of bias stated. M1's precedent requires
   the ruling to be written here, before the next milestone opens.
3. **Halt** per the off-ramp — *"else the pipeline does not go to breadth"* —
   and re-scope.

The operator's recommendation is **(1) or (2), and against starting M5 line
items before whichever is chosen is written into this file.** Every prior
milestone in this project was funded by its gate record; M5 funded by an unruled
gate would be the first exception, and the discipline is the product.

**Decision taken:** _(unfilled — the owner's ruling)_

---
