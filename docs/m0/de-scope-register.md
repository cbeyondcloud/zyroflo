# De-scope register (M0, tracker item 8)

**Status:** committed at M0 · **Scope:** v1 of the SDLC harness · **Last updated:** 2026-08-05

Everything here is **deliberately not in v1**. Each entry names the trigger that would put it
back on the table.

The register exists because the expensive failure mode is not "we left something out" — it is
**"we left something out and nobody wrote down why, so it got rebuilt from scratch as an
emergency six months later, or argued about every sprint."** An entry here is a decision with
an expiry condition, not a wish list. If a trigger fires, the item is re-scoped through the
normal gates; it does not sneak back in because someone had a free afternoon.

A companion rule: **adding to this register is cheaper than adding to the tracker.** When
scope pressure appears mid-milestone, the default answer is an entry here with a trigger.

---

## Product surface

| # | Deliberately NOT in v1 | Why | Re-add trigger |
|---|---|---|---|
| D-01 | **Hosted web approvals dashboard** | v1 is local-first by design; a server is a different operational and security posture, and all access already flows through CLIs/MCP so hosting is a config swap rather than a redesign | Pilot succeeds **and** more than one team adopts |
| D-02 | **Jira / GitHub / monitoring intake connectors** beyond an interface + file-drop stub | Intake is a configurable path; connectors are integration surface, not lifecycle logic | The pilot team intakes from Jira or GitHub **daily** |
| D-03 | **Multi-tenant hosted operation** | The remaining half of the rev-4 convergence de-scope. Single-repo/estate convergence is **done**; running the harness *for* other people is not | An external team asks to run it without installing it |
| D-04 | **Productivity dashboard beyond static HTML** (Grafana/Datadog live) | `metrics.json` has a stable, versioned schema, so a live backend consumes it unchanged | Org-level rollout past the pilot |

## Lifecycle & quality

| # | Deliberately NOT in v1 | Why | Re-add trigger |
|---|---|---|---|
| D-05 | **Mutation-adequacy gates** (pitest, stryker-js) | Slow, and coverage + baseline-relative gating is not yet exhausted as a signal | Defect-flow regression quality plateaus; then nightly-only, post-M6 |
| D-06 | **Learning-loop auto-apply** | **Never in v1, by design.** Retros *propose* diffs; humans approve them through G3. A harness that silently rewrites its own rules cannot be audited | None. This is a design invariant, not a deferral — removing it would break the halt rule |
| D-07 | **Cucumber/Gherkin execution** of intake scenarios | The intake agent already offers drafted Given/When/Then for human confirmation at G1; *executing* them is a separate test-runner integration | A team authors executable scenarios and asks the harness to run them |
| D-08 | **Cross-component code refactoring driven by the graph** (rename a symbol across every component at once) | The graph can *find* the sites; executing a workspace-wide refactor is a different risk class — one bad edit lands everywhere simultaneously | M6 ships **and** a real cross-component rename is requested |
| D-09 | **`act` CI parity as authoritative evidence** | The M0 workspace check confirmed GH Actions in a majority of components, so `act` is *applicable* — but local execution diverges from hosted runners in ways that produce false reds | Never authoritative. Advisory only; real CI at P8 stays the source of truth |

## Technical / infrastructure

