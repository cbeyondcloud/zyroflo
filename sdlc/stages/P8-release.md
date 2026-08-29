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
stage.py --target <repo> --run <id> --phase P8 --prompt --set branch=<name>
```

Blank commands in the profile's `release:` block skip individual steps. Blank
means skipped, and the artifact says which were.

## Steps

1. **Rebase onto the latest upstream first.**

   ```
   worktree.py --target <repo> --run <id> --component <name> --rebase origin/main
   ```

   It reports the upstream commits that arrived since this run's base — **re-run
   the impact check over those** before publishing, because the blast radius was
   computed against a tree that has since moved. A conflict is **aborted, not
   left in the tree**: resolving one is new code nobody has reviewed, so it
   re-enters at G3 rather than sliding into the merge. Exit 1 means conflicts.
2. **Publish the branch** with `vcs.push_command` when it is set, otherwise
   `git push`. The branch is the one `--create` made, from the component's
   `branch_pattern`; the policy layer refuses any other target, and the CI
   ledger gate reads the same pattern to decide which pull request is about a
   run. When `deny_raw_git` is true and `push_command` is blank, the commit
   command already published or publishing is off — **never fall back to raw
   git**. A component whose `role` is `fork` is never pushed to at all.
3. **Open the pull request** with the run's artifacts attached or linked: the
   requirements, the design, the test results, the review record. A reviewer
   who has to reconstruct the run from the diff is doing the work twice.
4. **Watch the real CI.** Not the local suite — the one the merge is gated on.

   ```
   release.py --target <repo> --checks --pr <number> --out <artifacts-out>
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

5. **Draft, do not decide, the release metadata**: version via
   `release.version_command`, changelog via `release.changelog_command`, notes
   from the run's own artifacts when `release.notes_from_run` is set, and the
   tag. Each is a draft the human approves by merging.
6. **Stop at the merge.** Report that the pull request is green and ready, and
   what is waiting behind it.

## What this phase owes

`80-release.md`: the branch and the pull request, which release steps ran and
which were skipped because their command was blank, the CI result with a link,
and the drafted version, changelog and notes.

```
state.py --target <repo> --record <id> --artifact 80-release.md
```
