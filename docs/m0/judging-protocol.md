# Dogfood judging protocol (M0, tracker item 7)

**Pre-registered:** 2026-08-05 · **Governs:** the M1 and M2 kill gates · **Status:** locked

> **This document is written before any evidence exists, and that is the entire point.**
> A judging protocol authored after seeing results is not a protocol — it is a rationalization
> with a rubric attached. Once this file is committed, the thresholds below may only be
> changed by a dated amendment that states what moved and why, *and* re-runs any gate already
> judged under the old numbers.

---

## 0. The problem this protocol has to solve honestly

The author of the harness is also its only judge. Classic blinding — a judge who does not
know which condition produced which output — is **not fully achievable here**, and pretending
otherwise would make this document worse than useless.

So the protocol does not rely on the judge being blind. It relies on **moving as much of the
decision as possible out of judgment entirely**:

| Defense | What it does |
|---|---|
| **Mechanical ground truth** | The answer key is derived from what the merged fix actually touched — computed by script, never by opinion |
| **Mechanical primary metric** | The M1 gate is decided by recall@k against that key. No human scores it |
| **Operational definition of "non-obvious"** | The M2 gate's key term is defined so it can be *checked*, not felt (§4.2) |
| **Pre-committed inputs** | Commit SHAs are chosen and written down **before** any tool is run (§2) |
| **Artifact blinding** | Outputs are stripped to a common format so the two conditions are not visually distinguishable (§3.3) |
| **Recorded prediction** | The judge writes their expected outcome before running. A surprise is informative; a confirmed prior is weak evidence |

Where judgment remains, it is bounded and logged. Residual bias is a **stated limitation of
the evidence**, not a defect to be papered over — see §6.

---

## 1. Hypotheses (stated so they can fail)

**H1 (M1 — impact beats grep).** Given a change, the graph-derived context pack ranks the
files that the real fix actually touched higher than a competent grep-based baseline does.

**H2 (M2 — dogfood finds the non-obvious).** Reviewing a real merge commit *with* the pack
surfaces defects or risks that reading the diff alone does not, where "does not" is
established by actually reviewing the diff alone first.

**Null result is a real outcome.** If H1 fails, the M1 kill gate halts the impact-engine line.
If H2 fails, M2 is a NO-GO on the SDLC machine and the project re-scopes to read-only graph
tooling + OKF export — which the plan already names as a shippable product. Neither is a
disaster; both are the spike working as designed.

---

## 2. Commit selection — fixed before anything runs

Cherry-picking the evidence is the easiest way to fake this gate, so selection is mechanical
and recorded first.

**Rule.** From the repos named in the milestone (M1: any three of the 26; M2: SVC-A or
Platform-Core), take the **most recent merge commits** satisfying all of:

1. touches **≥ 2 distinct services or modules** (a single-module change has no blast radius to
   predict, so it tests nothing);
2. is **not** a dependency bump, version bump, formatting-only, or generated-file change;
3. has **≥ 3 non-test files** changed;
4. is old enough that the graph snapshot's parent commit predates it (so the tool is not
   being shown the answer).

Walk backwards from `HEAD` and take the **first** commits that qualify. Not the most
interesting ones. Not the ones with the best story.

**Record before running anything:**

```
Selected commits (SHA, repo, date, files changed, one-line subject):
  1. ____________________
  2. ____________________
  3. ____________________
Selection date: __________       Graph snapshot commit: __________
Rejected while walking back (SHA + which rule excluded it):
  ____________________
```

Logging rejections matters as much as logging selections — it is what makes "we walked back
and took the first three" checkable rather than merely claimed.

---

## 3. M1 — impact vs grep (mechanical)

### 3.1 Ground truth

For each selected commit `C`:

