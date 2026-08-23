# P5 · test — baseline-relative, and loud when it cannot measure

**Phase** `P5` · **produces** `50-tests.md` · **gate** none · **active** always

## Purpose

The question this phase answers is not "do the tests pass". It is "did this
change break anything that worked before, and is the new code covered". Those
are different questions, and only the first survives a repository that was
already red.

## Inputs

```
stage.py --target <repo> --run <id> --phase P5 --prompt --set baseline=<count>
```

`test-command`, `coverage-command` and `coverage-threshold` come from the
profile. `baseline` is P0's recorded number.

## Steps

1. **Run the full suite** and compare against `baseline`. The bar is **no NEW
   failures**, not zero failures. A test that was red at P0 and is red now is
   evidence, listed by name; a test that was green and is now red stops the
   phase.
2. **Read the exit code from the runner itself.** Piping a suite through
   another command and reading *that* exit status reports the pipe's success,
   not the suite's — the harness has made this mistake and it reads exactly
   like a green run. Redirect to a file and read the exit code directly.
3. **Coverage on changed files**, per `coverage_scope`. If `coverage-command`
   is blank or the tool cannot produce a number, **say it is unmeasurable and
   treat that as a finding**. An unmeasured threshold silently reported as met
   is worse than no threshold.
4. **Dependent builds**, where the workspace has more than one component: build
   what depends on what changed. Breakage outside the approved scope is a
   scope-add, which goes back through the design gate rather than being fixed
   quietly.
5. **New failures loop to P4** — and re-entering P4 re-triggers G3, because the
   fix is agent-written code nobody has read yet. Moving backwards is legal in
   this lifecycle and is recorded in the run's history.

## What this phase owes

`50-tests.md`: the baseline number, the current number, every failure by name
with its verdict (pre-existing / new), the changed-file coverage figure or the
reason there is none, and the dependent-build results where they apply.

```
state.py --target <repo> --record <id> --artifact 50-tests.md
```
