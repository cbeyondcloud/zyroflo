---
name: sdlc
description: Run the complete software development lifecycle for one bug fix or feature — explore, elaborate requirements with free/OSS options, plan with tests, get approval, implement in tracked (optionally parallel) steps, run a self-scoring code-review loop, triage findings, fix, test to a coverage gate, and close out. Use when the user types /sdlc, or asks to implement a feature or fix a bug end-to-end following the standard process. Provider-agnostic and tool-agnostic; driven entirely by .sdlc/profile.yml.
---

# SDLC Harness

A repo-agnostic orchestration loop that takes a single input — a bug or feature description — through the full lifecycle to a clean close-out. Everything repo-specific lives in **`.sdlc/profile.yml`**; this skill never hardcodes a test command, coverage tool, or provider. It runs under any LLM and inside any AI coding tool.

## Prime directive

You are the **orchestrator**. The user gives you *what* to build or fix; you run *the process*. Automate every step **except the two human approval gates (A and B)** — at those you STOP and wait for an explicit "approved" from the user. Never self-approve, never assume approval from silence.

---

## Start here (every invocation)

1. **Read `.sdlc/profile.yml`.** It defines `test_command`, `coverage_command`, `coverage_threshold`, `review.*`, `tools`, etc. If it is missing, tell the user to run the installer (`sdlc-init.py`) first and stop.
2. **Read `.sdlc/review-rubric.md`** (+ `review.rubric_overlay` if set) so you know the scoring model before you write code.
3. **New run or resume?**
   - `/sdlc resume` (or "resume the sdlc run") → find the newest file in `plans_dir` whose metadata **Status ≠ ✅ Fully Implemented**. Continue from its recorded **Phase** and the first tracker row that is not ✅/❌. Do not restart from Phase 0.
   - Otherwise → new run, begin at **Phase 0**.
4. Announce the plan doc path you will use and which Phase you are entering.

---

## Capability adaptation (read once, apply throughout)

Different harnesses expose different power. Detect what you have and degrade gracefully — the process is identical; only the mechanism changes.

| Capability | If your harness has it | If not |
|---|---|---|
| **Clarify / choose** | Use the native question UI | Ask in plain chat and wait for the reply |
| **Approval gates** | Native plan-approval UI if present | Post the plan/report in chat, say "reply `approved` to continue", and STOP |
| **Parallel work** | Spawn ≤3 subagents, one per independent workstream, ideally each in an isolated git worktree | Implement rows sequentially in dependency order |
| **Finding generation** | Run `review.local_review_command` + `review.security_review_command` from the profile | Review the diff yourself against the rubric |
| **Reviewer subagents** | If `sdlc-reviewer` / `sdlc-security` subagents exist, delegate Phase-4 review to them (isolated context = more independent findings) | Adopt the reviewer personas yourself in a single pass |
| **Issue tracker / chat (MCP)** | If a GitHub/JIRA/Linear MCP is connected, offer to pull the task from an issue and post the tracker + close-out back as a comment; if Slack is connected, offer gate notifications | Take the task from the user's message and report in chat |
| **Task tracking** | Mirror rows into the native task list for visibility | The plan-doc tracker is the source of truth regardless |

The **durable state is always the plan/tracker markdown file** in `plans_dir`. Anything else is a convenience mirror. This is what makes a run resumable across sessions and across tools.

---

## The phases

### Phase 0 — Intake & Baseline
1. **Clean tree check:** run `git status --porcelain`. If dirty, STOP and ask the user to commit/stash first (a clean baseline is required for reliable diffs and rollback). If the repo is not a git repo, warn that per-row commits and rollback will be unavailable, and ask whether to continue.
2. **Baseline test run:** run `profile.test_command`; record the pass/fail count. This distinguishes pre-existing failures from ones you introduce. Save the number — Phase 7 compares against it.
3. **Dedup check:** scan `plans_dir/*` and `git log --oneline -30` for prior related work; if found, ask whether to resume/extend instead of starting fresh.
4. **Scope triage:** classify the task **trivial** (one-liner, no new behavior), **standard**, or **epic** (multi-subsystem). For *trivial*, you may propose a condensed path (skip some ceremony) — but the user still approves at Gate A, and the default is the full process.
5. **Understand the repo:** read the `conventions_files` from the profile (CLAUDE.md / AGENTS.md / README) and map the code the task touches — entry points, related modules, existing tests, patterns to reuse. Prefer reusing existing utilities over new code. Where supported, use parallel read-only explore agents.

### Phase 1 — Requirements Elaboration
1. **Clarify sweep** — ask targeted questions across this fixed taxonomy (skip any that are already unambiguous): **scope** (in/out), **data shape** (inputs/outputs), **error behavior**, **performance/latency**, **tenancy/isolation** (if multi-tenant), **config** (what must be tunable). Batch the questions; wait for answers.
2. **Design options** — present **2–4 options**. **At least one must be the free / OSS / least-cost choice**, and you must recommend one with a one-line rationale. For each option state: new dependencies + **SPDX license** (prefer *none*), and **performance impact**.
3. **Acceptance criteria** — write them in **EARS** form: "WHEN `<trigger>` THE SYSTEM SHALL `<response>`." Each must be objectively testable — you will verify every one at close-out.
4. Note which gates/overlays from the profile and repo standards apply.

