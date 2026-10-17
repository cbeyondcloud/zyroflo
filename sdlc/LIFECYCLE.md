# The SDLC Lifecycle — one spec, two capability tiers

This is the normative phase list. It is a **superset**: no workspace runs all of it. Which
phases are active is declared in `profile.yml: phases[]`, so a solo repo and a multi-repo workspace
run the *same* lifecycle under the *same* numbering — they differ only in which capabilities
are present.

The alternative — a "lite" lifecycle and a "full" lifecycle — was rejected. Two phase lists
means two gate implementations, two sets of artifact contracts, and every future phase built
twice. One list with an activation set costs a config field.

## Capability adaptation

Detect what you have and degrade gracefully. **The process is identical; only the mechanism
changes.** Extending the table that already governs provider differences:

| Capability | If present | If not |
|---|---|---|
| **Graph substrate** | `scoping` computes an impact pack — impacted services, endpoints, topics, tables, configs, tests, each with `source_file:line` provenance; review receives the blast radius before reading code | `scoping` is inactive. The agent explores the repo directly and maps what the task touches |
| **Estate config** (`components > 1`) | a worktree per repository and branch, each component in its own directory - isolated where the components are on different branches or in different repositories - cross-component dependent build closure, un-scoped breakage raised as a scope-add | one component. No closure to compute; worktrees optional |
| **Clarify / choose** | native question UI | ask in chat and wait |
| **Approval gates** | native plan-approval UI | post the artifact, say "reply `approved` to continue", STOP |
| **Parallel work** | ≤3 subagents, one per independent workstream, each in its own worktree where the components are on different branches or in different repositories (components of one repository on one branch share one) | sequential in dependency order |
| **Finding generation** | `review.local_review_command` + `review.security_review_command` | review the diff against the rubric yourself |
| **Reviewer subagents** | delegate review to `sdlc-reviewer` / `sdlc-security` (isolated context = more independent findings) | adopt the reviewer personas yourself |
| **Issue tracker / chat (MCP)** | offer to pull the task from an issue and post the tracker back | take the task from the user's message |

Durable state is always **two files**: the machine-readable `state.json` and the human-facing
plan/tracker markdown in `plans_dir`. Everything else is a convenience mirror. This is what
makes a run resumable across sessions, tools, and providers.

## The phases

| # | Name | Active when | Produces | Gate |
|---|---|---|---|---|
| 0 | `intake` | always | clean-tree check · **baseline test run recorded** · dedup scan · scope triage (trivial/standard/epic) · conventions files read → `00-intake.md` | — |
| 1 | `requirements` | always | clarify sweep (scope, data shape, errors, performance, tenancy, config) · **2–4 options, ≥1 free/OSS**, each with SPDX license + perf impact, one recommended · **EARS criteria** → `10-requirements.md` | → G1 |
| 2 | `scoping` | graph substrate | impact map with provenance · cross-run overlap check → `20-scope.md` | **G1** |
| 3 | `design` | always | per-component change-set · test plan · **ADR when a dependency, service boundary, or data model changes** · consistency check → `30-design.md`, `30-adr-*.md` | **G2** |
| 4 | `implementation` | always | one agent per component, worktree-isolated where the components are on different branches or in different repositories · test-first where practical · commit per tracker row · diffs only, never pushes → `40-impl/` | **G3** |
| 5 | `test` | always | **baseline-relative** (no NEW failures) · changed-file coverage, **fail loud when unmeasurable** · SCA · dependent build-check *(estate)* → `50-tests.md` | → G4 |
| 6 | `review` | always | deterministic pre-pass → agent review · ≤`max_iterations` · **exit when `block_on` is clear** → `60-review.md` | — |
| 7 | `triage` | always | open findings become top-priority tracker rows; loop 3→6 until no pending bug rows → `70-triage.md` | **G4** |
| 8 | `release` | always* | push · PR with artifacts · **CI watch** (red → phase 4) · version, changelog, notes, tag · **merge stays human** → `80-release.md` | human merges |
| 9 | `deploy` | `deploy_command` set | deploy-impact pack; cost-incurring deploys need a **fresh** yes every time → `90-deploy.md` | **G5** |
| 10 | `verify` | probes configured | health/readiness of impacted services · failure → rollback proposal + auto defect intake → `A0-verify.md` | — |
| 11 | `closeout` | always | **every EARS criterion verified explicitly** · docs · retro → harness-learning diffs **and codebase tech-debt into intake** → `B0-closure.md` | — |

\* blank commands skip individual steps within `release`.

`intake.py` also writes **`00-spec.md`** at run creation: the specification
verbatim, fenced as data. It is intake's document and **no phase declares it** —
P0 owes `00-intake.md`, which is a different file. They were one file until item
125, and a runner writing the report its SOP asked for therefore destroyed the
specification every later phase is measured against.

**Solo default** — `[intake, requirements, design, implementation, test, review, triage, closeout]`.
Eight phases, no graph, no estate, no deploy. This reproduces the drop-in harness's behavior
exactly, with **G2 ≡ its Gate A** and **G4 ≡ its Gate B**.

