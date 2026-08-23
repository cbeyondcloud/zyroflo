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

1. **Branch from the recorded baseline.** The branch name is in the prompt; the
   policy layer refuses a push to anything else.
2. **Isolate, unless you must not.** One worktree per component lets streams
   run in parallel without fighting over the index. **If `vcs.deny_raw_git` is
   true, do not use a worktree**: a worktree carries only the primary git
   directory, so a repository with a second one silently loses half its state.
   Run sequentially in the main checkout instead.
3. **Test first where practical** — the failing test, then the code that passes
   it. Where it is not practical, say which and why in the artifact.
4. **Commit per tracker row**, so rollback is a revert and the history is
   reviewable. Use `vcs.commit_command` with `{message}` when it is set,
   otherwise `git commit`. Message: `sdlc(<slug>): <row summary>`.
5. **Never push, never open a pull request.** P8 does that, after a human has
   approved. An agent that publishes has taken a gate it was not given.
6. **Stay inside the walls.** The policy layer confines writes to the worktree,
   protects the paths the repository declared, and caps the diff size. A
   refusal from it is not an obstacle to route around — it is the run telling
   you the change grew past what was approved. Go back to P3.

## What this phase owes

`40-impl/` — a directory, one file per component or workstream, each naming the
files it changed, the tests it added, the commits it made, and anything it
could not do. **An empty directory is a failure, not a pass**: a phase that
produced nothing has not been reviewed by producing nothing.

```
state.py --target <repo> --record <id> --artifact 40-impl/
```
