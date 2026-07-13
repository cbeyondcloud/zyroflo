# Code Review Rubric (generic)

Used by **Phase 4** of the SDLC harness to convert review findings into a reproducible score out of 10. The threshold and iteration cap live in `.sdlc/profile.yml` (`review.review_threshold`, `review.max_iterations`).

Keep scoring **deterministic** so the same diff scores the same across models and sessions: score the categories, then apply the fixed deductions. Do not "feel" a number.

---

## Categories (base weights sum to 10)

| # | Category | Weight | What to check |
|---|----------|-------:|---------------|
| 1 | **Logical correctness** | 2.5 | Does it do what the acceptance criteria say? Edge cases, off-by-one, null/empty, concurrency, and **performance regressions** (blocking calls on hot/async paths, N+1 queries, per-request heavy init). |
| 2 | **Security** | 2.0 | Secrets not hardcoded; input validation; injection (SQL/command/path/template); authz and **isolation/namespacing** (no cross-tenant/user leakage); safe deserialization. |
| 3 | **Tests** | 2.0 | Tests exist for the change and its edge cases; **≥ `coverage_threshold` on changed files**; both success and failure/disabled paths covered; tests assert behavior, not implementation trivia. |
| 4 | **Error handling** | 1.5 | Failures handled or surfaced deliberately; graceful degradation when a dependency is missing/unavailable; no bare excepts that swallow errors; actionable messages. |
| 5 | **Maintainability & style** | 1.0 | Follows the repo's conventions (naming, structure, imports); reuses existing utilities; readable; no dead code; comments where non-obvious. |
| 6 | **Dependency hygiene** | 1.0 | New dependencies justified and minimal; **SPDX license** recorded; nothing pulled in that the repo already solves; no unpinned/abandoned packages. |

An optional **`review.rubric_overlay`** file may append repo-specific categories (its weights are additive; renormalize to 10 by scaling all weights proportionally, or treat overlay items as extra deductions — state which in the overlay file).

---

## Scoring procedure (per iteration)

1. Start each category at its full weight.
2. Subtract within a category for weaknesses you find (partial credit is fine, e.g. tests present but a path uncovered → −0.5 of category 3).
3. Sum categories → **raw score**.
4. Apply **severity deductions** from the findings list (these stack, and apply on top of the raw score):

   | Severity | Deduction each | Meaning |
   |----------|---------------:|---------|
   | 🔴 Critical | caps final score at **8.0** | Data loss, security hole, crash on a normal path, corrupts state |
   | 🟠 High | −0.5 | Wrong result in a plausible case; missing error handling on a real failure |
   | 🟡 Medium | −0.2 | Edge case, minor perf issue, weak test |
   | 🟢 Low | −0.05 | Style, naming, nits |

5. Final score = `min(raw − deductions, 8.0 if any Critical else raw − deductions)`, floored at 0.
6. Record in the plan doc's **Code Review Log**: iteration number, final score, and every finding with severity + file:line + one-line fix.

**A Critical finding cannot pass**, regardless of raw score — it caps at 8.0, below any sane threshold. Fix Criticals first.

---

## Reviewer personas (apply per category for depth)

- **Security reviewer** → category 2 (and secrets/isolation anywhere).
- **Architect** → categories 1, 5, 6 (design coherence, reuse, dependency fit).
- **QA engineer** → categories 3, 4 (coverage, failure paths, edge cases).

Reviewing each category "in character" surfaces issues a single generic pass misses.

---

## Output shape (what Phase 4 appends to the plan doc)

```
### Code Review Log

#### Iteration N — score X.X / 10
- 🔴 Critical — path/to/file.py:42 — <defect> → <fix>
- 🟠 High — ... → ...
- 🟡 Medium — ...
Category breakdown: correctness 2.3 / security 2.0 / tests 1.5 / errors 1.5 / style 1.0 / deps 1.0  (raw 9.3, −0.7 deductions = 8.6)
```