## Gates

| Gate | After | `solo` | `team` | `regulated` |
|---|---|---|---|---|
| G1 scope + requirements | 2 | folded into G2 | halt | halt |
| G2 design | 3 | **halt** | halt | halt |
| G3 agent changes | 4 | folded into G4 | **halt per diff** | **halt per diff** |
| G4 pre-merge | 7 | **halt** | halt | halt |
| G5 pre-deploy | 9 | n/a | halt | halt |

**The halt rule is not a dial.** `gate_profile` sets the *granularity* at which agent changes
are reviewed — per-diff, or per-branch at G4 — never whether they are reviewed. In `solo`,
nothing leaves the branch until a human has read the whole diff. What the dial buys is
proportionality: five gates and signed attestations for a one-line fix is overhead that gets
routed around, and a gate people route around protects nothing.

Two structural guards keep the dial honest:

1. **`solo` is rejected when `components > 1`.** Not a warning — a hard refusal. The one case
   where per-branch review is genuinely too coarse is the one case the config cannot select.
2. **Run-scope config may only tighten.** Flags and env vars can raise a gate's severity, never
   lower it. Loosening means editing `policy.yml`, which is itself a change that goes through G3.

The resolved `gate_profile` is written into every ledger entry, so which regime a run used is
a matter of record rather than reconstruction.

## Customising the pipeline

The superset above is fixed; which part of it a workspace runs, and where it stops for a
human, is profile data. `stage.pipeline()` resolves `profile.yml` into the one pipeline
that phase advance, `approve`, `status` and the drawn diagrams all read, and the CI merge
check reads the gates it resolved from the ledger — so the rules below hold identically
everywhere.

**Deleting steps.** `phases[]` *selects*; it does not sequence. Order is always the spec's,
whatever order the list is written in. The rules:

- **Locked steps.** `intake`, `implementation` and `review` cannot be removed. A `phases[]`
  omitting any of them is refused by name: a run that never takes in the work, implements
  it or reviews it is not a shorter lifecycle. `closeout` is deliberately *not* locked — a
  workspace may stop at `triage`.
- A name that is not a step is refused, never dropped. An empty `phases[]` is refused. An
  absent `phases[]` takes the solo default, and the resolver says that it did.
- A step listed without the capability it needs is **skipped-for-cause** (`listed, but this
  workspace has no graph capability`), reported apart from a step skipped by choice (`not
  listed in phases[]`).

**Adding a hard gate.** `extra_gates: [<step>, …]` places a gate named `G-<step>` at the
end of that step. It is **always hard**, whatever `gate_profile` says — adding a stop that
does not stop would be no addition. It halts the run like a built-in gate, is approved the
same way (`approve.py --gate G-<step>`), and the approval pins that step's artifacts. A
gate on a step that is not a step, or on one the profile does not run, is refused. A
built-in gate and an `extra_gates` gate at the same step are two distinct stops; both
stand.

**What happens to a gate whose step does not run.** A built-in gate is never silently lost.
The two reasons a step is absent get opposite treatment:

- **The user deleted the step** → the gate is **re-anchored** to the nearest *preceding*
  active step. A gate approves the work up to a point, and re-anchoring preserves that
  point. This is what keeps G4 — the merge gate — standing when `triage` is deleted: it
  moves to the end of `review`.
- **The step needs an absent capability** (G1 at `scoping` needs a graph; G5 at `deploy`
  needs a deploy command) → the gate is **dropped** and recorded with its reason. Moving a
  scope gate onto an earlier step would demand artifacts that step cannot produce. On the
  solo default with no graph and no deploy command this drops G1 and G5, and `stage --list`
  prints why. (With a graph present, an unlisted `scoping` is a *deleted* step instead, and
  G1 moves to the end of `requirements`.)
- **Two built-ins re-anchored onto one step** merge into one. The merge gate wins; between
  two others, the one later in lifecycle order wins. The absorbed gate is recorded with the
  gate that absorbed it — never discarded without a sentence.

**Making a built-in gate harder.** A `gates:` block entry (`G1: hard`) may make a gate hard
that the regime leaves advisory. Never softer: `advisory` on a gate the regime makes hard is
refused by name, and so is making G4 advisory under any regime.

**Org floors (tighten-only).** `.sdlc/policy.yml` may name `required_phases` (steps every
project must run) and `required_gates` (gates every project must keep as hard stops — a
built-in, or an `extra_gates` `G-<step>`). Both default to empty, which is no floor. They
are checked against the *resolved* pipeline — the active steps and the reconciled hard
gates — when an approval is recorded; a profile that falls below either is refused, naming
`policy.yml`. An org-mandated stop belongs in `required_gates`, not in a project's own
`extra_gates`, which that project can remove.

**The merge check follows the resolved gates.** Every approval stamps the run's resolved
gates into its ledger entry, and CI's merge check requires **every hard gate the run
reached** — not G4 alone — approved over bytes that have not moved. An `extra_gates` stop
left unapproved blocks the merge.

