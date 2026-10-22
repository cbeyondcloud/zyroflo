<p align="center">
  <img src="assets/logo.svg" alt="Zyroflo" width="480">
</p>

<p align="center">
  <strong>Zyroflo — the generic SDLC harness.</strong><br>
  AI-driven software delivery under <strong>strict human governance</strong>. Drop it into any repo;
  run your whole lifecycle end-to-end — under any LLM, inside any AI coding tool.
</p>

<p align="center">
  <strong>100% free &amp; open source (MIT)</strong> — no third-party packages, no network, no API keys.<br>
  Product page → <a href="https://cbeyond.cloud/zyroflo" target="_blank" rel="noopener"><strong>cbeyond.cloud/zyroflo</strong></a>
  &nbsp;·&nbsp;
  <a href="https://youtu.be/v_1nk1uJCRE" target="_blank" rel="noopener"><strong>Watch the explainer ▶</strong></a>
</p>

[![CI: runs locally](https://img.shields.io/badge/CI-runs_locally-blue.svg)](CONTRIBUTING.md#run-ci-before-you-ask-for-a-merge)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Zero dependencies](https://img.shields.io/badge/dependencies-0-brightgreen.svg)](#no-packages-no-network-no-api-keys)
[![GitHub stars](https://img.shields.io/github/stars/cbeyondcloud/zyroflo?style=social)](https://github.com/cbeyondcloud/zyroflo/stargazers)

**Zyroflo** is a portable, **provider-agnostic** and **tool-agnostic** software-development lifecycle you drop into **any** repo. One command scaffolds it; one command per task runs the whole lifecycle end-to-end — hand it a bug or a one-line feature and it elaborates the requirement, asks the clarifying questions, then designs, implements, tests and prepares the deploy, with a human at every gate.

🎯 **12-step pipeline · 5 human gates, yours to shape** &nbsp;·&nbsp; 🔌 **7 AI coding tools** &nbsp;·&nbsp; 🧩 **8 stacks auto-detected** &nbsp;·&nbsp; 🧾 **hash-chained approval ledger + CI check** &nbsp;·&nbsp; 📦 **0 dependencies** &nbsp;·&nbsp; 🏷️ **MIT**

> AI can now take a whole feature from ticket to deploy — so the question that decides whether you can actually *use* it is **governance**: when a change ships, who approved it, which model wrote it, and can you prove it later? Zyroflo makes those decisions first-class — gates that stop, a ledger that proves, a pipeline you shaped — so AI-driven delivery stays adoptable where an audit trail is non-negotiable.

**Why teams use it:**
- 🪄 **Drop-in** — one command scaffolds the whole lifecycle into any repo, and fans the same skill out to every AI coding tool it detects, each in its native format. Nothing about how your team already works has to change.
- 🚦 **Human-governed** — the AI proposes; a person disposes. The approval gates are **hard stops**, not checkpoints the pipeline glides past — it does not move until you say so.
- 🧾 **Provable** — every gate decision is a hash-chained line in an append-only ledger, and the installed CI check refuses a merge whose gate is missing, rejected, or approved over bytes that have since moved.
- 🔌 **Any tool, any model, any stack** — no provider, model, or tool name in the lifecycle; commands are profile data. Claude Code, Cursor, Windsurf, Copilot, Gemini, Aider, Codex — and pytest, Maven, cargo, `dotnet`, rspec, ctest, or your own.
- 🧠 **It calls no model unless you switch that on** — by default the AI coding tool you already use does the work, on your subscription. Route a step to a CLI agent or a model endpoint and it is your keys, your providers, your spend, acknowledged before anything runs ([`RUNNER_CONTRACT.md`](RUNNER_CONTRACT.md)).
- 📦 **Zero-dependency** — no third-party packages, no network, no API keys. That promise is a **test**, not a claim.
- ♻️ **Resumable** — durable state is plain markdown in `.sdlc/plans/`, so a run survives across sessions and even across tools.

---

## Quick Start

```bash
# 1. Install into a repo (run at its root, or use --target). Run from a clone of this
#    harness, it also builds that repo's graph into .sdlc/graph; run as a standalone
#    file it scaffolds and says plainly that no graph was built. --no-graph skips it.
python /path/to/sdlc-init.py
#    The FIRST run stops and writes sdlc-project.toml: fill in name, description,
#    owner and criticality (low | medium | high) once, then run it again. It is the
#    one file INIT asks you for - committed with the code, stamped into every run.

# ...or across a workspace of many repos (see "Multi-component workspaces")
python sdlc-init.py --workspace sdlc-workspace.toml

# 2. In any AI coding tool opened on that repo:
/sdlc fix the null-pointer in the CSV importer
/sdlc add pagination to the /orders endpoint
/sdlc resume                                     # continue an interrupted run
```

### No packages, no network, no API keys

Free and OSS (MIT). One scoped exception: the chained graph build runs from the harness clone's locked `uv` environment, and its first run in a fresh clone syncs that environment over the network — `--no-graph` (or the standalone file) keeps the install itself dependency- and network-free.

That promise is a test, not a claim (`tools/tests/test_zero_dependency_parity.py`): with nothing installed and the network blocked, install and build still complete. Formats that need an absent library or grammar (YAML, Java, TypeScript, …) are then **reported as unread, per component, in the build's unresolved output** — the graph is degraded and says exactly where, never silently short.

## What it does

Takes a single input — a bug or feature — through a pipeline of **up to 12 steps** (`P0`–`P11`) with **five human gates** (`G1`–`G5`). That is the *superset*: your `profile.yml` picks which steps run, and a fresh install runs **8 of them**.

```mermaid
flowchart LR
    P0["P0 · Intake<br/>& baseline"] --> P1["P1 · Requirements<br/>clarify + EARS"] --> P2["P2 · Scoping<br/>(graph)"]
    P2 --> G1{{"G1<br/>scope"}} --> P3["P3 · Design<br/>+ ADRs"] --> G2{{"G2<br/>design"}}
    G2 --> P4["P4 · Implement<br/>worktrees, TDD"] --> G3{{"G3<br/>agent changes"}} --> P5["P5 · Test<br/>vs baseline"]
    P5 --> P6["P6 · Review loop<br/>until block_on clear"] --> P7["P7 · Triage"] --> G4{{"G4 · MERGE<br/>hard, always"}}
    G4 --> P8["P8 · Release<br/>PR + CI watch"] --> P9["P9 · Deploy<br/>(if configured)"] --> G5{{"G5<br/>deploy"}}
    G5 --> P10["P10 · Verify<br/>(if probed)"] --> P11["P11 · Close-out<br/>criteria + retro"]

    classDef gate fill:#241127,stroke:#ff3d9a,stroke-width:2px,color:#ff8fc7;
    classDef merge fill:#241127,stroke:#ff3d9a,stroke-width:4px,color:#ff8fc7;
    classDef phase fill:#141a2e,stroke:#2a3350,color:#e8ecf5;
    classDef optional fill:#141a2e,stroke:#2a3350,stroke-dasharray:4 3,color:#9aa3b8;
    class G1,G2,G3,G5 gate;
    class G4 merge;
    class P0,P1,P3,P4,P5,P6,P7,P11 phase;
    class P2,P8,P9,P10 optional;
```

| Step | What happens | Gate after | Solo default |
|---|---|---|---|
| **P0 · intake** 🔒 | clean-tree check, **baseline test run recorded**, scope triage, conventions read | — | ✓ |
| **P1 · requirements** | clarify sweep, 2–4 options (≥1 free/OSS), **EARS** acceptance criteria | — | ✓ |
| **P2 · scoping** | graph impact pack with `file:line` provenance — needs the graph substrate | **G1** | |
| **P3 · design** | per-component change set, test plan, an ADR when a boundary or data model changes | **G2** | ✓ |
| **P4 · implementation** 🔒 | worktree-isolated where the components are on different branches or in different repositories, test-first, a commit per tracker row | **G3** | ✓ |
| **P5 · test** | **no NEW failures** against the baseline; changed-file coverage, loud when unmeasurable | — | ✓ |
| **P6 · review** 🔒 | deterministic pre-pass, then review until no `block_on` severity is open (default `[critical, high]`); the rubric score is reported, never the gate | — | ✓ |
| **P7 · triage** | open findings become top-priority tracker rows; loop back until none are pending | **G4 — merge** | ✓ |
| **P8 · release** | push, PR, **watch the real CI**; version, changelog, tag — the merge click stays human | — | |
| **P9 · deploy** | only when a `deploy_command` is set; a cost-incurring deploy needs a fresh yes every time | **G5** | |
| **P10 · verify** | only when `probes` are set; a failing probe *proposes* a rollback and opens a defect | — | |
| **P11 · closeout** | every EARS criterion verified explicitly, docs, retrospective | — | ✓ |

🔒 = locked: a profile cannot remove it. The normative spec — what each step owes, the capability rules, the gate regimes — is [`sdlc/LIFECYCLE.md`](sdlc/LIFECYCLE.md).

### The gates

`G1` scope · `G2` design · `G3` agent changes · `G4` pre-merge · `G5` pre-deploy. A **hard** gate is a stop: the run does not move until a person records an approval in the ledger. Which gates are hard is `gate_profile`:

| `gate_profile` | Hard gates | Agent changes reviewed |
|---|---|---|
| `solo` *(default for one repo)* | G2 + G4 | the whole branch diff, at G4 |
| `team` *(default for a workspace)* | all five | every agent diff, individually, at G3 |
| `regulated` | all five, plus `medium` in `block_on` and signed (`dsse`) entries | every agent diff, at G3 |

**G4 is the merge gate — hard in every regime**, and the installed CI check reads it. A profile may make a gate *harder* (a `gates:` block entry such as `G1: hard`), never softer.

A gate whose step does not run is never silently lost. On a fresh single-repo install with no graph (`--no-graph`, or the standalone installer) and no deploy command, `sdlc stage --target . --list` prints exactly this:

```
P0 intake --> P1 requirements --> P3 design ==[G2 hard]==> P4 implementation
   +==[G3 advisory]==> P5 test --> P6 review --> P7 triage
   +==[G4 merge]==> P11 closeout
   skipped: scoping, release, deploy, verify (not listed in phases[])
   dropped: G1, G5
```

G1 and G5 are **dropped, with the reason printed**: their steps need a graph and a deploy command this repository does not have, and moving a scope gate onto an earlier step would demand artifacts that step cannot produce. Install from a harness clone and the graph *is* built — then `scoping` could run but is simply not listed, so G1 is instead **moved** to the end of `requirements` (`moved: G1 from P2 scoping to P1 requirements`), advisory under `solo`.

### Shape the pipeline to your process

Everything below is `.sdlc/profile.yml` (plus the org's `.sdlc/policy.yml`); nothing is code.

- **Delete a step** — leave it out of `phases:`. Order is fixed by the spec, not by how you list them. `intake`, `implementation` and `review` are **locked** — a `phases:` that omits one is refused by name, because a run that never takes in the work, builds it, or reviews it is not a shorter lifecycle. A misspelt step is refused, never dropped.
- **Add a hard stop** — `extra_gates: [test]` puts a gate named `G-test` at the end of `test`. An added gate is **always hard**, whatever the regime, and is approved like any other (`approve.py --gate G-test`); CI refuses to merge a run that reached it unapproved. A gate on a step you do not run is refused.
- **Delete a gated step, keep its gate** — a built-in gate whose step you removed is **re-anchored** to the nearest earlier step that runs. Delete `triage` and G4 moves to the end of `review`; `--list` says so (`moved: G4 from P7 triage to P6 review (triage does not run)`). If two built-in gates land on one step they merge into one: the merge gate wins, otherwise the later gate.
- **Org floors** — `.sdlc/policy.yml` can set `required_phases` and `required_gates` (e.g. `[G2, G-test]`). They only tighten: a profile that drops a required step or gate is refused when an approval is recorded, naming the file to change.
- **Custom steps** — the step format ([`sdlc/STEP_SPEC.md`](sdlc/STEP_SPEC.md)) and its validator ship, and the 12 built-in steps are written in it. Loading a custom step from your repository **does not ship yet**.

### Or answer questions: `sdlc config`

`sdlc config` sets the same keys by question and answer, and **never writes a configuration a run would refuse**. There are three ways to answer:

- **In chat: `/sdlc-config`.** The installer adds a `sdlc-config` skill beside `/sdlc` (Claude Code's `.claude/skills/sdlc-config/SKILL.md`, a Cursor or Windsurf rule, a section of the harness block in `GEMINI.md`, `AGENTS.md` and the rest). The agent reads the questions, asks them one at a time, shows the pipeline before and after, and applies each answer through the command below. It ships only when the installer runs from a harness clone.
- **At a terminal: `sdlc config --target . --interactive`.** The same questions, then one previewed change, written only on a yes. Without a terminal it refuses and names the other two ways, rather than wait for input that cannot come.
- **In one line, from a script or an agent:**

```bash
sdlc config --target . --spec                            # every question as JSON: type, choices, current value
sdlc config --target . set extra_gates=test gates.G3=hard  # several pairs are one change
sdlc config --target . --dry-run set gate_profile=team   # the pipeline now and after; writes nothing
sdlc config --target . apply --from answers.json         # {"gate_profile": "team", "extra_gates": ["test"]}
sdlc config --target . --check                           # exit 1 names what a run or INIT would refuse
```

- **The questions come from the committed schemas**, not from a list kept beside them: the project fields (`project.name`, `project.description`, `project.owner`, `project.criticality`, then the optional ones), then `phases`, `gate_profile`, `extra_gates`, `gates.<gate>`, `signing`, `review.block_on`, `model_routing`, `models.default` and `models.<step>`. An empty value (`gates.G3=`) clears an override.
- **Checked the way a run checks it, before anything is written** — the same pipeline resolver, policy floors and model routing. A refused change prints every reason and writes nothing (exit 1). A change is whole or absent: `regulated`, for example, needs `signing=dsse` and `medium` in `review.block_on` in the same change, since each half alone is refused. Every written value is read back through the harness's own readers first, so a value the file cannot carry is refused rather than stored differently.
- **Your file stays yours.** Each answer edits only its own lines; comments and line endings are kept. Every change redraws `.sdlc/pipeline.md`.
- **Before INIT**, `set project.<field>=...` writes `sdlc-project.toml` (from the template when it is not there) and names the required fields still blank; the pipeline keys wait for INIT. **After INIT**, a `project.*` answer goes to `sdlc-project.toml` and to the profile's `project:` block, which runs are stamped from.

Exit `0` = applied (or nothing to change) or clean, `1` = refused, `2` = bad input or could not look.

### Before the first install: `sdlc-project.toml`

The installer asks four questions before it writes anything. The first run writes `sdlc-project.toml` (name pre-filled from the directory, default branch from git), names the blanks, and **stops with exit 2 having installed nothing**. Fill in `name`, `description`, `owner` and `criticality` (`low | medium | high`) — by hand, or with the two `sdlc config` commands the message prints (`--interactive`, or `set project.<field>=...`) — then run it again. A workspace can carry the same fields as a `[project]` table in its own TOML. The values are rendered into `profile.yml`'s `project:` block and stated in every run's intake (P0) prompt; `criticality: high` makes the installer *suggest* the `team` regime, never force it.

### Who runs each step — three execution modes

| Mode | How you choose it | Model and key |
|---|---|---|
| **Driving tool** *(default)* | nothing — `/sdlc` in the AI coding tool you have open | that tool's model, your subscription; the harness reads no key |
| **Declared runner** | `runners:` (a command meeting [`RUNNER_CONTRACT.md`](RUNNER_CONTRACT.md)) + a `phase_runners:` entry, e.g. `implementation: <name>` | the runner's own provider; a `{provider}`/`{model}` placeholder in its command is filled from `models:` |
| **Model endpoint** | `model_routing: true` + a `models:` block of `<step>: <provider>/<model>` (plus `default`) | a provider from `.sdlc/providers.toml`, keyed from the harness `.env` |

The model endpoint is **dormant by default**: `model_routing: false` ships beside a pre-filled `models:` `default: openai/gpt-4o-mini` that routes nothing — no key, no network. An explicit `phase_runners:` entry always wins over `models:`. A step that must *act* (edit files, run suites) cannot be sent to a chat endpoint — naming one for it is refused, and `default` leaves it with the driving tool. A missing key is refused by name before anything spends. `sdlc drive --run <id> --spend <names>` walks the steps between two gates unattended; `--plan` shows who would run each one and spawns nothing. `stage --list` shows each step's resolved actor (`[driving tool]`, `[runner hermes]`, `[openai/gpt-4o-mini]`).

### Seeing the pipeline

- `sdlc stage --target . --list` — the ASCII diagram above, then every step with what it owes, the gate after it, and who runs it; skipped steps and moved or dropped gates carry their reason.
- `.sdlc/pipeline.md` — a Mermaid flowchart plus a step/gate table, written by the installer and committed, so a pipeline change shows up as a diff in review. Regenerate it with `sdlc stage --target . --diagram mermaid`; `--diagram mermaid --check` fails when it is stale.
- `sdlc status --target . --run <id>` — one run drawn on the same diagram: which steps are done, where it is now, and each gate's verdict or `waiting`.

### One command: `sdlc`

In a harness clone, `uv run sdlc <subcommand>` fronts every harness tool (installing the package as a tool also works, but brings the graph tier's dependencies with it, and `sdlc init` needs the clone): `sdlc stage`, `sdlc status`, `sdlc approve`, `sdlc drive`, `sdlc init`, …; bare `sdlc` lists them. Arguments pass through untouched, and every `python tools/sdlc/<tool>.py` path keeps working.

## Why it's generic

Everything workspace-specific lives in **`profile.yml`** — auto-detected (test/coverage/lint/build/deploy commands, threshold, tools). The skill in `.claude/skills/sdlc/SKILL.md` reads the profile and **hardcodes no command, no provider, no model**. The installer fans the same skill out to every AI coding tool it detects (Claude Code, Cursor, Windsurf, Copilot, Gemini, Aider, Codex), each in its native format. Durable state is plain markdown in `.sdlc/plans/`, so a run resumes across sessions and tools.

Genericity runs on three axes, each of which is meant to be a test rather than a claim:

- **Provider-agnostic** — no provider, model, or tool name in the lifecycle. What a non-Claude runner must do is written down in [`RUNNER_CONTRACT.md`](RUNNER_CONTRACT.md) and checked by running it: `tools/provider_smoke.py --runner "<command>"` is the conformance test.
- **Stack-agnostic** — commands are profile data; the harness never assumes pytest or Maven.
- **Scale-agnostic** — see below.

## Two tiers, one lifecycle

**One repo with zero dependencies and a multi-repo workspace with a graph substrate are the same lifecycle.** They differ only in which capabilities are present and which phases the profile activates — not in gates, not in artifacts, not in the review loop. Neither is a degraded version of the other.

| | **Tier 0 — drop-in** | **Tier 1/2 — estate** |
|---|---|---|
| Scope | one repo | N repos (`components[]`) |
| Dependencies | **none** — no packages, no network, no API keys | uv, Docker, Neo4j |
| Phases active | 8 | 12 |
| Change review | whole branch diff at G4 | every agent diff, individually |
| Impact analysis | agent explores the repo | graph blast radius with `file:line` provenance |
| Install | `python sdlc-init.py` | `python sdlc-init.py --workspace sdlc-workspace.toml` |

The full phase list, the capability-degradation table, and the gate regimes are specified in **[`sdlc/LIFECYCLE.md`](sdlc/LIFECYCLE.md)**. Tier 0 is not a stripped-down mode you graduate out of — it is the whole product for a solo developer, and its "no packages, no network" property is a gate the estate tier is not allowed to break.

> **Status.** Tier 0 is complete and is what `/sdlc` runs today. The estate/graph tier is under
> active construction; its current, authoritative state — which extractors, stores and phases are
> live — is tracked in [`SDLC-harness-backlog.md`](SDLC-harness-backlog.md) and specified in
> [`sdlc/LIFECYCLE.md`](sdlc/LIFECYCLE.md), rather than pinned to a date here. `SKILL.md` and the
> per-step SOPs use the same `P0`–`P11` numbering as `LIFECYCLE.md`; there is no second numbering to
> map. Tier 0's "no packages, no network" property is a gate the estate tier is not allowed to
> break.

## Free vs Enterprise

**Everything in this repository is free and open source under MIT, and stays that way** - every phase and gate, the approval ledger and its CI check, the audit trail, metrics and dashboards, the graph tier and every connector. Nothing here is a trial of a paid edition, and nothing will be moved behind a paywall.

Cbeyond is building **Zyroflo Enterprise** on top of this engine. It imports the free modules and never forks them, and nothing here depends on it: the suite's [`tools/tests/test_open_core_split.py`](tools/tests/test_open_core_split.py) checks that no tracked file under `tools/`, `sdlc/` or `sdlc_templates/`, nor `sdlc-init.py`, apart from that guard's own two files, imports the `enterprise` package, writes the path `enterprise/` or uses the quoted word `'enterprise'` as a path segment (Python prose writing `import enterprise` counts too), and that no private path is tracked here. For what it offers, and the design-partner programme, see [cbeyond.cloud/zyroflo](https://cbeyond.cloud/zyroflo).

## Multi-component workspaces

The harness never hardcodes a path, a repository name, or how many there are. Everything about *your* workspace enters through one file that you write and that stays yours:

```bash
cp sdlc_templates/workspace.example.toml sdlc-workspace.toml
```

Set `root` — the directory containing your repos — and run:

```bash
python sdlc-init.py --workspace sdlc-workspace.toml
```

**`root` is the only required field.** Everything else is detected and merely overridable, because asking you to enumerate components, roles and identity collisions *before the first scan* asks questions you cannot yet answer. Discovery finds every child directory with a `.git`, runs the same stack and VCS detection the single-repo path uses, and writes the result into `components[]`.

Come back to the file only for what detection cannot reach: which directories to include or exclude, a component's `role` (a fork and its upstream are byte-identical to a scanner), and `[[canonical]]` ownership — filled in only once the build reports a collision.

Your filled-in copy is gitignored here, `--workspace` takes a path so it can live anywhere, and the leak guard's ban list is generated *from* it — so the one file that describes your workspace is the one file that tells the guard what must never leak out of it.

## What gets installed into a repo

```
sdlc-project.toml                 # what this repo IS - you fill it once, before INIT (see above)
.claude/skills/sdlc/SKILL.md      # + one entry point per detected tool (.cursor/rules/…, GEMINI.md, AGENTS.md, …)
.claude/skills/sdlc-config/SKILL.md  # /sdlc-config, the wizard in chat (a section of the one block for block tools)
.sdlc/profile.yml                 # auto-detected; the one file you edit
.sdlc/policy.yml                  # the repository's constitution: floors a profile may only tighten
.sdlc/providers.toml              # model-provider catalog, read only when model_routing is true
.sdlc/pipeline.md                 # the resolved pipeline as a Mermaid diagram - generated, committed
.sdlc/review-rubric.md
.sdlc/plan-template.md
.sdlc/plans/                      # per-run plan + tracker docs (durable, resumable state)
.sdlc/runs/<run-id>/              # one folder per run: state.json + the hash-chained approvals.jsonl and events.jsonl
.sdlc/metrics/                    # written on demand by metrics.py; audit.db and export/ are derived and gitignored
.sdlc/ledger_check.py             # the approval-ledger and audit-trail gate, as one dependency-free file
.github/workflows/sdlc-ledger.yml # runs it on every PR  (GitHub repos only)
```

## The approval ledger, and why CI checks it

Every gate decision is a line in `.sdlc/runs/<run-id>/approvals.jsonl`, hash-chained with the standard library so an edited line is visible. Hashing alone cannot catch a *wholesale* rewrite — recompute every hash and the file is consistent again — so the installed workflow compares the ledger, and the run's `events.jsonl` [audit trail](#the-audit-trail), against the pull request's base commit and requires each to have been **appended to, never rewritten**. It also refuses a `sdlc/<run-id>` branch whose merge gate — or any other hard gate the run reached, an `extra_gates` stop included — is missing, rejected, or approved over bytes that have since moved.

```bash
python .sdlc/ledger_check.py --ledger --target . --base origin/main
```

That file is generated, self-contained, and standard-library only: no harness clone, no packages, no network. Installing the workflow does not *enforce* it — GitHub only requires a check a branch protection rule names, and that grant is the repository owner's to make.

## The audit trail

Every run also keeps `.sdlc/runs/<run-id>/events.jsonl`, committed with the run folder: append-only and hash-chained with the approval ledger's own hash, so it is tamper-evident the same way. `sdlc status --target . --verify` names any event that was edited, inserted or removed, and the CI check above compares the file with its copy at the base commit.

| Event | Written when | What it records |
|---|---|---|
| `run_opened` | a run is opened | kind, plus `project`, `owner` and `criticality` from `sdlc-project.toml` |
| `step_started` / `step_finished` | a step is entered and left, by `sdlc drive` or by the driving tool moving the run on | attempt, start, end and duration, actor (`runner`, `model-endpoint` or `driving-tool`), runner, provider/model, tokens, cost, exit code, outcome, digests of the artifacts the step produced |
| `gate_waiting` / `gate_decided` | a run stops at a hard gate it has not passed (it is marked `awaiting_approval`) / a person records a verdict | gate and whether it is hard; verdict, approver, the hash of the ledger entry, and the human wait time in ms |
| `loop_back` | a run moves back to an earlier step | from, to |
| `run_closed` | a run is closed or rejected | final status |

- **Extension fields.** A framework built on the harness can add a few ids to every event in an optional `ext` block — declared first with `events.register_extension`, shape-checked (ids, finite numbers, optional per-field `pattern`/`enum`), covered by the chain hash, and supplied once per process with `events.set_default_ext` so events the harness writes itself carry them too. Events without `ext` are unchanged. It is kept in NDJSON exports and in `audit.db`'s `events.payload`; CSV exports and the typed columns do not include it. A faulty default provider is refused before the harness writes anything. Ext values are exported as given (`identity: hash` pseudonymises `approver` only), so never put a person's email or name in one.
- **Installed as a dependency.** The wheel ships the lifecycle content (`sdlc/`, `sdlc_templates/`, `third_party/manifest.json`) under `tools/_content/`; `tools.sdlc.content.root()` returns that packaged copy when installed and the checkout otherwise, and the stage reader finds its SOPs through it. An installed harness records its version from its install record (the git commit it was installed from, else its package version), never from the host repository. Still checkout-only: the SOPs' `<harness>/tools/...` CLI commands, the installer (`sdlc-init.py`), `schema --write/--check`, the `third_party` fetcher and the providers catalog fallback. A framework that keeps its runs out of the committed tree calls `state.set_runs_root(".agentic/local/runs")` once at startup (default `.sdlc/runs`); it is exported as `SDLC_RUNS_ROOT` so spawned harness CLIs agree, must be a relative folder outside `.git` with Windows-safe names, and must hold no tracked files. The clean-checkout check exempts run folders wherever they are. Git-based checks of committed run folders (CI ledger gate, `validate`) then have nothing to check; the framework owns that persistence.
- **Tokens are reported, never guessed.** A runner that reports no usage is recorded as `unavailable - runner reported no usage`, never as 0. A runner reports by appending to the file named in `SDLC_USAGE_FILE` ([`RUNNER_CONTRACT.md`](RUNNER_CONTRACT.md) §1); the harness's endpoint runner writes whatever counts the provider's response carries. A driving tool that ran a step itself can report its own counts with `sdlc stage --target . --run <id> --phase P4 --report-usage input=N,output=N`, recorded as `self-reported`. A step the driving tool ran itself is still timed, from the move that entered it to the move that left it, with its tokens `unavailable` unless it reported them; it is never counted twice.
- **Cost only from a price you declared.** A cost is computed only when `.sdlc/providers.toml` gives the provider a `price_in_per_mtok` / `price_out_per_mtok`; otherwise it is `unpriced`, with the reason. No price ships with the harness.
- **No free text.** Events carry ids, names, statuses, timings and hashes only. Notes, prompts and model output never reach an event, and a gate decision stays in `approvals.jsonl`; the event points at it by hash.

The per-event schema is [`sdlc_templates/schemas/events.schema.json`](sdlc_templates/schemas/events.schema.json).

## Metrics, exports and dashboards

`sdlc metrics --target .` rolls the run folders up into numbers: runs, gates, review, learning and debt, plus per step (attempts, duration p50/p90, outcomes, tokens, cost), per provider/model, the human wait at each gate (p50/p90), and loop-backs `from->to`. A number that cannot be measured prints *why*, never 0. `--json` prints the whole document, `--write` saves it as `.sdlc/metrics/metrics.json`.

| Command | Writes |
|---|---|
| `--export ndjson` / `--export csv` | one file per event type per run: `<repo>/<event_type>/<YYYY-MM-DD>/<run-id>.<fmt>`, plus `<repo>/manifest.json` (per-run event count and chain head, rows per type). CSV columns are fixed per event type. |
| `--export sqlite` | `audit.db`: tables `runs`, `events`, `steps`, `gates`, `loop_backs`, `artifacts` and a `models` view, keyed by each event's chain hash so a re-export never double-counts |
| `--dashboard` | `.sdlc/metrics/dashboard.html`, one offline file (no script, no CDN), plus `series.json` |
| `--report` | `.sdlc/metrics/REPORT.md`: Markdown tables, a Mermaid chart, and the chain verdict |

- **Where exports go:** `--out DIR` > `$SDLC_METRICS_OUT` > `metrics_out` in `.sdlc/policy.yml` (repo-relative only; an absolute path is refused) > `.sdlc/metrics/export/` (`.sdlc/metrics/audit.db` for `--export sqlite`). `<repo>` is the `sdlc-project.toml` name, else the directory name. `--since YYYY-MM-DD` exports only runs with an event on or after that day.
- **No silent clobber:** a re-export that would change a file already written is refused unless you pass `--force`; an identical re-export writes nothing.
- **Grafana:** `sdlc_templates/grafana/` holds a data-source snippet for the SQLite store and a six-panel dashboard. The store's column dictionary is [`docs/metrics-columns.md`](docs/metrics-columns.md); unmeasured values are NULL there, never 0.
- **Org roll-up:** `sdlc metrics --merge <audit.db>... --into org.db` folds many repositories' stores into one with a `repo` column, idempotently. `identity: hash` in `policy.yml` writes approvers as a stable pseudonym (`person-<hex>`) in every export while the ledger itself stays exact; that is pseudonymisation, not anonymity. `sdlc_templates/collector/nightly-sdlc-metrics.yml` is a sample nightly job for an org's own metrics repository; it is not installed.
- **Warehouses:** [`docs/warehouse-loading.md`](docs/warehouse-loading.md) loads the CSV/NDJSON exports into Postgres (or any warehouse) keyed on the event hash.

**The harness holds no database connection string.** It writes files; loading them into Grafana, a warehouse or anything else is the org's own job, with its own credentials.

## Installer reference

| Flag | Purpose |
|------|---------|
| `--target DIR` | Install into `DIR` (default: current directory) |
| `--workspace TOML` | Multi-component install: discover the components a filled-in `sdlc-workspace.toml` describes and render them into `components[]`. Supplies *which* components exist; `--target` still says *where* the config is written |
| `--dry-run` | Show planned writes, change nothing |
| `--tools auto\|all\|<list>` | Which tools to wire (default: `auto` = detect; Claude always included) |
| `--force` | Overwrite a hand-edited `.sdlc/profile.yml` |
| `--claude-extras` | Also install the Claude Code power layer (subagents + hooks + permission allowlist) |
| `--no-graph` | Scaffold only — skip the chained graph build |
| `--bundle OUT` | Emit a single self-contained script (templates base64-embedded) |
| `--dump-templates DIR` | Write the templates out for editing |

Exit status: `0` = scaffold written (graph built, skipped, or honestly absent); `3` = scaffold written but the chained graph build failed (the scaffold is usable; re-run just the build with the command the output prints); `4` = the scaffold is incomplete: a file could not be written after others were (each is named, and nothing is built on it; re-run once it can be); `1` = nothing was installed (bad input, or a file it must write cannot be written - each is named); `2` = bad command line, or INIT stopped for `sdlc-project.toml` (nothing was installed).

Idempotent: re-running updates generated files in place, never duplicates, and never clobbers a hand-edited `profile.yml` (it stages `profile.yml.new` instead). Console output is ASCII and stdout is reconfigured to UTF-8, so it runs on a default Windows cp1252 terminal.

## Supported AI coding tools

The base install is tool-neutral: the installer detects which tools a repo uses and writes the **same** lifecycle into each, in that tool's native entry-point format.

| Tool | Entry point written |
|---|---|
| **Claude Code** | `.claude/skills/sdlc/SKILL.md` (always included) |
| **Cursor** | `.cursor/rules/sdlc.mdc` |
| **Windsurf** | `.windsurf/rules/sdlc.md` |
| **GitHub Copilot** | `.github/copilot-instructions.md` |
| **Gemini** | `GEMINI.md` |
| **Aider** | `CONVENTIONS.md` |
| **Codex** | `AGENTS.md` — the skill composed into its one managed block (detected by `AGENTS.md` or `.codex/`) |

`--tools auto` (the default) wires only the tools it detects; `--tools all` wires every one; `--tools <list>` picks. Pass `--claude-extras` for the Claude Code power layer (below).

## Detected stacks

Python (pytest) · JavaScript/TypeScript (jest/vitest) · Go · Rust · Java (Maven/Gradle) · **.NET/C#** (dotnet test) · **Ruby** (rspec/rake + SimpleCov) · **C/C++** (CMake + ctest) · plus a generic fallback that leaves commands blank for you to fill.

## Repos where raw `git` is unsafe

Some repos must not be driven with plain git — open-core splits with a second git dir, monorepos that route files per destination, signing/policy wrappers. `profile.yml` has a `vcs:` block for this:

```yaml
vcs:
  commit_command: "./checkin-push.sh \"{message}\""   # {message} = commit text
  push_command: ""                                     # {branch} = branch name
  deny_raw_git: true    # harness must NEVER call git commit/push/worktree
  notes: "open-core split: .git is public, .git-commercial is private"
```

The installer detects this automatically: a `.git-*` directory beside `.git` that is a second repository (laid out as a git directory, with `HEAD`, `objects/` and `refs/`, or a clone holding one; a name alone, like git-crypt's `.git-crypt/`, is not one) sets `deny_raw_git: true`, and a repo-level check-in script (`checkin-push.sh`, `commit-push.sh`, …) is wired as `commit_command`. When `deny_raw_git` is on, the harness commits through your script, refuses to `git push`, **and skips git worktrees** — a worktree carries only `.git`, so the second repo would be silently lost. `--claude-extras` mirrors this in `settings.json` (raw `git commit`/`push`/`worktree` denied, your script allowed). If the signal fires but no script is found, `/sdlc` stops at Phase 0 and asks rather than guessing.

## Optional: Claude Code power layer (`--claude-extras`)

The base install is tool-neutral. Add `--claude-extras` to also install a Claude Code-only layer (other tools ignore it):

- **Reviewer subagents** — `.claude/agents/sdlc-reviewer.md` + `sdlc-security.md`. Phase 4 delegates to these isolated-context reviewers for sharper, more independent findings.
- **Permission allowlist** — `.claude/settings.json` auto-allowing exactly the profile's test/coverage/lint/build binaries and safe local git (status/diff/commit/branch/worktree), so the automated loop doesn't stall on prompts. Destructive ops (force-push, `reset --hard`, `clean -f`, `rm -rf`) are explicitly denied. Merges with any existing `settings.json` — never clobbers. A file it cannot merge into (a comment, a trailing comma, a key of the wrong shape) is left as you wrote it and named, and nothing is merged into it until you fix it.
- **Automation hooks** — `.claude/hooks/`: a PostToolUse hook auto-runs the linter after edits (non-blocking), and a SessionStart hook surfaces any in-progress run so you can `/sdlc resume`.
- **Default-pointer block** — a managed block in `CLAUDE.md` / `AGENTS.md` so "fix this bug" / "add this feature" routes to `/sdlc` from session start.

`.mcp.json` isn't installed, but the skill is MCP-aware: if a GitHub/JIRA/Linear or Slack MCP is connected, it offers to pull the task from an issue and post the tracker/close-out back.

```bash
python sdlc-init.py --target ../your-repo --claude-extras
```

## Editing the harness

The canonical templates are `sdlc_templates/{SKILL.md, review-rubric.md, plan-template.md, profile.yml}`. Edit those, then re-run the installer in a repo to refresh every tool. Tune scoring per repo by pointing `review.rubric_overlay` in `profile.yml` at a repo-specific additions file.

For a single droppable file (no `sdlc_templates/` sibling needed):

```bash
python sdlc-init.py --bundle sdlc-init.bundled.py
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md); report vulnerabilities as [SECURITY.md](SECURITY.md) describes, and follow the [Code of Conduct](CODE_OF_CONDUCT.md). Questions and commercial support: [SUPPORT.md](SUPPORT.md).

## License

MIT — see [LICENSE](LICENSE). Third-party software the other tiers use or fetch, and its licences: [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).
