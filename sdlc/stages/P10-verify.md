---
id: P10
name: verify
after: deploy
inputs: [90-deploy.md]
outputs: [A0-verify.md]
conditional: [A0-verify.json]
evidence: []
after_gate: []
needs: probes
gate: none
---
# P10 · verify — probe what was deployed, and propose the rollback yourself

## Purpose

A deploy that returned zero is not a deploy that worked. This phase closes the
loop by asking the running system, not the deployment tool.

It also does the thing that is hardest to do at three in the morning: when a
probe fails, it writes the rollback proposal and opens a defect while the
context is still here.

## Inputs

```
tools/sdlc/stage.py --target <repo> --run <id> --phase P10 --prompt
```

Plus `90-deploy.md` — what was deployed, where, and what rollback was planned
for it.

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

1. **Probe the impacted surface**, not the whole estate: health and readiness
   for what P9 actually changed. The scope came out of the deploy-impact pack.
2. **Record every probe** — the endpoint, the expected result, the actual one,
   and the time. A probe that was not recorded did not happen.
3. **On failure, propose the rollback** — the concrete command, from P9's plan,
   with what it will and will not undo. Propose; do not run it. Rolling back is
   as much a change to a live system as rolling forward.
4. **Open a defect from the failure**, through the intake adapter, so the
   problem enters the lifecycle as a run rather than as a message somebody has
   to remember.
5. **On success, say what you did not check.** Probes cover what somebody
   thought of. Naming the gaps is how the next run's probes get better.

## What this phase owes

`A0-verify.md`: every probe with its result and timing, the verdict, and either
the rollback proposal and the defect it opened, or the honest list of what was
not covered.

`A0-verify.json`, which `verify.py` writes and `stage.py --verify` asks for. It
carries the machine half of the same answer: the three per-probe statuses, what
was deployed, the rollback proposal, and which defect run the failure opened.

```
tools/sdlc/verify.py --target <repo> --run <id> --probe
tools/sdlc/state.py --target <repo> --record <id> --artifact A0-verify.md
```

Nothing is asked without `--probe`, a failing probe never rolls back, and
`--no-file` probes without opening a defect.
