---
id: P7
name: triage
after: review
inputs: [60-review.md]
outputs: [70-triage.md]
conditional: []
evidence: []
after_gate: []
needs: none
gate: G4
---
# P7 · triage — what survived review, and the last gate before it can merge

## Purpose

Review produces findings; triage decides what happens to each one and makes
that decision visible. It is the shortest phase in the lifecycle and the most
load-bearing, because **G4 stands at the end of it** — the pre-merge gate, hard
in every regime, and the one the CI ledger check reads at merge time.

This phase had no artifact of its own until item 36. The one phase whose gate
can never be softened was the one phase that owed nothing, which meant G4 could
be approved over an empty record.

## Inputs

```
tools/sdlc/stage.py --target <repo> --run <id> --phase P7 --prompt
```

Plus `60-review.md` and the loop's record — in particular anything the loop
reported as still open when the cap was reached.

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

1. **Every open finding becomes a row**, at the top of the tracker, with a
   priority and a status. Nothing is dropped by hitting the iteration cap;
   hitting the cap is precisely why this list exists.
2. **Rows with a `block_on` severity loop back to P3.** They are not "known
   issues" — the gate that follows this phase is about work that is finished.
   Going back is normal and is recorded.
3. **Say what is deliberately not being fixed**, with a reason and who agreed.
   A deferred low-severity finding is a decision; a deferred critical is the
   gate being routed around, and the loop refuses it upstream.
4. **Check the phase's own arithmetic**: every finding from `60-review.md`
   appears here exactly once, with a disposition. A finding that vanished
   between two documents is the failure this phase exists to prevent.

## What this phase owes

`70-triage.md`: every finding with its disposition and, where it is not being
fixed, the reason and the person. **G4 stands at the end of this phase.**
Present it, stop, and wait — the approval CLI refuses an entry you write for
yourself, and refuses a yes about work that does not exist yet.

```
tools/sdlc/state.py --target <repo> --record <id> --artifact 70-triage.md
```
