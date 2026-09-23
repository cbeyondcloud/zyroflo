---
id: P4
name: implementation
after: design
inputs: [30-design.md]
outputs: [40-impl/]
conditional: [40-repro.md]
evidence: []
after_gate: []
needs: none
gate: G3
---
# P4 · implementation — one agent per component, isolated, and it never pushes

## Purpose

This is the only phase that writes product code, and the only one where an
agent's output is not read by a human before it moves. **G3 stands at the end
of it** for exactly that reason: in `team` and `regulated` every agent diff is
reviewed individually; in `solo` the whole branch is read at G4. The dial
changes how finely, never whether.

## Inputs

```
tools/sdlc/stage.py --target <repo> --run <id> --phase P4 --prompt --set component=<alias> --set branch=<name>
```

**`worktree` is filled for you** — the harness made the tree and wrote down
where, so asking a caller to repeat it could only introduce a disagreement. A
component denied a tree by `vcs.deny_raw_git`, and a run that has not created
its trees yet, both resolve to the main checkout, which is the truth in each
case rather than a default. Plus the
component's own section of `30-design.md` — an agent reads its section, not the
whole design.

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

1. **Make this run's trees, and let the harness do it.**

   ```
   tools/sdlc/worktree.py --target <repo> --run <id> --create
   ```

   One branch and one linked worktree per component, under the worktrees root
   the repository's own policy names. It records the **base commit** for each,
   which every later diff in this run is computed against. Add `--dry-run`
   first to see the plan without making anything, and `--component <name>` to
   limit it.

   It refuses the whole set rather than making half of them: a run with some
   components isolated and some not produces diffs from two different worlds.
   A dirty component is one of those refusals — a base commit recorded over
   uncommitted work describes a tree nobody can reconstruct.
2. **A component with `vcs.deny_raw_git` gets no worktree**, and the record says
   so and why. A worktree carries only the primary git directory, so a
   repository with a second one silently loses half its state; that component
   works in its own checkout, sequentially. You do not have to detect this —
   `--create` already did, and `--list` shows which mode each component is in.
3. **Test first where practical** — the failing test, then the code that passes
   it. Where it is not practical, say which and why in the artifact.
4. **Commit per tracker row**, so rollback is a revert and the history is
   reviewable. Use `vcs.commit_command` with `{message}` when it is set,
   otherwise `git commit`. Message: `sdlc(<slug>): <row summary>`.

   **The run folder is part of the change, not a side effect of it.** A run is
   `.sdlc/runs/<run-id>/`, committed with the code it describes: the merge gate
   reads the approval ledger out of the BRANCH, so a branch carrying only code
   has nothing to defend a merge with. Commit the folder onto this branch as
   the artifacts land — not once at the end, where a forgotten `git add` is
   discovered by CI after a pull request is already open.
5. **Never push, never open a pull request.** P8 does that, after a human has
   approved. An agent that publishes has taken a gate it was not given.
6. **Stay inside the walls, and ask before you cross one.**

   ```
   tools/sdlc/guard.py --target <repo> --run <id> --path <file> --command <cmd> --diff <file>
   ```

   Exit 1 means blocked, and the refusal is recorded in
   `.sdlc/metrics/guardrail.jsonl` whether or not anyone is watching. There are
   five walls and this list names all of them, because a wall nobody was told
   about is one a runner discovers by hitting it: writes are confined to the
   run's own worktree; the paths the repository declared are protected; the
   diff size is capped; the push target is fixed to this run's branch; and
   **destructive commands are refused — both git operations that rewrite
   history (`deny_git`) and commands that destroy data git never saw
   (`deny_commands`: recursive deletion in any shell, raw device writes,
   `shred`, `truncate`).** The second list is matched by tokens, so every
   spelling of one operation is the same rule, and a command hidden behind
   `&&`, a pipe, an interpreter, `find -exec`, or stuck to a flag instead of
   given as the next word (`cmd /c=…`, `env -S…`) is still judged.

   A refusal is not an obstacle to route around — it is the run telling you the
   change grew past what was approved. Go back to P3. If you need a tree
   cleared, say so rather than reaching for `rm -rf`: a component's own build
   command runs from the profile and does not come through this door.

## What this phase owes

`40-impl/` — a directory, one file per component or workstream, each naming the
files it changed, the tests it added, the commits it made, and anything it
could not do. **An empty directory is a failure, not a pass**: a phase that
produced nothing has not been reviewed by producing nothing.

`40-impl/change.patch` — **the diff itself**, and it is named here because the
diff is the central object of everything downstream and nothing named it. P6
reviews it, G3 approves it, and the defect check reads it. Write it from the
worktree with `git format-patch`, so the patch carries the commits rather than
a snapshot.

```
tools/sdlc/state.py --target <repo> --record <id> --artifact 40-impl/
```

### If this run is a DEFECT, it also owes `40-repro.md`

Not because a checklist says so: because **a fix with no failing-first test is
a claim that something was broken with nothing behind the claim**. Whether it
is owed is not your judgement —

```
tools/sdlc/defect.py --target <repo> --run <id> --check
```

decides it from the run's own `kind`, and this phase is not finished while one
is owed and missing.

What it must contain, and the reason each part is checkable rather than
decorative:

1. **The symptom, as observed.** What failed, and how it was seen to fail —
   not what you concluded was wrong with it. A defect that arrives already
   diagnosed has skipped the part where the diagnosis could have been wrong.
2. **The reproduction.** The regression test, **declared on its own line and
   named in backticks, spelled exactly as the change spells it**:

   ```
   Regression test: `testGetObjectMapper`
   ```

   A heading whose subject is the regression test, with the name on the line
   below it, is read the same way. **Only a declaring line is read**, so the
   rest of the document is free to cite artifacts, commits and run ids in
   backticks without any of them becoming an obligation — which is not a
   convenience but a correction: the first version read every backticked
   identifier anywhere in the document, and on a real reproduction that meant
   seven false obligations and a document that had to be made worse to pass.

   The harness does not guess what a test is called in your language; it checks
   that what you called it is really in `change.patch` — in the changed content,
   not the patch's headers, so a commit sha cannot ground itself. A test named
   only in prose is a claim about work nobody did.
3. **Its output against the UNFIXED source.** The failure, quoted. A test that
   passes before the fix proves the fix changed nothing.

```
tools/sdlc/state.py --target <repo> --record <id> --artifact 40-repro.md
```
