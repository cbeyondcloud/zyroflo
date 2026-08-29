# P11 · closeout — verify every criterion by name, then feed the next run

**Phase** `P11` · **produces** `B0-closure.md` · **gate** none · **active** always

## Purpose

Close-out is where the run is checked against what it promised at P1, one
criterion at a time. "It works" is not a close-out; a numbered list where every
EARS criterion has a verdict is.

It is also the only phase whose output is an input to a *different* run. What
was learned here either becomes a change to the harness and a tech-debt item in
the backlog, or it is forgotten by the next person to meet it.

## Inputs

```
stage.py --target <repo> --run <id> --phase P11 --prompt
```

Plus `10-requirements.md` — the criteria being verified — and every artifact
the run produced.

## Steps

1. **Walk the EARS criteria individually.** Each one gets a verdict and the
   evidence for it: the test that proves it, the output that shows it. A
   criterion verified "by inspection" is one that will regress unnoticed.
2. **Close every tracker row** — done, or deliberately not done with a reason.
   A row left pending at close-out is a promise nobody kept and nobody withdrew.
3. **Update the documents the change requires.** The repository's own
   conventions files are read at P0 by the next run; leaving them stale is how
   the next run inherits the wrong map.
4. **Read what the run actually recorded before writing the retro** —
   `tools/sdlc/feedback.py --target <repo> --run <id>`. Every gate verdict
   captured what the human changed before saying yes: artifacts edited between
   the agent writing them and the approval, ADR triggers dismissed, scope-add
   proposals answered or left unanswered, review findings rejected. A retro
   written from memory reports the corrections somebody remembers; this reports
   the ones that happened.
5. **Retrospective, split two ways, and neither half is written by hand.**

   What the *harness* got wrong becomes a proposed change to the profile, the
   rubric or an SOP, and it goes through the same gates as any other change:

   ```
   learning.py --target <repo> --propose
   ```

   What the *codebase* got wrong becomes a tech-debt intake, so it is a run
   somebody schedules rather than a paragraph somebody remembers:

   ```
   debt.py --target <repo>            # what is owed, writing nothing
   debt.py --target <repo> --file     # open a run for each
   ```

   **This reads declarations you already made rather than looking for smells.**
   A test in `known_failures`, an advisory in `sca_known_findings`, a review
   finding a human DEFERRED — each was declared so a gate could stay honest
   about something it was letting past, and letting something past is what debt
   is. A finding somebody REJECTED is not debt: they decided it was not real,
   and re-filing it would be a machine overruling a person.

   Items are deduped against OPEN runs, so a recurring smell files once. A run
   that closed while the declaration is still there does NOT hold it filed —
   the debt outlived the attempt, and a tracker that treats a closed run as
   payment is one that quietly forgets.
6. **Rebuild the graph** where one exists, so the next run's scoping sees this
   change instead of the tree before it.
7. **Take the run's trees away** — `worktree.py --target <repo> --run <id>
   --remove`. It refuses a tree with uncommitted work in it and names which
   one: an agent's unfinished edit is still the only copy of that work, and
   removing a worktree deletes the directory. Commit it, or `--force` and say
   in the closure that you did.
8. **Close the run** — `state.py --target <repo> --advance <id> --status
   closed`. A closed run is archived and takes no further approvals.

## What this phase owes

`B0-closure.md`: every criterion with its verdict and evidence, the final
tracker state, the documents updated, the retro split into harness diffs and
tech-debt intakes, and the summary — files changed, tests added, coverage,
review outcome.

```
state.py --target <repo> --record <id> --artifact B0-closure.md
```
