---
id: P9
name: deploy
after: release
inputs: []
outputs: [90-deploy.md]
conditional: [90-deploy.json]
evidence: []
after_gate: [90-deploy.json]
needs: deploy
gate: G5
---
# P9 · deploy — a fresh yes, every time, because this one costs something

## Purpose

Deploy is the first action in the lifecycle that spends money and changes
something users can see. **G5 stands at the end of this phase and is never
reused**: an approval given for one deploy is not an approval for the next one,
however similar it looks.

The phase is inactive unless the profile sets `deploy_command`. A lifecycle
that cannot deploy should say so rather than offer a step that does nothing.

## Inputs

```
tools/sdlc/stage.py --target <repo> --run <id> --phase P9 --prompt
```

`deploy-command` is filled from the profile — it is exactly what a yes at G5
will run, and it is stated in the prompt so nobody approves a command they were
not shown.

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

1. **Check the run's branch carries the deploy configuration you expect.** The
   deploy runs from this run's WORKTREE, not the repository root — so anything
   under `.sdlc/deploy/`, and any config the deploy reads, must be on the run's
   branch. Fixing a port, a host or a credential path on `master` does not
   reach it, and the failure arrives as a connection error from the deployed
   service rather than as anything naming the cause.

   Both behaviours are correct and they pull against each other: the branch is
   authoritative on purpose, and an operator naturally fixes repository config
   in the repository. Nothing checks this, deliberately — a branch differing
   from its base is the normal case, so a check would fire on every run and be
   switched off within a week. Carry the change over, or say in the artifact
   which configuration this deploy is running with.
2. **Build the deploy-impact pack** for what actually changed in the deployable
   surface — chart values, environment configuration, image references. Where
   the graph is present these carry `source_file:line` provenance like any
   other claim.
3. **Name what a rollback would be**, before deploying. Working it out
   afterwards is working it out under pressure.
4. **State the cost.** A deploy that spends money says how much and on what.
5. **Present, stop, and wait for G5.** The command that will run is in the
   prompt; put it in the artifact verbatim. An approval for "the deploy" that
   does not name the command is not informed.
6. **Deploy only after the yes.** `deploy.py` records what ran, when, from
   which tree and what it returned, into `90-deploy.json` — **you do not write
   the outcome into `90-deploy.md`.** Where components declare their own
   `deploy_command`, deploy each with `--component NAME`: the record keeps
   every one, and this phase is owed until each has deployed.

   That document is what G5 approved, and an approval is of specific bytes:
   appending to it after the yes voids the gate that permitted the deploy, so
   the run cannot reach P10 without either re-taking G5 over bytes nobody
   re-read or forcing it. The harness no longer PINS `90-deploy.json` at G5 for
   the same reason — a gate cannot be a verdict about the outcome of the act it
   authorises — so the outcome has a home that recording it does not break.

## What this phase owes

`90-deploy.md`: the impact pack, the exact command, the cost and the rollback
plan. **The PLAN, and nothing added after the gate** — G5 pins it.

`90-deploy.json`: what the deploy actually did, written by `deploy.py` itself.
Not pinned by G5, because the gate authorised the act that produces it.

```
tools/sdlc/state.py --target <repo> --record <id> --artifact 90-deploy.md
```
