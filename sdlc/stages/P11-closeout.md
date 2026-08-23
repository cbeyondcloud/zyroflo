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
4. **Retrospective, split two ways.** What the *harness* got wrong becomes a
   proposed change to the profile, the rubric or an SOP — and it goes through
   the same gates as any other change. What the *codebase* got wrong becomes a
   tech-debt intake, so it is a run somebody schedules rather than a paragraph
   somebody remembers.
5. **Rebuild the graph** where one exists, so the next run's scoping sees this
   change instead of the tree before it.
6. **Close the run** — `state.py --target <repo> --advance <id> --status
   closed`. A closed run is archived and takes no further approvals.

## What this phase owes

`B0-closure.md`: every criterion with its verdict and evidence, the final
tracker state, the documents updated, the retro split into harness diffs and
tech-debt intakes, and the summary — files changed, tests added, coverage,
review outcome.

```
state.py --target <repo> --record <id> --artifact B0-closure.md
```
