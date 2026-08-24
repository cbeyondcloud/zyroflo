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

The values above are the workspace's TOP-LEVEL fallbacks. Where the workspace
has components, each one declares its own `build_command`, `test_command`,
`coverage_command`, `coverage_threshold` and `known_failures` in
`profile.yml: components[]`, and the engine reads those — a single command is
the single-repository case.

## Steps

1. **Run the engine.** It computes which components the change reaches, orders
   them dependencies-first, runs each one's declared commands, and judges every
   result against P0's baseline:

   ```
   suite.py --target <repo> --run <id> --execute
   ```

   Seed it yourself with `--changed <names>` only when the run has no worktree
   diffs to derive from; `--plan` shows the closure and runs nothing.
2. **The bar is no NEW failures**, not zero failures. A component whose
   baseline was already red needs its pre-existing failures named in
   `known_failures` — with an empty quarantine there is no failure set to
   compare against, and the engine refuses rather than passing.
3. **Read the exit code from the runner itself.** Piping a suite through
   another command and reading *that* exit status reports the pipe's success,
   not the suite's — the harness has made this mistake and it reads exactly
   like a green run. Redirect to a file and read the exit code directly.
4. **Coverage on changed files.** The engine reads Cobertura, JaCoCo and LCOV
   reports and matches them against the files this run changed. A blank
   `coverage_command`, a missing report, a report that predates the command, or
   a report naming none of the changed files all mean **unmeasurable — which is
   a finding, not a pass**. An unmeasured threshold silently reported as met is
   worse than no threshold.
5. **Run the SCA gate** over the components the change touches:

   ```
   sca.py --target <repo> --components <names> --out <artifacts-out>
   ```

   It walks each component and reads whatever dependency manifests it finds —
   the formats are the scanner's answer, not a list in the harness — and every
   known vulnerability becomes a finding graded by its CVSS score, weighed
   against `block_on` by the same rule the review loop uses. A vulnerability
   nobody has scored is **ungraded**, which holds the phase open rather than
   being rounded down. **Exit 3 means it could not look** — no vendored binary,
   or a component whose manifests it cannot read — and that is never a clean
   bill of health. `50-sca.json` is owed once the engine has run.

6. **Breakage outside the approved scope is a scope-add**, not a fix to make
   here: fixing it puts unreviewed work inside a reviewed change. Write the
   proposal and stop —

   ```
   suite.py --target <repo> --run <id> --scope-add
   ```

   — and it goes back through **G1** with the rest of the scope.
7. **New failures loop to P4** — and re-entering P4 re-triggers G3, because the
   fix is agent-written code nobody has read yet. Moving backwards is legal in
   this lifecycle and is recorded in the run's history.

## What this phase owes

`50-tests.md`: what the engine found, in prose a human reads — the components
it reached and why, each verdict with the baseline it was judged against, the
changed-file coverage figure or the reason there is none, and the dependent
results. `50-suite.json` is the engine's own record and is written for you, and
`50-sca.json` is the SCA gate's.
`50-scope-add.md` is owed **exactly when** breakage lands outside the approved
change set; `stage.py --phase P5 --verify` says so, and the run does not leave
the phase while it is missing.

```
state.py --target <repo> --record <id> --artifact 50-tests.md
```
