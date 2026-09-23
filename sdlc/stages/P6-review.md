---
id: P6
name: review
after: test
inputs: []
outputs: [60-review.md]
conditional: []
evidence: [prepass.json, prepass.md, 60-review-loop.json]
after_gate: []
needs: none
gate: none
---
# P6 · review — machines first, then agents, until `block_on` is clear

## Purpose

Review has a deterministic half and a judgement half, and running them in the
wrong order wastes the expensive one. Secrets and rule matches are found by
tools that do not get tired; the agent's context is spent on what tools cannot
see.

**The exit is categorical.** The loop ends when no finding at a `block_on`
severity is open — not when a score clears a threshold. A score is not
reproducible across providers, and a model that knows the number ends the loop
drifts toward it. The rubric score is reported for continuity and never gates.

## Inputs

```
tools/sdlc/stage.py --target <repo> --run <id> --phase P6 --prompt --set diff=<file> --set component=<alias> --set prepass-out=<dir> --set hitl-target=<repo>
```

`rubric`, `block-on`, `max-iterations` and **`worktree`** are filled for you.
`block-on` is the **resolved** list — the regime can add a severity the profile
never wrote.

**`worktree` is the tree the change under review actually lives in, and every
step below that reads code must be pointed at it, not at `<repo>`.** Since the
parallel-run model a run's change is never in the repository root: it is in
`<worktrees_root>/<run-id>/<component>/`, and the root is still at the base
commit. This SOP said `<repo>` for both, and the cost was measured — the
pre-pass reported a defect the change had just FIXED, and a defect a change
INTRODUCES would not have been visible to it at all. The harness resolves the
path from the run's own record, so it is not a thing anyone has to remember.

## Access tiers

MCP where the `sdlc-graph` server is registered; the Python CLIs otherwise, and
every step below is written against the CLI tier because that is the only one
every runner has.

**`pack-out` is stated by the harness, not supplied.** A context pack is a
DERIVED artifact - `pack.py` says so in its own help - so it belongs in the
gitignored scratch directory and never in git, and the run folder is committed
with the code it describes. Passing `--set pack-out=` is refused rather than
ignored: a supplied value nothing reads is the same defect as a value nobody
supplies, arriving from the other side.

## Running the commands below

Every `tools/...` path in this document lives under `<harness>` — **never under
the repository being worked on** — and is run through this prefix:

```
uv run --project "<harness>" python "<harness>/<path>" ...
```

So step 1's `tools/sdlc/stage.py` is:

```
uv run --project "<harness>" python "<harness>/tools/sdlc/stage.py" --target <repo> --run <id> --phase <P> --prompt
```

`<harness>` is stated in the prompt as `harness:`. Do not run a step's path
relative to the repository: the file is not there, and **the error that
produces has sent a runner improvising** — on walk 4 a runner ran a bare
`state.py`, got *is not recognized as an internal or external command*, and
stopped with the document it had just written left unpinned. `--project` is
load-bearing rather than decorative: a bare `uv run` resolves the PASTER's
project, which the installer records as a degraded run at exit 0, or a
stranger's repository quietly gaining a `.venv`.

## Steps

1. **Open the review session** — `tools/review/hitl.py --target <hitl-target>
   --begin --component <alias> --run <id>`. The clock starts here and the M4
   gate reads what it records. **Name the run.** Without it these rows and the
   run's own record are two accounts of one review with no join between them,
   so a finding you reject never reaches the run's feedback record and the
   retro at P11 sees a review that found nothing. A run id the target does not
   have is refused rather than written. If a previous session is open, close it
   honestly with `--end <verdict>` or `--abandon`; an abandoned review is data.
2. **Deterministic pre-pass** — `tools/review/prepass.py --target <worktree>
   --diff <diff> --out <prepass-out>`. **`<worktree>`, not `<repo>`** — the
   pass copies the touched files out of whatever `--target` names, and only the
   worktree holds the change. Exit **1 is findings, not failure**; exit **3 is
   a gate that could not run**, and outranks findings — the change is not
   reviewed. `review_exclude` narrows what is scanned and is reported by name;
   it never narrows the secret gate.
3. **Context pack** — `tools/graph/pack.py --graph-dir <repo>/.sdlc/graph
   --diff <diff> --component <component> --out <pack-out> --no-svg`. The GRAPH
   is the repository's and is read from `<repo>`; the diff is the change's.
   Read the radius before the code.
4. **Review**, covering every rubric category. Prefer independent reviewers
   where the harness has them — a reviewer with its own context finds things a
   continuation of your own does not.
5. **Record every finding** through `tools/review/hitl.py --finding <id> --source <s>
   --disposition <d> --severity <critical|high|medium|low>`. **Grade every
   one.** An ungraded finding holds the loop open, because it has not been
   shown to be outside `block_on`.
6. **Ask the loop, do not decide it** — `tools/review/loop.py --target <repo>
   --run <id> --prepass <prepass-out>/prepass.json --iterate --score <n>`.
   Exit **0** may exit · **1** fix the named findings and iterate again · **3**
   the cap is reached with findings still open, and they are owed to P7.
7. **A finding this repository has DECLARED is not reported, and nothing else
   is silenced.** `review_suppress` in the profile is the learning loop's
   output: a finding humans rejected across separate runs, proposed by
   `learning.py`, landed through a corpus check and a G3, and recorded in an
   audit that also records its reversal. What it silenced is COUNTED in the
   loop's verdict rather than dropped, so *"the review found nothing"* and
   *"the review was told to say nothing"* stay different sentences.

   An entry written by hand is not refused — a person may have good reason —
   but `learning.py --audit` names it, because a suppression that passed no
   gate should not be indistinguishable from one that did.

8. **A `block_on` severity cannot be deferred.** Deferral is honest below the
   line and is a hole at or above it: fix it, or reject it as not real and say
   why.
9. **Close the session** — `tools/review/hitl.py --target <hitl-target> --end <verdict>`.

## What this phase owes

`60-review.md`: every iteration, its findings with severity and disposition,
the score reported each time, and the final outcome — clean or capped. The loop
writes its own machine record beside it.

```
tools/sdlc/state.py --target <repo> --record <id> --artifact 60-review.md
```
