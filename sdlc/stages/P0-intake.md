---
id: P0
name: intake
after: none
inputs: []
outputs: [00-intake.md]
conditional: []
evidence: []
after_gate: []
needs: none
gate: none
---
# P0 · intake — the baseline everything later is measured against

## Purpose

Nothing later in the lifecycle means anything without this phase. "The tests
pass" is not a fact about a change unless somebody wrote down what passed
before it; "the tree is clean" is not a fact unless somebody looked. P0 is
where the run stops being a conversation and becomes a record.

It is also the cheapest place to discover the work has already been done.

## Inputs

Every value comes from the stage prompt — render it with:

```
tools/sdlc/stage.py --target <repo> --run <id> --phase P0 --prompt
```

`baseline-command` and `conventions` are filled from the profile;
`baseline` states what P0 actually recorded, read from the run itself;
`spec` is the specification this run exists for, verbatim;
the rest are the run's own coordinates.

**`spec` is intake's document and is not yours to write.** The artifact this
phase owes is `00-intake.md`, which is a different file; writing your report
over the specification leaves every later phase working from the title alone.

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

1. **Clean tree, or stop.** `git status --porcelain`. A dirty tree makes every
   later diff a mixture of your change and somebody else's, and makes rollback
   a guess. If the repository is not under git, say so in the artifact: per-row
   commits and rollback are unavailable and that is a fact the reviewer needs.
2. **Read the VCS block** of `.sdlc/profile.yml`. When `deny_raw_git` is true
   you may never call `git commit`, `git push` or `git worktree` — use
   `vcs.commit_command` / `vcs.push_command`. If `deny_raw_git` is true and
   `commit_command` is blank, STOP and ask: guessing here publishes to the
   wrong place.
3. **Report the baseline this run was opened with.** Do NOT run the suite here.
   The baseline is measured by `intake.py --baseline` at run creation, on the
   clean tree step 1 just checked, and a suite run later describes a *different
   tree* — which is why there is no mechanism to record one now and why adding
   one would be wrong. Read what was recorded (the prompt states it) and put it
   in the artifact: the pass count and every failure by name where it ran, or
   the fact that it did not.

   **The `baseline` value says which case you are in** — a measurement, or a
   line beginning `NOT measured`. Do not read the second off a value you cannot
   find: if the prompt states no `baseline` line at all, that is a defect in
   the harness and not a run opened without one, and it is worth saying so
   rather than sending the operator to re-open a run that is fine.

   **A run opened without `--baseline` cannot be fixed from this phase.** Say so
   in `00-intake.md` and tell the operator, whose act it is: close this run and
   re-open it with `--baseline`. P5 is measured *against this number*, not
   against zero, so a suite that was already red stays honest instead of
   becoming your fault — and with no number, P5 reports `unmeasurable` rather
   than passing.
4. **Dedup.** Scan `.sdlc/runs/` and the recent log for prior work on the same
   thing. Finding it here costs a minute; finding it at P6 costs the run.
5. **Triage the scope** — trivial, standard, or epic. This is a *proposal*: the
   human decides at the first gate, and the default is the full process.
6. **Read the repository's own documents** (`conventions`) before its code.
   Then map what the task touches — entry points, related modules, the tests
   that already cover it, the utilities to reuse instead of rewriting.

## What this phase owes

`00-intake.md`, carrying: the clean-tree result, the VCS constraint if any, the
baseline pass count and named failures, what the dedup scan found, the triage
call with its reason, and the map of what the task touches.

Record it, or it is not pinned:

```
tools/sdlc/state.py --target <repo> --record <id> --artifact 00-intake.md
```
