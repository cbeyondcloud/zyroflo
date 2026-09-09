---
name: sdlc-graph
description: Query this repository's committed dependency graph before scoping, reviewing, or planning a change — impact of a diff, blast radius of a file, context packs with source_file:line provenance, freshness checks. Use when reviewing a diff or PR, scoping a feature, or asking what a change touches beyond the changed files.
---

# Dependency graph (sdlc-graph)

This repository carries an extracted dependency graph at `@@GRAPH_DIR@@`
(relative to the repository root): `graph.jsonl` holds the facts, and
`build_report.json` states how they were built. Every fact carries
`source_file:line` provenance — a claim without a citation did not come from
the graph.

## Running the commands below

Every `tools/...` path in this file lives under `@@HARNESS@@` — **never under
the repository being worked on** — and is run through this prefix:

```
uv run --project "@@HARNESS@@" python "@@HARNESS@@/<path>" ...
```

The commands below are already written out that way. Run them **from the
repository root**: `--project` pins uv to the harness's own locked environment,
the quotes survive a harness path with a space, and `@@GRAPH_DIR@@` is relative
to the repository, so a command run from anywhere else is refused.

## The workflows

Follow these SOPs from the harness clone rather than improvising:

- `@@HARNESS@@/workflows/graph-build.md` — build or refresh the graph,
  check freshness, publish the snapshot baseline.
- `@@HARNESS@@/workflows/impact-review.md` — review a diff with the
  dependency picture first; the review SOP the M2 gate dogfooded.
- `@@HARNESS@@/workflows/feature-impact.md` — scope a feature proposal
  through the graph's evidence.

## Two access tiers

**MCP** — tools that read `.mcp.json` get the `sdlc-graph` server (registered
by the installer once a graph exists): `graph_status`, `impact`,
`impact_diff`, `context_pack`, `search_nodes`, `node_neighbors`,
`validate_graph`, `check_fresh`.

**CLI (always works, any runner)** — the same functions, **run from the
repository root**: `@@GRAPH_DIR@@` is relative to it, so these commands resolve
the graph against the current directory and will refuse from anywhere else. The
harness path is quoted because a clone can live under a path with a space.

```
uv run --project "@@HARNESS@@" python "@@HARNESS@@/tools/graph/impact.py" --graph-dir @@GRAPH_DIR@@ --seed <component>/<relpath>
uv run --project "@@HARNESS@@" python "@@HARNESS@@/tools/graph/pack.py"   --graph-dir @@GRAPH_DIR@@ --diff <file> --component <alias> --out <dir>
uv run --project "@@HARNESS@@" python "@@HARNESS@@/tools/graph/build.py"  --check-fresh --out @@GRAPH_DIR@@
```

## Doctrine (the graph's honesty rules)

- **Freshness first**: check `check_fresh` before trusting the graph; review
  over a stale graph only with the staleness stated in the findings.
- **Seeds have modes**: `hunk` (facts at the changed lines), `provenance`
  (the file's facts), `none` — a `none` file contributed no graph evidence,
  so claims about its radius are code-reading, not graph.
- **Generated rows say "regenerate, don't edit"** and name their source; a
  finding on a generated file is a finding about its `regenerate_from`.
- **Absence is stated, never silent**: an empty section is a stated limit of
  extraction, not proof of no impact.

Paths above were written by `sdlc-init.py` at install time; if the harness
clone moves, re-run the installer to refresh them.
