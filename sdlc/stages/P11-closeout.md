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
tools/sdlc/stage.py --target <repo> --run <id> --phase P11 --prompt
```

Plus `10-requirements.md` — the criteria being verified — and every artifact
the run produced.

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
   tools/sdlc/learning.py --target <repo> --propose
   ```

   What the *codebase* got wrong becomes a tech-debt intake, so it is a run
   somebody schedules rather than a paragraph somebody remembers:

   ```
   tools/sdlc/debt.py --target <repo>            # what is owed, writing nothing
   tools/sdlc/debt.py --target <repo> --file     # open a run for each
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
6. **Recompute the metrics**, so the numbers a retro cites are the ones the
   records hold rather than the ones somebody remembers:

   ```
   tools/sdlc/metrics.py --target <repo> --write
   ```

   Read its `unmeasured` list before its numbers. **A metric nobody can compute
   is reported as null with a reason, never as zero** — *deployment frequency:
   0* would say this repository deploys nothing, when the truth may be that it
   has never deployed. The same document counts forced ADVANCES beside forced
   approvals, because an override that lives in a run's history rather than the
   approval ledger is still an override.

7. **Rebuild the graph** where one exists, so the next run's scoping sees this
   change instead of the tree before it.
8. **Take the run's trees away** — `tools/sdlc/worktree.py --target <repo> --run <id>
   --remove`. It collects every reason to refuse **before it deletes anything**,
   and names them:

   - *uncommitted work* — an agent's unfinished edit is still the only copy of
     it, and removing a worktree deletes the directory;
   - *a deploy ran from that tree* — **you do not undeploy at close-out.** P9
     deploys from the run's worktree, so a local deploy may still be serving out
     of it. A tree is a resource, not a record: leaving one behind costs disk,
     while removing one out from under a running service costs the service and
     the script that stops it.

   Commit it, stop the service, or `--force` and say in the closure that you
   did. **If `--force` fails anyway** — a file held open, a permission — the
   tree may be PART removed, because `git worktree remove` deletes what it can
   before failing; the error says so and names what was removed before it.
9. **Close the run** — `tools/sdlc/state.py --target <repo> --advance <id> --status
   closed`. A closed run is archived and takes no further approvals.

## What this phase owes

`B0-closure.md`: every criterion with its verdict and evidence, the final
tracker state, the documents updated, the retro split into harness diffs and
tech-debt intakes, and the summary — files changed, tests added, coverage,
review outcome.

```
tools/sdlc/state.py --target <repo> --record <id> --artifact B0-closure.md
```
