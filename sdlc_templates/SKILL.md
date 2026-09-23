---
name: sdlc
description: Run the complete software development lifecycle for one bug fix, feature, or deploy change — intake and baseline, requirements with free/OSS options and EARS criteria, scoping, design with ADRs, worktree-isolated implementation, tests against the recorded baseline, a review loop whose exit is categorical, triage, release, deploy, verify, and close-out. Human gates G1–G5 halt the run; which of them are hard is set by the profile's gate_profile. Use when the user types /sdlc, or asks to implement a feature or fix a bug end-to-end following the standard process. Provider-agnostic and tool-agnostic; driven entirely by .sdlc/profile.yml.
---

# SDLC Harness

A repo-agnostic orchestration loop that takes one input — a bug, a feature, or
a deploy change — through the full lifecycle to a clean close-out. Everything
repo-specific lives in **`.sdlc/profile.yml`**; this skill never hardcodes a
test command, a coverage tool, or a provider. It runs under any model and
inside any AI coding tool.

**You are the orchestrator.** The user gives you *what*; you run *the process*.
Automate every step **except the human gates**, where you STOP and wait for an
explicit approval. Never self-approve, never read approval into silence — and
you could not record one anyway: the approval CLI refuses an entry you write
for yourself, and refuses a yes about work that does not exist yet.

---

<!--harness-only-->
## Running the commands below

Every `tools/...` path in this skill lives under `@@HARNESS@@` — **never under
the repository being worked on** — and is run through this prefix:

```
uv run --project "@@HARNESS@@" python "@@HARNESS@@/<path>" ...
```

So `tools/sdlc/stage.py` is:

```
uv run --project "@@HARNESS@@" python "@@HARNESS@@/tools/sdlc/stage.py" --target . --list
```

`--project` pins uv to the harness's own locked environment — pasted without
it, a bare `uv run` resolves *your* project instead — and the quotes survive a
harness path with a space in it. Do not run a bare `stage.py`: the file is not
in this repository, and **the error that produces has sent a runner
improvising** — one ran a bare `state.py`, got *is not recognized as an
internal or external command*, and stopped with the document it had just
written left unpinned. If this harness clone moves, re-run `sdlc-init.py` to
refresh the paths below.
<!--else-->
## This install teaches no commands

It was written by a standalone `sdlc-init.py` with no harness beside it, so
there is no program to point at and every path one could name would be a file
that is not here. **Do not improvise a command** from the phase table below —
that table names artifacts, not programs, and a runner who guessed at one got
*is not recognized as an internal or external command* and stopped with the
document it had just written left unpinned.

What still works is the lifecycle itself: work the phases in order, write each
phase's artifact by hand under `.sdlc/`, and the approvals stay hash-chained and
locally verifiable. To get the commands, clone the harness and re-run its own
`sdlc-init.py` against this repository; the skill is rewritten with paths that
resolve.
<!--/harness-only-->

---

## Start here (every invocation)

1. **Read `.sdlc/profile.yml`.** It defines `phases`, `gate_profile`,
   `test_command`, `coverage_command`, `review.*`, `vcs.*`. Missing? Tell the
   user to run the installer (`sdlc-init.py`) and stop.
2. **Read `.sdlc/policy.yml`** — the repository's constitution. The profile may
   only *tighten* it. If they disagree, the run refuses; do not edit the
   constitution to make your run legal.
3. **Read `.sdlc/review-rubric.md`** (plus `review.rubric_overlay` if set) so
   you know the scoring model before you write code. The score is **reported,
   never the gate**.
<!--harness-only-->
4. **Ask which phases this workspace runs, rather than assuming:**
   `tools/sdlc/stage.py --target . --list`. It prints the active phases in spec
   order and what it skips, saying for each whether it was turned off or is
   unavailable.
5. **New run or resume?** `tools/sdlc/state.py --target . --list` shows every
   open run. Resume from the recorded phase; do not restart at P0.
