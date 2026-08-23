# P1 · requirements — questions first, then options, then testable criteria

**Phase** `P1` · **produces** `10-requirements.md` · **gate** none · **active** always

## Purpose

The expensive defects are agreed to in this phase, not written in P4. A
requirement nobody questioned becomes a design nobody questioned, and by the
time it is code the cost of the question has gone up by two orders of
magnitude.

The output is not prose about intent. It is a list of statements that can each
be shown true or false at close-out.

## Inputs

```
stage.py --target <repo> --run <id> --phase P1 --prompt
```

Plus `00-intake.md` — the map, the baseline, and the triage call.

## Steps

1. **Clarify sweep.** Ask across the fixed taxonomy, skipping only what is
   already unambiguous: **scope** (in and out), **data shape** (inputs and
   outputs), **error behaviour**, **performance**, **tenancy and isolation**,
   **configuration** (what must be tunable). Batch the questions into one
   round and wait. A taxonomy is used because the questions people forget are
   the same six every time.
2. **Options — two to four, at least one free or open-source.** For each:
   the new dependencies it adds with their **SPDX licence**, and its
   performance impact. Recommend one, in one line. An option list with no
   recommendation moves the decision without informing it.
3. **Acceptance criteria in EARS form** — `WHEN <trigger> THE SYSTEM SHALL
   <response>`. Every one must be objectively testable, because every one is
   verified individually at P11. A criterion you cannot imagine failing is not
   a criterion.
4. **Name the standards that apply** from the profile and the repository's own
   documents — the coverage threshold, the review rules, anything the change
   must satisfy that the task did not mention.

## What this phase owes

`10-requirements.md`: the questions and their answers, the options with
licences and performance, the recommendation and why, and the EARS criteria as
a numbered list P11 can walk.

```
state.py --target <repo> --record <id> --artifact 10-requirements.md
```
