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
tools/sdlc/stage.py --target <repo> --run <id> --phase P3 --prompt
```

Plus `10-requirements.md`, and `20-scope.md` where scoping ran.

## Running the commands below

Every `tools/...` path in this document lives under `<harness>` — **never under
the repository being worked on** — and is run through this prefix:

```
uv run --project "<harness>" python "<harness>/<path>" ...
```

So step 1's `tools/sdlc/stage.py` is:

```
uv run --project "<harness>" python "<harness>/tools/sdlc/stage.py" --target <repo> --run <id> --phase <P> --prompt
```

`<harness>` is stated in the prompt as `harness:`. Do not run a step's path
relative to the repository: the file is not there, and **the error that
produces has sent a runner improvising** — on walk 4 a runner ran a bare
`state.py`, got *is not recognized as an internal or external command*, and
stopped with the document it had just written left unpinned. `--project` is
load-bearing rather than decorative: a bare `uv run` resolves the PASTER's
project, which the installer records as a degraded run at exit 0, or a
stranger's repository quietly gaining a `.venv`.

## Steps

1. **A change set per component**, under a heading or label that says
   **`Change set`**. Which files, what changes in them, in what order. Where the
   workspace has more than one component, one such section each — P4 runs one
   agent per component and each agent reads only its own section.

   **Name every file in backticks, workspace-relative** (`orders-api/pom.xml`,
   not "the api's pom"), and name them as a **list** under the declaration:

   ```
   ## Change set — orders-api

   - `orders-api/ShortenController.java` — stop logging inbound headers
   - `orders-api/ShortenControllerTest.java` — the regression test
   ```

   **Only those list entries are read as the change set**, which is what lets
   the rest of this document cite `10-requirements.md`, a base commit or its own
   run id in the ordinary backticked style without any of them being taken for
   files you intend to change. Prose inside the section is prose; the section
   ends at the next heading.

   Step 3's check reads the change set out of this document, and a file it
   cannot see declares nothing — **a design that declares no change set at all
   is reported rather than passed**, because "no decision worth recording" out
   of a document that named nothing is a clean bill of health from a check that
   looked at nothing.
2. **The test plan**, targeting `coverage_threshold` on changed files. Name the
   tests, not the intention: "a test that a malformed header is refused with
   its line number" is a plan; "add tests" is not.
3. **Ask what fired, then write the ADR.** The trigger is not your reading of
   your own design:

   ```
   tools/sdlc/adr.py --target <repo> --run <id> --check
   ```

   It resolves every file step 1 named, looks up what that file *declares* in
   the graph, and fires on the evidence: a **dependency** (the file declares
   what this component builds against), a **boundary** (it declares a call, a
   topic, an exposed endpoint or a spec another component reaches across), or a
   **data model** (it declares a table's shape or its ownership). Each trigger
   is printed with the `source_file:line` behind it.

   One file per decision, `30-adr-<slug>.md`, stating the alternatives that
   were rejected and why. Two lines are load-bearing and the check reads them:

   ```
   **Answers** `boundary:orders-api/src/main/java/OrderController.java`
   **Rejected** versioning the path instead — every consumer would pin the old one
   ```

   One ADR may answer several triggers; list them all. **Do not write an empty
   one to satisfy a checklist**: an ADR corpus with filler in it teaches people
   to skip the real ones. A trigger that genuinely deserves no record is
   *refused*, with a reason and a name against it —

   ```
   tools/sdlc/adr.py --target <repo> --run <id> --dismiss <trigger-id> --because "<reason>"
   ```

   — and a refusal is a decision. Silence is not: a trigger nothing answers is
   work this phase still owes, and **G2 has nothing to stand on until it is
   closed one way or the other**.
4. **Consistency check, and do not skip it.** Every acceptance criterion maps
   to at least one change-set item *and* at least one planned test. Every
   change-set item traces back to a criterion. An orphan on either side is a
   defect in the design, found here for free.

## What this phase owes

`30-design.md`, plus `30-adr-*.md` answering every trigger step 3 fired (or a
recorded dismissal for it). **G2 stands at the end of this phase.** Write the design, stop, and wait — implementation
before the design gate is the thing the gate exists to prevent.

```
tools/sdlc/state.py --target <repo> --record <id> --artifact 30-design.md
```
