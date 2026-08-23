# P3 · design — the change set, the test plan, and the decisions worth a record

**Phase** `P3` · **produces** `30-design.md` · **gate** `G2` · **active** always

## Purpose

Design is where the change stops being a description and becomes a list of
files. It is also the last phase where changing your mind is cheap.

Two things come out of it that nothing else produces: a plan a *different*
agent could execute, and a written record of the decisions that will look
arbitrary in six months.

## Inputs

```
stage.py --target <repo> --run <id> --phase P3 --prompt
```

Plus `10-requirements.md`, and `20-scope.md` where scoping ran.

## Steps

1. **A change set per component.** Which files, what changes in them, in what
   order. Where the workspace has more than one component, one section each —
   P4 runs one agent per component and each agent reads only its own section.
2. **The test plan**, targeting `coverage_threshold` on changed files. Name the
   tests, not the intention: "a test that a malformed header is refused with
   its line number" is a plan; "add tests" is not.
3. **Write an ADR when a trigger fires** — a dependency added or removed, a
   service boundary moved, a data model changed. One file per decision,
   `30-adr-<slug>.md`, stating the alternatives that were rejected and why.
   **Do not write an empty one to satisfy a checklist**: an ADR corpus with
   filler in it teaches people to skip the real ones.
4. **Consistency check, and do not skip it.** Every acceptance criterion maps
   to at least one change-set item *and* at least one planned test. Every
   change-set item traces back to a criterion. An orphan on either side is a
   defect in the design, found here for free.

## What this phase owes

`30-design.md`, plus `30-adr-*.md` for each triggered decision. **G2 stands at
the end of this phase.** Write the design, stop, and wait — implementation
before the design gate is the thing the gate exists to prevent.

```
state.py --target <repo> --record <id> --artifact 30-design.md
```
