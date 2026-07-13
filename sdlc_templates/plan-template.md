# Plan: <SHORT TASK DESCRIPTION>

<!-- Per-run plan + tracker for one SDLC harness run. The tracker is the durable,
     resumable state — keep it current in real time, never batched at the end. -->

## Plan Metadata

| Field | Value |
|-------|-------|
| Created | YYYY-MM-DD |
| Last Updated | YYYY-MM-DD |
| Status | 🔴 Not Started \| 🔄 In Progress \| ⚠️ Partially Implemented \| ✅ Fully Implemented |
| Phase | 0 Intake \| 1 Requirements \| 2 Planning \| 3 Implementation \| 4 Review \| 5 Triage \| 6 Fix \| 7 Test Gate \| 8 Close-out \| 9 Deploy |
| Branch | sdlc/<slug> |
| Baseline | <N passed> at Phase 0 |
| Implemented (partial) | — |
| Implemented (full) | — |

## Implementation Tracker

| # | Item | Priority | Depends-on | Status |
|---|------|----------|-----------|--------|
| 1 | <deliverable> | 🔴 Critical | — | ⏳ Pending |
| 2 | <deliverable> | 🟡 Medium | 1 | ⏳ Pending |
| 3 | <tests for the above> | 🔴 Critical | 1,2 | ⏳ Pending |
| 4 | <docs update> | 📝 Docs | — | ⏳ Pending |

**Status key:** ⏳ Pending · 🔄 In Progress · ✅ Done · ❌ Skipped
**Priority key:** 🔴 Critical · 🟠 High · 🟡 Medium · 🟢 Low · 📝 Docs

> Review findings (Phase 5) are inserted at the TOP of this table with a priority and ⏳ Pending.

## Context

<Why this change: the problem, what triggered it, the intended outcome.>

## Acceptance Criteria (EARS)

1. WHEN <trigger> THE SYSTEM SHALL <response>.
2. WHEN <trigger> THE SYSTEM SHALL <response>.

<!-- Every criterion must map to ≥1 tracker row and ≥1 planned test (Phase 2 consistency check).
     Every criterion is verified explicitly at close-out (Phase 8). -->

## Design

<Chosen approach and the one-line reason it beat the alternatives. New dependencies + SPDX license (prefer none). Performance impact.>

## Test Plan

<Which tests are added/changed and how they hit ≥ coverage_threshold on the changed files.
 Name the success paths, failure/disabled paths, and edge cases covered.>

## Definition of Done

- [ ] Feature/fix works; acceptance criteria all pass
- [ ] Tests written and green; full suite pass count ≥ baseline, 0 failed
- [ ] Coverage ≥ threshold on changed files
- [ ] Config externalised where the repo requires it; no stray magic values
- [ ] Docs updated (README / AGENTS / CLAUDE as applicable)

## Code Review Log

<!-- Phase 4 appends one block per iteration here. -->

## Verification

<Numbered steps to confirm the change end-to-end: commands to run, expected output.>
