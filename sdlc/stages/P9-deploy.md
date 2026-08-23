# P9 · deploy — a fresh yes, every time, because this one costs something

**Phase** `P9` · **produces** `90-deploy.md` · **gate** `G5` · **active** deploy_command set

## Purpose

Deploy is the first action in the lifecycle that spends money and changes
something users can see. **G5 stands at the end of this phase and is never
reused**: an approval given for one deploy is not an approval for the next one,
however similar it looks.

The phase is inactive unless the profile sets `deploy_command`. A lifecycle
that cannot deploy should say so rather than offer a step that does nothing.

## Inputs

```
stage.py --target <repo> --run <id> --phase P9 --prompt
```

`deploy-command` is filled from the profile — it is exactly what a yes at G5
will run, and it is stated in the prompt so nobody approves a command they were
not shown.

## Steps

1. **Build the deploy-impact pack** for what actually changed in the deployable
   surface — chart values, environment configuration, image references. Where
   the graph is present these carry `source_file:line` provenance like any
   other claim.
2. **Name what a rollback would be**, before deploying. Working it out
   afterwards is working it out under pressure.
3. **State the cost.** A deploy that spends money says how much and on what.
4. **Present, stop, and wait for G5.** The command that will run is in the
   prompt; put it in the artifact verbatim. An approval for "the deploy" that
   does not name the command is not informed.
5. **Deploy only after the yes**, then record what ran, when, and what it
   returned.

## What this phase owes

`90-deploy.md`: the impact pack, the exact command, the cost, the rollback
plan, and — after the gate — the result.

```
state.py --target <repo> --record <id> --artifact 90-deploy.md
```