<!--else-->
4. **Read `phases` in the profile and say which of P0–P11 this workspace runs**
   before you start. There is nothing here to ask, so the profile is the answer
   and an assumption is not.
5. **New run or resume?** Read the `state.json` named below. Resume from the
   recorded phase; do not restart at P0.
<!--/harness-only-->
6. Announce the run id and the phase you are entering.

---

## Capability adaptation (read once, apply throughout)

Different harnesses expose different power. Detect what you have and degrade
gracefully — **the process is identical; only the mechanism changes.**

| Capability | If your harness has it | If not |
|---|---|---|
| **Graph substrate** | `scoping` builds an impact pack with `source_file:line` provenance | the phase is inactive; explore the repository directly and say the map is unprovenanced |
| **Clarify / choose** | the native question UI | ask in plain chat and wait |
| **Approval gates** | the native plan-approval UI | post the artifact, say "reply `approved` to continue", and STOP |
| **Parallel work** | ≤3 subagents, one per independent workstream, each in its own worktree | sequential in dependency order |
| **Finding generation** | `review.local_review_command` + `review.security_review_command` | review the diff yourself against the rubric |
| **Reviewer subagents** | delegate review to `sdlc-reviewer` / `sdlc-security` — isolated context finds more | adopt the reviewer personas yourself |
| **Issue tracker / chat (MCP)** | offer to pull the task from an issue and post the tracker back | take the task from the user's message |

**Durable state is two files**: `state.json` and the plan/tracker markdown in
`plans_dir`. Everything else is a convenience mirror, and that is what makes a
run resumable across sessions, tools, and providers.

---

## The phases

One numbering, `P0`–`P11`, the same for a single repository and a multi-repo
workspace — they differ only in which phases are active.
<!--harness-only-->
**The procedure for each phase is its SOP**, under `@@HARNESS@@/sdlc/stages/`.
Read the SOP for the phase you are in; do not work from this table, which exists
to tell you which file to open and what the phase owes.
<!--else-->
**The procedure for each phase is its SOP, and this install does not carry the
SOPs** — they live under `sdlc/stages/` in a harness clone. So the table below
is all this file has: it names what each phase owes, and you work the phase from
that plus the profile rather than from a procedure you do not have.
<!--/harness-only-->

| # | Phase | SOP | Owes | Gate at the end |
|---|---|---|---|---|
| P0 | intake | `P0-intake.md` | `00-intake.md` | — |
| P1 | requirements | `P1-requirements.md` | `10-requirements.md` | — |
| P2 | scoping *(graph)* | `P2-scoping.md` | `20-scope.md` | **G1** |
| P3 | design | `P3-design.md` | `30-design.md` (+ `30-adr-*.md`) | **G2** |
| P4 | implementation | `P4-implementation.md` | `40-impl/` | **G3** |
| P5 | test | `P5-test.md` | `50-tests.md` | — |
| P6 | review | `P6-review.md` | `60-review.md` | — |
| P7 | triage | `P7-triage.md` | `70-triage.md` | **G4** |
| P8 | release | `P8-release.md` | `80-release.md` | human merges |
| P9 | deploy *(if configured)* | `P9-deploy.md` | `90-deploy.md` | **G5** |
| P10 | verify *(if probed)* | `P10-verify.md` | `A0-verify.md` | — |
| P11 | closeout | `P11-closeout.md` | `B0-closure.md` | — |

<!--harness-only-->
**This table is the DEFAULT superset, typed by hand — not this repository's
pipeline.** The profile removes steps (`phases`), adds hard stops
(`extra_gates` → `G-<step>`), and moves a built-in gate whose step was removed
to the nearest earlier step that runs; a gate whose step lacks a capability is
dropped. `tools/sdlc/stage.py --target . --list` is authoritative for which
steps run here and which gate stands after each — when it and this table
disagree, it is right.
<!--else-->
**This table is the DEFAULT superset, typed by hand — not this repository's
pipeline.** `phases` in `.sdlc/profile.yml` says which steps run here, and
`extra_gates` adds a hard stop named `G-<step>` at the end of a step. A
built-in gate whose step was removed stands at the end of the nearest earlier
step that runs; one whose step needs a missing capability does not stand.
<!--/harness-only-->

