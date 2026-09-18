# Bootstrapping a workspace the harness has never seen

*Item 56. The route from "here is a repository" to "a run can be opened against it",
written down because every step below has surprised somebody — usually the author of
this file, usually by being skipped.*

This is the **new-estate** guide: one repository or many, one stack or several. There
is no separate multi-repo product. Scope and substrate are capabilities, so a lone
repository with no dependencies and a workspace with a graph run the same lifecycle
and differ only in which capabilities are present.

---

## 0 · Before anything is installed

**The harness refuses more than it fixes, on purpose, and two of its refusals happen
here.** Both are cheaper now than after a run has started, because a run's gates pin
artifacts that name the model of the repository.

**A repository built from several units will not install as one component.** If
there are build manifests *below* the root and none *at* it, `sdlc-init.py` exits 1
and names the directories it found. A root manifest means the repository builds as a
whole however many sit below it, and that installs normally. If the refusal is wrong
for your repository — a single build whose modules happen to carry manifests —
`--single-component` asserts it, and the assertion is **recorded in the profile**, so
a run that later turns out to be repository-wide is traceable to somebody having said
so.

**A workspace of many repositories is described by a file you fill in**, not by
scanning a directory and guessing. Copy `sdlc_templates/workspace.example.toml`,
fill it, and pass it:

```bash
uv run python sdlc-init.py --target /path/to/repo --workspace sdlc-workspace.toml
```

The workspace file supplies **which components exist**; `--target` still says where
the harness config is written. If you plan to use the scheduled jobs
(`--snapshot-cadence`, `--corpus-replay`), the workspace file must live
**inside** the repository the job checks out -- place it at the top of
`--target` rather than beside it. The scheduled workflows check out only the
repository, so a workspace file outside it names a path no runner can open; the
installer refuses to write those workflows for such a layout.

---

## 1 · Install

```bash
uv run python sdlc-init.py --target /path/to/repo --tools auto
```

`--tools auto` detects which AI coding tools the repository is set up for and writes
a surface for each. Nothing about the lifecycle depends on which one you use: the
tool-specific files are pointers to the same SOPs.

Exit codes matter here, and `0` is not the only good one — read what it prints. An
install can succeed while the graph is honestly absent, which is a fact about your
stack rather than a failure. **A repository whose language has no extractor yet gets
a scaffold and no graph, and says so**; the phases that need a graph (P2, P6, P9)
report themselves as capabilities this workspace does not have, rather than
pretending.

Add `--claude-extras` for the Claude Code layer (reviewer subagents, a scoped
permission allowlist, hooks). Add `--estate` with `--workspace` to verify the
vendored binaries, report Neo4j readiness, and chain the workspace graph build.

---

## 2 · Fill in the profile

`.sdlc/profile.yml` is written with your components in it and **most of its dials
blank**. Blank is a legal state and the harness says so rather than inventing a
value — but a blank dial is a phase that cannot run.

The ones worth filling on day one, because a gate asks for each:

| Key | What it turns on |
|---|---|
| `test_command`, `build_command` | P5 can judge a suite against the baseline |
| `coverage_command`, `coverage_threshold` | P5's coverage gate |
| `known_failures` | a red baseline is judgeable at all — P5 **refuses** a red baseline with an empty quarantine, because "no NEW failures" cannot be judged against a failure set nobody wrote down |
| `deploy_command` | P9 |
| `probes` | P10 |
| `gate_profile` | which gates halt (`solo`, `team`, `regulated`) |

Nothing here may be **looser** than `.sdlc/policy.yml`, which is the constitution:
the loosest each dial is ever allowed to be. A profile that loosens one is refused by
name at approval time, because a control that disappears without anybody deciding to
remove it is the failure this floor exists for.

---

## 3 · Take one small run end to end before trusting any of it

Not the interesting change. A small one, where you already know the answer.

The point is not the change; it is that **you find out here** which of your dials are
blank, which of your commands need a different working directory, and which gate
stops you. Every field defect this project has recorded was found by running
something, and several of them had been passing a suite of thousands of tests.

---

## 4 · Wire the outside world in — connectors

An external tracker or an alert manager can open runs, through the same door
everything else uses.

**A connector is a mapping you write, not an integration somebody shipped.** The
harness does not know what your tracker's fields are called and does not learn: it
reads a small JSON file that says *in this payload the title is at
`fields.summary`*, and every source is then the same code path with a different
declaration. There is no vendor name anywhere in `connect.py`, which is what makes a
tracker nobody here has heard of exactly as supported as the three with stubs.

Copy the closest stub and edit it:

```bash
mkdir -p /path/to/repo/.sdlc/connectors
cp sdlc_templates/connectors/jira.json /path/to/repo/.sdlc/connectors/jira.json
```

Stubs shipped: `jira.json`, `github-issue.json`, `alert.json`. They are **not**
installed into your repository by default, deliberately — a config file installed
everywhere and used nowhere is dead config, and this project keeps finding that
exact class.

Then look before you file:

```bash
uv run python tools/sdlc/connect.py --target /path/to/repo --source jira --payload issue.json
```

Without `--file` **nothing is written**. It tells you the title it read and whether a
run is already open for it. Add `--file` to open one:

```bash
uv run python tools/sdlc/connect.py --target /path/to/repo --source jira --payload issue.json --file
```

Two properties worth knowing before you point a webhook at this:

- **The same item filed twice is one run.** The specification is deterministic — no
  clock, no run id — so the identity is stable and `--file` on an item already open
  reports it and writes nothing.
- **A run that CLOSED does not hold the item filed.** If the thing is still true
  after somebody's attempt went away, it outlived the attempt and files again.

---

## 5 · Turn on the recurring jobs, once there is something for them to check

Both are opt-in and neither is written unless you ask, for the same reason: a
scheduled job on a repository that cannot answer its question reddens forever, and a
recurring red is how a real check gets switched off.

- `--corpus-replay` — the nightly impact replay. Needs a harvested corpus.
- `--snapshot-cadence` — the weekly graph-snapshot publish. Needs a graph.

---

## 6 · What you do NOT have to decide

- **Which AI tool.** Six fan-outs, one lifecycle.
- **Which build system.** The profile names commands; nothing here knows an
  ecosystem.
- **Whether to adopt every phase.** `phases[]` says which run at all, and a phase
  whose capability is absent reports itself as absent.
- **Whether to use the graph.** Three access tiers, widest last: MCP tools, the
  Python CLIs, and an exported bundle any tool that can open a file can read.

---

## The one thing to carry into the first week

**An empty result from an external tool is not evidence of absence.** It has produced
two confidently wrong answers inside this harness and several more in the field. When
a gate reports nothing, check that it looked before believing it found nothing — that
distinction is built into every status the harness reports, and it is worth adopting
as a habit rather than only as a mechanism.