**Seeing the result.** `stage.py --list` draws the resolved pipeline (steps, gates, what is
skipped, moved or dropped, and who runs each step); `stage.py --diagram mermaid` writes the
same pipeline to `.sdlc/pipeline.md`, deterministic and committed, and `--check` fails when
it is stale; `status.py --run <id>` draws one run's position on it.

**Custom steps.** A step is specified by the format in [`STEP_SPEC.md`](STEP_SPEC.md) —
front-matter for the contract, body for the SOP — and the twelve built-in SOPs carry that
same front-matter, held equal to the stage table by a test. The parser and validator ship;
**loading a custom step from an adopter's repository does not**. Writing a spec today
documents a step; it does not add one to a run.

## Review exit

The loop exits when **no `block_on` severity remains open** — default `[critical, high]`;
`regulated` adds `medium`. The rubric score and per-severity counts are **reported, never the
gate**.

A score threshold was the obvious alternative and is worse in two specific ways: different
providers score the same diff differently, so the exit condition stops being reproducible the
moment you swap runners; and a model that knows 9.5 ends the loop drifts toward 9.5. A
severity list is a claim about the code that another reviewer can check.

## Artifacts

All artifacts are OKF-conformant markdown with YAML frontmatter, under `runs/<run-id>/`. Every
committed schema carries `schema_version` from its first write.

Approval is an append-only ledger entry in every mode — one format, one code path. A chat
"approved" is an *input method* that writes the same entry, not a parallel mechanism. Entries
are hash-chained with stdlib `hashlib`, so **tamper-evidence costs no dependencies**;
`signing: dsse` adds in-toto envelopes when cross-person attribution is needed.

**Improving an artifact before approving it is the ordinary case, not a violation.** A gate
pins the bytes that exist when the verdict is recorded, so a reviewer who rewrites the
rollback step and then approves is approving their own version; a later edit still voids the
gate. What changed between the agent's draft and the accepted version is recorded in
`runs/<run-id>/feedback.jsonl` alongside the verdict, the reviewer's note, the review findings
they rejected and the proposals they left unanswered — the run's own account of where the
harness needed correcting, which P11's retro reads instead of remembering.

## The audit trail

Every run keeps `runs/<run-id>/events.jsonl` beside its ledger: what happened, when, and at
what cost. It is **append-only** and **hash-chained with the ledger's own `entry_hash`**
(each event carries `seq`, `prev` and `hash`), and it is committed with the run folder.

It is written by the code paths that already act, never by a second collector, so it cannot
tell a different story from `state.json`:

| Event | Written by | When |
|---|---|---|
| `run_opened` | `state.create` | a run is opened; carries the project's name, owner and criticality |
| `loop_back` | `state.advance` | the run moves to an earlier step |
| `gate_waiting` | `state.advance` | the status becomes `awaiting_approval`, once per gate at that step - which `drive` and `stage.advance` do when a run stops at a hard gate it has not passed |
| `run_closed` | `state.advance` | the status becomes `closed` or `rejected` |
| `gate_decided` | `approve.record` | a verdict is recorded; it points at the ledger entry by hash and carries the wait since `gate_waiting` |
| `step_started` / `step_finished` | `drive.step` | around every step the driver spawns; timings, actor, runner, provider/model, tokens, cost, outcome |
| `step_started` / `step_finished` | `stage.advance` | entering and leaving a step the driving tool runs itself (actor `driving-tool`): timings and outcome, tokens `unavailable`; a step already finished by `drive` or a self-report is not closed twice |

`stage.py --report-usage` is the one other writer: a `step_finished` marked `self-reported`
for a step the driving tool ran itself.

**Rules.** No free text reaches an event: notes, prompts and model output stay out, and an
unknown field is refused. Tokens a runner did not report are `unavailable`, never 0; cost
exists only where `providers.toml` declares a price. **What checks it:** `status.py --verify`
walks every run's chain; the installed CI ledger check walks it too, and requires each run's
`events.jsonl` to be a pure append to its copy at the base commit. The per-commit walk along
the branch covers `approvals.jsonl` only; for events the base comparison is the check.

The event types and their fields are `tools/sdlc/events.py` (`TYPES`, `FIELDS`); the schema
derived from them is `sdlc_templates/schemas/events.schema.json`.

## Status

The phase list, gate regimes and review exit above are **normative and
implemented**. `tools/sdlc/stage.py` holds the same table as data — what each
phase produces, which gate stands at its end, which capability it needs, and
the exact values its runner prompt states — and a test asserts the two agree
row for row, so this document cannot drift from the machine that reads it.

`SKILL.md` and the per-phase SOPs under `sdlc/stages/` are wired onto this
numbering (item 36). The old mapping table is gone because there are no longer
two numberings to map between.

**One correction the wiring made.** Phase 7 named no artifact, and it is the
phase **G4** stands at — the merge gate, hard in every regime. A gate whose
phase owes nothing can be approved over an empty record, which is what made
item 32's artifact pinning cover nothing in the one place it mattered most. P7
now produces `70-triage.md`, and `approve.py` refuses a gate whose phase has
not delivered.
