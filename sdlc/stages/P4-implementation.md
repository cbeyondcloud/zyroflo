# P4 · implementation — one agent per component, isolated, and it never pushes

**Phase** `P4` · **produces** `40-impl/` · **gate** `G3` · **active** always

## Purpose

This is the only phase that writes product code, and the only one where an
agent's output is not read by a human before it moves. **G3 stands at the end
of it** for exactly that reason: in `team` and `regulated` every agent diff is
reviewed individually; in `solo` the whole branch is read at G4. The dial
changes how finely, never whether.

## Inputs

```
stage.py --target <repo> --run <id> --phase P4 --prompt --set component=<alias> --set branch=<name> --set worktree=<dir>
```

Pass `--set worktree=-` when the work happens in the main checkout. Plus the
component's own section of `30-design.md` — an agent reads its section, not the
whole design.

## Steps

1. **Make this run's trees, and let the harness do it.**

   ```
   worktree.py --target <repo> --run <id> --create
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
5. **Never push, never open a pull request.** P8 does that, after a human has
   approved. An agent that publishes has taken a gate it was not given.
6. **Stay inside the walls, and ask before you cross one.**

   ```
   guard.py --target <repo> --run <id> --path <file> --command <cmd> --diff <file>
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
   `&&`, a pipe, an interpreter or `find -exec` is still judged.

   A refusal is not an obstacle to route around — it is the run telling you the
   change grew past what was approved. Go back to P3. If you need a tree
   cleared, say so rather than reaching for `rm -rf`: a component's own build
   command runs from the profile and does not come through this door.

## What this phase owes

`40-impl/` — a directory, one file per component or workstream, each naming the
files it changed, the tests it added, the commits it made, and anything it
could not do. **An empty directory is a failure, not a pass**: a phase that
produced nothing has not been reviewed by producing nothing.

```
state.py --target <repo> --record <id> --artifact 40-impl/
```
