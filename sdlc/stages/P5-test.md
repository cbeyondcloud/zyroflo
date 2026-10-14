---
id: P5
name: test
after: implementation
inputs: []
outputs: [50-tests.md]
conditional: [50-scope-add.md]
evidence: [50-sca.json, 50-sca.md, 50-suite.json]
after_gate: []
needs: none
gate: none
---
# P5 · test — baseline-relative, and loud when it cannot measure

## Purpose

The question this phase answers is not "do the tests pass". It is "did this
change break anything that worked before, and is the new code covered". Those
are different questions, and only the first survives a repository that was
already red.

## Inputs

```
tools/sdlc/stage.py --target <repo> --run <id> --phase P5 --prompt
```

The values above are the workspace's TOP-LEVEL fallbacks. Where the workspace
has components, each one declares its own `build_command`, `test_command`,
`coverage_command`, `coverage_threshold` and `known_failures` in
`profile.yml: components[]`, and the engine reads those — a single command is
the single-repository case.

A blank `test-command:` in the prompt is a **stated absence, not a value the
harness dropped**: `.sdlc/profile.yml` declares no top-level `test_command` (the
key is absent, or set to `""` — a default install on an unrecognised stack does
the latter). It means one of two things, and the engine below tells them apart,
not you: either the workspace's components each declare their own suite, which
`suite.py` reads and runs, or nothing declares one anywhere — and a workspace
with no suite to run is **a finding, not a pass**. So do not read the blank as
"tests are green"; run step 1's engine either way and let it refuse a workspace
it cannot measure rather than reporting green over silence.

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

1. **Run the engine.** It computes which components the change reaches, orders
   them dependencies-first, runs each one's declared commands, and judges every
   result against P0's baseline:

   ```
   tools/sdlc/suite.py --target <repo> --run <id> --execute
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
   `coverage_command`, a missing report, a report the command did not write, or
   a report naming none of the changed files all mean **unmeasurable — which is
   a finding, not a pass**. An unmeasured threshold silently reported as met is
   worse than no threshold.
5. **Run the SCA gate** over the components the change touches:

   ```
   tools/review/sca.py --target <repo> --components <names> --out <artifacts-out>
   ```

   It walks each component and reads whatever dependency manifests it finds —
   the formats are the scanner's answer, not a list in the harness — and every
   known vulnerability becomes a finding graded by its CVSS score, weighed
   against `block_on` by the same rule the review loop uses. A vulnerability
   nobody has scored is **ungraded**, which holds the phase open rather than
   being rounded down. **Exit 3 means it could not look** — no vendored binary,
   or a component whose manifests it cannot read — and that is never a clean
   bill of health. `50-sca.json` is owed once the engine has run.

   **A pre-existing vulnerability you are not fixing here is DECLARED, never
   forced past.** `sca_known_findings` in the profile is `known_failures` one
   gate along: a listed finding is reported and not owed, and anything not
   listed still blocks, so a CVE published tomorrow against a dependency
   declared today is still new. One entry names one vulnerability in one
   package version — `Maven org.postgresql:postgresql 42.7.11 CVE-2026-54291`
   — and the version is part of the identity on purpose, so a bump stops the
   match and somebody looks again. An entry matching nothing is reported, so
   the list cannot quietly rot.

   Draft one from a real scan rather than by hand, and paste only what you
   accept:

   ```
   tools/review/sca.py --target <repo> --propose-quarantine
   ```

   **State what the declaration silenced, in `50-tests.md`.** A declared
   finding is not owed, which is the point — and a gate that quietly stops
   reporting is the worst thing in this project's book, so the two are held
   apart rather than traded off. `suite.sca_silenced` gives the count, the
   advisories by name, and any stale entries; the evidence must carry them, so
   that *"the gate reported nothing"* and *"the gate was told to report
   nothing"* are never the same sentence to whoever reads it at G3.

   **Declaring is not the same as forcing, and the difference is the point.**
   A run forced past its supply-chain findings and a run that has just
   introduced a critical CVE leave the same override in the same ledger; once
   forcing is routine, nobody reads either. A declaration says which findings,
   at which versions, and stops covering them the moment either changes.

6. **Breakage outside the approved scope is a scope-add**, not a fix to make
   here: fixing it puts unreviewed work inside a reviewed change. Write the
   proposal and stop —

   ```
   tools/sdlc/suite.py --target <repo> --run <id> --scope-add
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
tools/sdlc/state.py --target <repo> --record <id> --artifact 50-tests.md
```