### Phase 2 — Planning
1. Copy `.sdlc/plan-template.md` to `plans_dir/sdlc-<slug>.md` (slug = short kebab of the task).
2. Fill: metadata block (set **Phase**, **Branch** = `sdlc/<slug>`, **Baseline** = Phase-0 count), Context, the **acceptance criteria**, the **Implementation Tracker** (every deliverable — code, tests, docs — its own row; **all rows ⏳ Pending**; include the **Depends-on** column to expose parallelism), a **Test Plan** targeting `coverage_threshold` on changed files, and the Definition-of-Done checklist.
3. **Consistency check (do not skip):** every acceptance criterion maps to ≥1 tracker row **and** ≥1 planned test; no tracker row is an orphan. Fix mismatches before the gate.

### ▲ GATE A — Plan approval (HUMAN)
Present the plan doc. **STOP.** Wait for explicit approval.
- Changes requested → return to **Phase 1**, revise, re-present. Loop until approved.
- Do not write any implementation code before approval.

### Phase 3 — Implementation
1. Create branch `sdlc/<slug>` from the clean baseline (skip if non-git, with the earlier warning).
2. Partition tracker rows into **workstreams** by file overlap + the Depends-on column (rows touching the same file share a stream). Where the harness supports it, run up to **3 streams in parallel, each in its own git worktree**; otherwise sequential in dependency order.
3. Implement **test-first where practical** (write the failing test, then the code).
4. **Only the orchestrator edits the tracker.** As each row lands: mark it ✅ in the plan doc, update **Last Updated** + **Status** in metadata, and **commit** (`git commit -m "sdlc(<slug>): <row summary>"`) for atomic rollback and a reviewable history.
5. Do not push or open a PR unless the user asks.

### Phase 4 — Code Review Loop
Review the full branch diff vs baseline. Each iteration:
1. **Generate findings.** Prefer independent reviewers: if the `sdlc-reviewer` and `sdlc-security` subagents exist, delegate to them (each has its own context). Else if `review.local_review_command` is set, run it (plus `review.security_review_command`). Else review the diff yourself, adopting the matching **reviewer persona** per category (security reviewer, architect, QA). Cover every rubric category.
2. **Score** per `.sdlc/review-rubric.md` (+ overlay): sum category points, apply the deterministic deductions. Append the score + every finding to the **Code Review Log** section of the plan doc.
3. If **score ≥ `review.review_threshold`** → exit the loop. Else fix the findings (new commits) and re-review.
4. **Hard cap:** stop after `review.max_iterations` even if still below threshold; record that the cap was hit.

### Phase 5 — Findings Triage
Insert any findings still open at the **top** of the tracker as new rows, each with a priority — 🔴 Critical / 🟠 High / 🟡 Medium / 🟢 Low — and status ⏳ Pending.

### ▲ GATE B — Post-review approval (HUMAN)
Present the review report (score history + triaged findings) and the updated tracker. **STOP.** Wait for explicit approval.

### Phase 6 — Fix Cycle
For approved fixes, re-enter at **Phase 1** (lightweight — skip Phase 0 exploration). Loop Phases 1→5 until there are **zero pending bug rows**.

### Phase 7 — Test & Coverage Gate
1. Run `profile.test_command` (full suite). Require **pass count ≥ baseline and 0 failures**.
2. Run `profile.coverage_command`; enforce `coverage_threshold` on **changed files** (per `coverage_scope`).
3. Any failure or shortfall → add tracker rows → back to **Phase 6**.

### Phase 8 — Close-out
1. Verify **every acceptance criterion** explicitly (list each with pass/fail).
2. All tracker rows ✅ or ❌ (❌ = intentionally skipped, with a note); metadata **Status → ✅ Fully Implemented**, set implementation dates.
3. Complete the docs rows (README/AGENTS/CLAUDE updates the change requires).
4. Final commit.
5. **Retrospective:** if you learned a durable quirk (a flaky command, a detection gap, a better default), fold it back into `.sdlc/profile.yml` or propose a SKILL/rubric edit — the harness should get sharper each run.
6. Print a summary: files changed, tests added, final coverage %, review-score history, criteria status.

### Phase 9 — Deploy (optional)
Only if `profile.deploy_command` is set **and** the user explicitly approves now. Any deploy that could incur cost requires a **fresh** approval every time — never reuse an earlier yes.

---

## Abort path (any gate)
If the user aborts: mark remaining rows ❌ Skipped with a reason, set plan **Status** to ⚠️ Partially Implemented (or 🔴 if nothing landed), and leave the working tree clean — the `sdlc/<slug>` branch is left in place (or reset to baseline on request).

## Non-negotiables
- **Gates A and B always pause.** No exceptions, no self-approval.
- **The plan-doc tracker is the source of truth** and is kept current in real time, not batched at the end.
- **Never run a cost-incurring action** (paid API, cloud deploy) without a fresh, explicit yes.
- **Stay provider- and tool-agnostic:** read commands from the profile; adapt mechanisms via the capability table; put nothing provider-specific in this file.
