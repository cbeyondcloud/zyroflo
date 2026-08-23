# P6 · review — machines first, then agents, until `block_on` is clear

**Phase** `P6` · **produces** `60-review.md` · **gate** none · **active** always

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
stage.py --target <repo> --run <id> --phase P6 --prompt --set diff=<file> --set component=<alias> --set prepass-out=<dir> --set pack-out=<dir> --set hitl-target=<repo>
```

`rubric`, `block-on` and `max-iterations` are filled for you. `block-on` is the
**resolved** list — the regime can add a severity the profile never wrote.

## Access tiers

MCP where the `sdlc-graph` server is registered; the Python CLIs otherwise, and
every step below is written against the CLI tier because that is the only one
every runner has.

## Steps

1. **Open the review session** — `tools/review/hitl.py --target <hitl-target>
   --begin --component <alias>`. The clock starts here and the M4 gate reads
   what it records. If a previous session is open, close it honestly with
   `--end <verdict>` or `--abandon`; an abandoned review is data.
2. **Deterministic pre-pass** — `tools/review/prepass.py --target <repo> --diff
   <diff> --out <prepass-out>`. Exit **1 is findings, not failure**; exit **3
   is a gate that could not run**, and outranks findings — the change is not
   reviewed. `review_exclude` narrows what is scanned and is reported by name;
   it never narrows the secret gate.
3. **Context pack** — `tools/graph/pack.py --graph-dir .sdlc/graph --diff
   <diff> --component <component> --out <pack-out> --no-svg`. Read the radius
   before the code.
4. **Review**, covering every rubric category. Prefer independent reviewers
   where the harness has them — a reviewer with its own context finds things a
   continuation of your own does not.
5. **Record every finding** through `hitl.py --finding <id> --source <s>
   --disposition <d> --severity <critical|high|medium|low>`. **Grade every
   one.** An ungraded finding holds the loop open, because it has not been
   shown to be outside `block_on`.
6. **Ask the loop, do not decide it** — `tools/review/loop.py --target <repo>
   --run <id> --prepass <prepass-out>/prepass.json --iterate --score <n>`.
   Exit **0** may exit · **1** fix the named findings and iterate again · **3**
   the cap is reached with findings still open, and they are owed to P7.
7. **A `block_on` severity cannot be deferred.** Deferral is honest below the
   line and is a hole at or above it: fix it, or reject it as not real and say
   why.
8. **Close the session** — `hitl.py --target <hitl-target> --end <verdict>`.

## What this phase owes

`60-review.md`: every iteration, its findings with severity and disposition,
the score reported each time, and the final outcome — clean or capped. The loop
writes its own machine record beside it.

```
state.py --target <repo> --record <id> --artifact 60-review.md
```
