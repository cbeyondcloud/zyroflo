# P8 · release — publish, watch the real CI, and leave the merge to a human

**Phase** `P8` · **produces** `80-release.md` · **gate** none · **active** always

## Purpose

Everything before this phase happened on a branch nobody else could see. P8 is
where the change becomes visible, and where the claim "the tests pass" stops
being a local measurement and becomes one anybody can check.

**The merge click is human, always.** Not because an agent could not press it,
but because the person pressing it is the record of who accepted the change.

## Inputs

```
tools/sdlc/stage.py --target <repo> --run <id> --phase P8 --prompt --set branch=<name>
```

Blank commands in the profile's `release:` block skip individual steps. Blank
means skipped, and the artifact says which were.

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

1. **Rebase onto the latest upstream first.**

   ```
   tools/sdlc/worktree.py --target <repo> --run <id> --component <name> --rebase origin/main
   ```

   It reports the upstream commits that arrived since this run's base — **re-run
   the impact check over those** before publishing, because the blast radius was
   computed against a tree that has since moved. A conflict is **aborted, not
   left in the tree**: resolving one is new code nobody has reviewed, so it
   re-enters at G3 rather than sliding into the merge. Exit 1 means conflicts.
2. **Check the branch carries the run folder before publishing.** The merge
   gate reads the ledger out of the branch, so a branch with the code and
   without `.sdlc/runs/<run-id>/` opens a pull request CI must refuse — and
   that refusal costs a published branch and an open PR to discover. `push`
   below refuses it for you, but only where the harness does the pushing: a
   `vcs.push_command` you invoke yourself goes around that check, which is why
   this step is here and not only in the code.

   ```
   git ls-tree <branch> -- .sdlc/runs/<run-id>
   ```

   Empty output means it is not there. Commit it, then publish.
3. **Publish the branch** with `vcs.push_command` when it is set, otherwise
   `git push`. The branch is the one `--create` made, from the component's
   `branch_pattern`; the policy layer refuses any other target, and the CI
   ledger gate reads the same pattern to decide which pull request is about a
   run. When `deny_raw_git` is true and `push_command` is blank, the commit
   command already published or publishing is off — **never fall back to raw
   git**. A component whose `role` is `fork` is never pushed to at all.
4. **Open the pull request** with the run's artifacts attached or linked: the
   requirements, the design, the test results, the review record. A reviewer
   who has to reconstruct the run from the diff is doing the work twice.
5. **Watch the real CI.** Not the local suite — the one the merge is gated on.

   ```
   tools/sdlc/release.py --target <repo> --checks --pr <number> --out <artifacts-out>
   ```

   `--out` is not optional in practice: it writes `80-ci.json`, and that file
   is what `stage.verify` reads. A verdict that was only printed is a verdict
   nobody can check afterwards.

   **Four of the five answers are not green, and none of them leaves the
   phase:**

   | answer | what it means |
   |---|---|
   | `green` | every check passed — the only pass |
   | `red` | a check failed. Add `--logs` and loop back to P4 **with the log**: *"CI is red"* is not something P4 can act on, and a failing job's output is. The fix re-enters at G3 |
   | `pending` | a check has not finished. Merging on a promise is not merging on a pass |
   | `none` | the pull request has **no checks at all**. Nothing ran, which is not the same as nothing failed — item 40's supply-chain gate scanned nothing and reported nothing for months on exactly this confusion |
   | `unknown` | a check reported a state this phase has never seen. A vocabulary gap is how a leaked credential once left the review loop clean; it is never read as a pass |

6. **Draft, do not decide, the release metadata**: version via
   `release.version_command`, changelog via `release.changelog_command`, notes
   from the run's own artifacts when `release.notes_from_run` is set, and the
   tag. Each is a draft the human approves by merging.
7. **Stop at the merge.** Report that the pull request is green and ready, and
   what is waiting behind it.

## What this phase owes

`80-release.md`: the branch and the pull request, which release steps ran and
which were skipped because their command was blank, the CI result with a link,
and the drafted version, changelog and notes.

```
tools/sdlc/state.py --target <repo> --record <id> --artifact 80-release.md
```