- `TOUCHED` = the set of non-test files `C` changed.
- `SEED` = the single file in `TOUCHED` that a developer would plausibly have started from
  (the primary source change; chosen by the rule "first non-config, non-test file in the
  commit's diff order" and **written down before running**).
- **`TRUTH` = `TOUCHED` − `SEED`.** This is the answer key: what else really had to change.

`TRUTH` is computed by script from the commit. It is never adjusted after seeing a result.
If `TRUTH` is empty under the rule above, the commit fails criterion (1) and is replaced.

### 3.2 The two conditions

**Condition A — baseline (grep + diff).** The honest thing a competent developer does today,
not a strawman:

```
git diff --name-only <C>^ <C>        # what changed
rg -n "<each changed symbol / property key / topic name>" <estate root>
```

Ranked by hit count per file, descending. Time-boxed (§3.4). The baseline gets the **same**
estate access and the **same** vendored ripgrep the harness uses.

**Condition B — context pack.**

```
python tools/graph/impact.py --diff-repo <repo> --ref <C> --pack
```

Ranked by the pack's own blast-radius score.

Both conditions are seeded from `SEED` only. Neither sees `TRUTH`.

### 3.3 Blinding what can be blinded

Both outputs are reduced to **a bare ranked list of repo-relative file paths**, one per line,
written to `A.txt` / `B.txt` with the condition labels replaced by a coin-flip-assigned
`X`/`Y`. Scoring is then run on `X.txt` and `Y.txt` by script. The mapping is revealed only
after the score is computed and written down.

This does not blind the judge to the *project*. It does mean the primary metric cannot be
nudged by presentation, prose quality, or the pack's nicer formatting — which is the specific
bias most likely to matter here.

### 3.4 Time box

**Condition A: 15 minutes per commit**, wall-clock, timer started at the first command.
Condition B is whatever `impact.py` takes, recorded.

15 minutes is the defensible number because it is roughly what a developer will actually
spend before acting. Giving the baseline unlimited time would test a different question
("can grep eventually find it?" — yes, usually) than the one that matters ("does the tool help
in the time a human really has?"). Giving it less would rig the comparison.

If Condition A finishes early, record the actual time; do not spend the remainder.

### 3.5 Metrics and threshold — fixed now

Primary: **recall@20** = `|top-20 ∩ TRUTH| / |TRUTH|`.
Secondary (reported, not gating): precision@10, rank of first true positive, wall-clock.

> **PASS ⇔ Condition B beats Condition A on recall@20 for ≥ 2 of the 3 commits,
> AND does not lose by more than 0.20 recall@20 on the third.**

The second clause exists so a tool that wins twice by a hair and catastrophically misses once
does not pass. A blast-radius tool that is confidently empty on a real change is worse than no
tool, because a human will trust it.

Ties (equal recall@20) count as **not beating**. The burden of proof is on the new thing.

---

## 4. M2 — dogfood review (bounded judgment)

### 4.1 Procedure

Order is fixed and matters:

1. **Diff-only review first.** Review the merge commit with no pack, no graph, 20-minute box.
   Write every finding to `diff-only.md`. **This is written and saved before the pack is
   generated** — otherwise "the pack found something new" is unfalsifiable, because the diff
   review can always be remembered as having missed it.
2. **Pack-assisted review second**, 20-minute box, findings to `pack-assisted.md`.
3. Classify each pack-assisted finding per §4.2.

Yes, step 2 benefits from step 1's warm-up. That ordering bias favors the pack and is
**recorded as a limitation** (§6) rather than hidden. The reverse order would be worse: it
would let pack knowledge contaminate the baseline.

### 4.2 "Non-obvious" — defined so it can be checked

A finding counts as **non-obvious** only if **all four** hold:

| # | Criterion | How it is checked |
|---|---|---|
| 1 | **Not in `diff-only.md`** | Textual comparison against the file saved in step 1 |
| 2 | **Cites something outside the diff** — a file, service, endpoint, topic, table, or config key that does **not** appear in the changed file set | Mechanical: is the cited path in `git diff --name-only`? |
| 3 | **Verifiable** — the cited `source_file:line` exists and says what the finding claims | Open the file and confirm |
| 4 | **Actionable** — names a specific thing a developer would do or check, not a general observation | Judgment. The one genuinely subjective criterion; log the reasoning per finding |

Criterion 2 is what makes this a test of the *graph* rather than of the reviewer. A finding
derivable from the diff alone may be valuable, but it is not evidence for the substrate.

### 4.3 Threshold — fixed now

> **PASS ⇔ ≥ 2 findings meet all four criteria on the selected merge commit.**

Findings are also tallied as: total, non-obvious, false (cited coupling does not exist).
**Any false finding is reported in the gate record.** A tool that surfaces two real
cross-repo couplings and three phantom ones has not passed cleanly, and the gate record must
say so even when the count clears.

---

## 5. Recorded prediction (fill in before running)

```
Judge: __________          Date: __________
H1 — I expect the pack to beat grep on ___ of 3 commits.
H2 — I expect ___ non-obvious findings.
Confidence (low / medium / high): ______
What result would genuinely surprise me: ______________________________
```

A protocol whose outcome was never in doubt tested nothing. If the prediction and the result
match exactly and the confidence was "high", say so in the gate record and weight the evidence
accordingly.

---

## 6. Known limitations of this evidence

Stated up front so the gate record does not have to pretend otherwise:

1. **The judge is not blind to the project.** Mitigated by mechanical ground truth and a
   mechanical primary metric (§3), but the M2 gate retains one subjective criterion (§4.2 #4).
2. **n = 3 for M1, n = 1 for M2.** These are kill gates, not a study. They are sized to catch
   a bet that is plainly wrong, not to measure effect size. Do not report them as more.
3. **Review order in §4.1 favors the pack** — the second review benefits from the first.
4. **Ground truth is what the fix touched, not what it *should* have touched.** A merged
   commit that missed a coupling scores the tool down for finding it. Rare, but it means a
   near-miss on recall is weaker evidence against the tool than a clean miss.
5. **The estate is one estate.** Passing here says nothing about portability; that is item 19's
   job, deliberately separate.

---

## 7. Gate record template

Both gates append to `docs/m1/gate-record.md` / `docs/m2/gate-record.md`:

```
## <M1|M2> gate — <date>
Protocol version: 2026-08-05 (unamended | amended <date>: <what changed>)
Commits judged: <SHAs>
Prediction on file: <yes/no>, made <date>
Raw results: <table>
Primary metric: <value> vs threshold <value>
Verdict: PASS | FAIL
False findings: <count + detail>
Limitations encountered beyond §6: <text>
Decision taken: <proceed | halt | re-scope to ...>
```

A FAIL is recorded with the same care as a PASS and triggers the off-ramp the milestone
already names. **The register of what we chose not to build is `de-scope-register.md`; this
file is the register of how we agreed to be wrong.**
