# P10 · verify — probe what was deployed, and propose the rollback yourself

**Phase** `P10` · **produces** `A0-verify.md` · **gate** none · **active** probes configured

## Purpose

A deploy that returned zero is not a deploy that worked. This phase closes the
loop by asking the running system, not the deployment tool.

It also does the thing that is hardest to do at three in the morning: when a
probe fails, it writes the rollback proposal and opens a defect while the
context is still here.

## Inputs

```
stage.py --target <repo> --run <id> --phase P10 --prompt
```

Plus `90-deploy.md` — what was deployed, where, and what rollback was planned
for it.

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
verify.py --target <repo> --run <id> --probe
state.py --target <repo> --record <id> --artifact A0-verify.md
```

Nothing is asked without `--probe`, a failing probe never rolls back, and
`--no-file` probes without opening a defect.
