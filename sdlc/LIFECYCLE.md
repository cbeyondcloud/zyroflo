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
| **Estate config** (`components > 1`) | per-component worktrees, cross-component dependent build closure, un-scoped breakage raised as a scope-add | one component. No closure to compute; worktrees optional |
| **Clarify / choose** | native question UI | ask in chat and wait |
| **Approval gates** | native plan-approval UI | post the artifact, say "reply `approved` to continue", STOP |
| **Parallel work** | ≤3 subagents, one per independent workstream, each in its own worktree | sequential in dependency order |
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
| 4 | `implementation` | always | one agent per component, worktree-isolated · test-first where practical · commit per tracker row · diffs only, never pushes → `40-impl/` | **G3** |
| 5 | `test` | always | **baseline-relative** (no NEW failures) · changed-file coverage, **fail loud when unmeasurable** · SCA · dependent build-check *(estate)* → `50-tests.md` | → G4 |
| 6 | `review` | always | deterministic pre-pass → agent review · ≤`max_iterations` · **exit when `block_on` is clear** → `60-review.md` | — |
| 7 | `triage` | always | open findings become top-priority tracker rows; loop 3→6 until no pending bug rows | **G4** |
| 8 | `release` | always* | push · PR with artifacts · **CI watch** (red → phase 4) · version, changelog, notes, tag · **merge stays human** → `80-release.md` | human merges |
| 9 | `deploy` | `deploy_command` set | deploy-impact pack; cost-incurring deploys need a **fresh** yes every time → `90-deploy.md` | **G5** |
| 10 | `verify` | probes configured | health/readiness of impacted services · failure → rollback proposal + auto defect intake → `A0-verify.md` | — |
| 11 | `closeout` | always | **every EARS criterion verified explicitly** · docs · retro → harness-learning diffs **and codebase tech-debt into intake** → `B0-closure.md` | — |

\* blank commands skip individual steps within `release`.

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

## Status

The phase list, gate regimes, and review exit above are **normative and implemented in
`profile.yml` (schema v2)**. Wiring `SKILL.md` and the phase SOPs onto this numbering is
tracked as plan item 36; until then `SKILL.md` remains the working solo implementation and its
phases map onto this table as:

| SKILL.md | 0 | 1 | 2 | Gate A | 3 | 4 | 5 | Gate B | 6 | 7 | 8 | 9 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **here** | 0 | 1 | 3 | **G2** | 4 | 6 | 7 | **G4** | 3–6 loop | 5 | 11 | 9 |