| # | Deliberately NOT in v1 | Why | Re-add trigger |
|---|---|---|---|
| D-10 | **chroma / embedding-based correction clustering** | At v1 feedback volume, lexical grouping by rule-id, file path and finding category finds the same clusters. The `group_corrections()` seam is the drop-in point | Feedback volume outgrows lexical grouping (measured, not assumed) |
| D-11 | **ragas / LLM-as-judge in the validation path** | Pack precision/recall is computed **deterministically** against corpus ground truth — reproducible and free. An LLM judge in the validation path makes the falsifiability instrument itself non-reproducible | Qualitative spot audits only; never a gate |
| D-12 | **OPA / Rego as the gate engine** | Gate checks are file and git assertions. A second language plus a Go binary on the most safety-critical path raises the audit bar for a Python team | Org scale, or a compliance requirement naming Rego |
| D-13 | **Non-git VCS** (Perforce, SVN, Mercurial, jj) | Out of scope. The `components[].vcs` command-override seam (`commit_command`, `push_command`, `deny_raw_git`) already ships and is the hook | An estate that isn't git |
| D-14 | **Runners beyond Claude Code, codex, goose** | Three runners across two instruction standards (AGENTS.md, MCP) is enough to prove provider-agnosticism | A team standardizes on a fourth provider |
| D-15 | **Per-provider tool targets beyond the 6 the installer fans out to** (claude, cursor, windsurf, copilot, gemini, aider) | Adding a target is a compile step, not a port | A team standardizes on something outside that set |
| D-16 | **Endpoints registered in a runtime datastore rather than in git** | No amount of static analysis reaches a fact that is not in the repo. Recorded as a `KnownGap` with a JSON drop-in hook so the gap is *visible*, not silently missing | An export of that datastore becomes available |
| D-17 | **Graphify's extraction core** (vendoring, not the convention) | Commodity intra-repo symbols with no extensibility API. A second extraction core is an accuracy risk with no upside; in a config-driven workspace the impact truth lives in the config seams and no commodity symbol extractor represents it. The `EXTRACTED/INFERRED/AMBIGUOUS` taxonomy **is** borrowed — as a convention, zero code | None foreseen. Revisit only if it grows a custom-node API |
| D-18 | **A second LLM-calling path** (pr-agent's litellm layer, `instructor`) | The tools never call LLMs — the *runner* does. That is precisely what keeps the harness provider-agnostic; a tool that calls an LLM directly re-couples it | None. Design invariant |

---

| D-19 | **Retroactive de-identification beyond this repository** | Item 73 rewrote this repo's history (2026-08-06) to strip a private workspace inventory and redact machine paths from historical blob content. It cannot reach a clone taken before the force-push. Judged proportionate rather than ignored: the exposed content was repository metadata for repos that are themselves public — names, remotes, commit SHAs — not credentials, and the window was 9 commits on a private repo. **Recorded as an accepted residual, not a deferral: there is no trigger and no future work that fixes it.** The forward-looking control is the item-70 guard, which prevents the next one | None. Accepted |
| D-20 | **Audit fidelity of the rewritten history** | The redaction pass replaced literal strings inside historical files, so old commits now show text that was never committed. Redaction and audit fidelity are in genuine tension and this resolves toward redaction. The unmodified history is preserved in a local mirror named in `guard-validation.md`, so the tension is documented and the original is recoverable — by the author, deliberately not by everyone | None. Accepted |

## Rejected outright (not deferred)

These are not on any trigger. Recording them prevents re-litigation:

- **ruflo/claude-flow** — independently documented fabricated benchmark claims.
- **Kuzu** — archived October 2025.
- **gemini-cli** — sunset June 2026.
- **Temporal / Windmill / n8n / Argo / Prefect** — server + DB infrastructure violates local-first.
- **CrewAI / Google ADK / OpenAI Agents SDK / MS agent-framework** — either own the agent runtime (breaks provider-agnosticism) or persist state as pickle/binary (breaks git-auditability).
- **mem0 / zep** — unauditable external memory.
- **guardrails-ai / NeMo Guardrails** — wrong layer; our guardrails are file/git assertions.
- **semgrep** — LGPL engine, Windows friction, non-OSI registry → `ast-grep` instead.
- **OpenRewrite recipes** — source-available license.
- **HumanLayer** — deprecated.
- **MetaGPT / ChatDev / gpt-pilot** — stale, toy, or FSL-licensed.

## How to use this register

- **Adding an entry** is a normal edit; say why and name a trigger. An entry with no trigger
  is not a de-scope, it is an omission.
- **A fired trigger** does not auto-admit the item. It moves it to the tracker as a proposed
  item, through the same gates as anything else.
- **Review cadence:** at each milestone kill gate, scan for fired triggers. Cheap, and it is
  the only thing that stops the register from becoming a graveyard.
