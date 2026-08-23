# P0 · intake — the baseline everything later is measured against

**Phase** `P0` · **produces** `00-intake.md` · **gate** none · **active** always

## Purpose

Nothing later in the lifecycle means anything without this phase. "The tests
pass" is not a fact about a change unless somebody wrote down what passed
before it; "the tree is clean" is not a fact unless somebody looked. P0 is
where the run stops being a conversation and becomes a record.

It is also the cheapest place to discover the work has already been done.

## Inputs

Every value comes from the stage prompt — render it with:

```
stage.py --target <repo> --run <id> --phase P0 --prompt
```

`baseline-command` and `conventions` are filled from the profile; the rest are
the run's own coordinates.

## Steps

1. **Clean tree, or stop.** `git status --porcelain`. A dirty tree makes every
   later diff a mixture of your change and somebody else's, and makes rollback
   a guess. If the repository is not under git, say so in the artifact: per-row
   commits and rollback are unavailable and that is a fact the reviewer needs.
2. **Read the VCS block** of `.sdlc/profile.yml`. When `deny_raw_git` is true
   you may never call `git commit`, `git push` or `git worktree` — use
   `vcs.commit_command` / `vcs.push_command`. If `deny_raw_git` is true and
   `commit_command` is blank, STOP and ask: guessing here publishes to the
   wrong place.
3. **Run the baseline suite** — `baseline-command` from the prompt — and record
   the pass count and every failure by name. P5 is measured *against this
   number*, not against zero, so a suite that was already red stays honest
   instead of becoming your fault.
4. **Dedup.** Scan `.sdlc/runs/` and the recent log for prior work on the same
   thing. Finding it here costs a minute; finding it at P6 costs the run.
5. **Triage the scope** — trivial, standard, or epic. This is a *proposal*: the
   human decides at the first gate, and the default is the full process.
6. **Read the repository's own documents** (`conventions`) before its code.
   Then map what the task touches — entry points, related modules, the tests
   that already cover it, the utilities to reuse instead of rewriting.

## What this phase owes

`00-intake.md`, carrying: the clean-tree result, the VCS constraint if any, the
baseline pass count and named failures, what the dedup scan found, the triage
call with its reason, and the map of what the task touches.

Record it, or it is not pinned:

```
state.py --target <repo> --record <id> --artifact 00-intake.md
```