<!--harness-only-->
Get the phase's prompt — its SOP path and every exact value it needs — from the
harness rather than assembling it yourself:

```
tools/sdlc/stage.py --target . --run <id> --phase P4 --prompt --set component=<alias> --set branch=<name>
```

**Record every artifact** with `tools/sdlc/state.py --record`, or it is not
pinned and no gate can stand on it. Check before you move on:

```
tools/sdlc/stage.py --target . --run <id> --phase P4 --verify
```
<!--else-->
**Record every artifact by hand**, in `state.json` and in the tracker, as you
produce it — nothing here will do it for you and nothing will catch you if you
don't. An artifact that is not recorded is not pinned, and no gate can stand on
it. Before leaving a phase, re-read the row above and confirm the file it owes
exists under `.sdlc/` and is named in `state.json`.
<!--/harness-only-->

---

## Gates

`G1` scope + requirements · `G2` design · `G3` agent changes · `G4` pre-merge ·
`G5` pre-deploy. **Which of them halt is `gate_profile`, and G4 always does.**

- `solo` — G2 and G4 halt; the whole branch diff is read at G4.
- `team` — all five halt; **every agent diff** is reviewed individually at G3.
- `regulated` — team, plus `medium` in `block_on` and signed attribution.

**The halt rule is not a dial.** The regime sets how finely agent changes are
reviewed, never whether they are. At a gate: present the artifact, STOP, and
wait. Changes requested → go back to the phase that owns the problem, which for
a design objection is P3 and not P4.

---

## The review loop

`block_on` is **the** exit condition: iterate until no finding at one of those
severities is open.
<!--harness-only-->
Ask the harness rather than judging it yourself —

```
tools/review/loop.py --target . --run <id> --prepass <dir>/prepass.json --iterate --score <n>
```

— which reads the pre-pass findings *and* the ones you recorded, in one
severity vocabulary, and exits **0** may exit · **1** fix and iterate · **3**
the cap is reached with findings still open, which are owed to P7 as tracker
rows rather than dropped.
<!--else-->
There is nothing here to ask, so you decide it yourself and must write the
decision down: list every finding with its severity, and leave the loop only
when none at a `block_on` severity is open. At the iteration cap, findings still
open are owed to P7 as tracker rows rather than dropped.
<!--/harness-only-->

Three rules the loop enforces, so you do not have to remember them: **grade
every finding** (an ungraded one holds the loop open), **a `block_on` severity
cannot be deferred** out of the gate, and **the score never decides** — it is
recorded for continuity because the same diff scores differently across models,
and a model that knows the number drifts toward it.

---

## Abort path (any gate)

Mark the remaining rows skipped with a reason, set the plan status to partially
implemented, leave the working tree clean, and leave the branch in place unless
asked to reset. Then close the run — an abandoned run left open blocks the next
one and biases the review statistics.

## Non-negotiables

- **Gates pause. Always.** No exceptions, no self-approval.
- **The tracker is the source of truth** and is current in real time, not
  batched at the end.
- **Never run a cost-incurring action** without a fresh, explicit yes. An
  approval for one deploy is not an approval for the next.
- **Never call raw `git commit` / `git push` / `git worktree` when
  `vcs.deny_raw_git` is true.** In those repositories raw git publishes to the
  wrong remote or silently drops a second git directory.
- **Never push or open a pull request from P4.** That is P8, after a human yes.
- **Stay provider- and tool-agnostic:** read commands from the profile, adapt
  mechanisms through the capability table, and put nothing provider-specific
  in this file.
