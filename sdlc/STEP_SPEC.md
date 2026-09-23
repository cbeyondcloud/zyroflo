# Step spec — one format for a built-in step and a custom one

A step is a Markdown file. Its **front-matter is the contract** — what the step
reads, what it owes, whether a human stops the run after it — and its **body is
the SOP**, the procedure a runner follows. The twelve built-in steps under
`sdlc/stages/` are written in this format, and a test holds each one's
front-matter equal to the harness's stage table, so a custom step is not a
second-class path.

> **Status: the format and its parser ship; loading does not.** `stage.parse_step_spec`
> reads and validates a spec, and a contract test proves a step parsed from one
> flows through the pipeline with no special-casing. Nothing yet LOADS a custom
> step from an adopter's repository — that is a future backlog row. Writing a
> spec today documents a step; it does not add one to a run.

## The front-matter

Between two `---` lines, one `key: value` per line. A list is `[a, b]`. A line
starting with `#` is a comment. An unknown key is refused by name, with a
spelling hint — an ignored key is a request that silently did not happen.

```
---
id: lint-report
name: lint-report
after: test
inputs: [50-tests.md]
outputs: [55-lint.md]
conditional: []
evidence: []
after_gate: []
needs: none
gate: hard
---
# lint-report — run the linters and record what they said

## Purpose
...
## Steps
...
```

| Key | Required | Meaning |
|---|---|---|
| `id` | yes | The step's id. A custom step: lower-case words joined by hyphens. `P<n>` is reserved for the built-ins. |
| `name` | yes | The name a profile uses — in `phases:`, `phase_runners:`, `models:`, `extra_gates:`. Unique. |
| `after` | yes (custom) | Which existing step this one FOLLOWS. The built-in order is fixed by the spec; a custom step is spliced in, never reordered around. `none` is the first built-in's alone. |
| `inputs` | no | Documents the step reads. Each must be written by a step at or before `after` — a step can only read what the pipeline has already produced. |
| `outputs` | yes | Artifacts the step owes, relative to the run folder. At least one: a step owing nothing is a step no gate can judge. |
| `conditional` | no | Artifacts owed only when a trigger fires (an ADR when a boundary changes). |
| `evidence` | no | Records the step's engine writes as proof of work (a suite result, a pre-pass). |
| `after_gate` | no | Artifacts written only AFTER the step's gate is approved (P9's deploy result). |
| `needs` | no | A capability the workspace must have for the step to run: `none`, `graph`, `deploy`, `probes`. A step whose capability is absent is reported skipped-for-cause, never silently dropped. |
| `gate` | no | `none`, or `hard`: a human stop at the end of the step, named `G-<name>` — the same id an `extra_gates:` stop gets, approved the same way with `approve.py --gate G-<name>`. The numbered gates `G1`–`G5` belong to the built-ins. |

## What the parser refuses

Every refusal names the line or the key, so the author fixes the spec rather
than guessing: no opening or closing `---`; a line that is not `key: value`; an
unknown or repeated key; a missing `id`, `name` or `outputs`; a custom `id` in the
reserved `P<n>` form; a name or id that already exists; `after` naming no step;
an input no earlier step writes; a `needs` that is not a capability; a gate other
than `none`/`hard` on a custom step.

## What the format deliberately does not carry

**Prompt values** — the exact-value block a runner is handed (`run`, `target`,
`test-command`, …). The harness computes them, so they stay code; a spec that
declared them would be a second statement of something only the harness knows.
**Ordering among custom steps** beyond `after:`, and **reordering the built-ins**:
their `reads`/`produces` dependencies are what make reordering unsafe.
