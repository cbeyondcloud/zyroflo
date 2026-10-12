# graph-build — build, refresh, and trust the knowledge graph

## Purpose

A current, validated graph is the precondition for every graph-tier answer: an
impact map, a context pack, or a review computed over a stale or silently
degraded graph is confidently wrong. This workflow ends in one of two honest
states — a graph you may rely on, or a stated reason you may not yet.

## When to run

- After a pull, branch switch, or any edit burst — before `impact-review` or
  `feature-impact`.
- When any freshness check says stale.
- First-time setup is not this workflow: `sdlc-init.py` chains the first build
  and registers the MCP server (`.mcp.json`) when the build produces a graph.
  It does not publish the baseline — step 2 does, once.

## Access tiers

Prefer MCP tools when the `sdlc-graph` server is registered; every step also
names the Python CLI, so any runner can execute this from the CLIs alone. All
CLI commands run from the harness clone's environment:

```
uv run --project <harness> python <harness>/tools/graph/<tool>.py ...
```

`<graph-dir>` is the completed build's output directory — `<repo>/.sdlc/graph`
for a single repository or an estate target.

## Steps

1. **Ask what the graph is.** MCP `graph_status` — run id, declared components,
   deduped node/edge counts, traversal-rules source, unresolved rollup. The
   component names listed are the aliases the other tools expect; module-scoped
   aliases beyond them are counted, and a wrong alias is refused with the
   complete list.
2. **Publish the baseline, once.** `build.py --target <repo> --publish-snapshot`
   (or `build.py --workspace <toml> --out <graph-dir> --publish-snapshot`)
   writes per-component shards + stamps — the committed team baseline that
   `check_fresh` compares against, `--incremental` reuses and `--from-snapshot`
   reloads; commit it with the tree. Until it exists, step 3 has nothing to
   compare against. Once a baseline is committed, start at step 3.
   `--check-snapshot` verifies a committed snapshot reproduces the source build.
3. **Check freshness.** MCP `check_fresh`, or CLI `build.py --target <repo>
   --check-fresh` (or `--workspace <toml>` in place of `--target <repo>`).
   Content decides, not commits: it hashes what the extractors would read and
   compares against the committed shards' stamps. Per component: fresh; stale
   with the reason, a component that depends on one whose inputs moved
   included; or REFUSED, a shard it cannot trust, with its remedy. A NOTE, at
   exit 0, names a deployment reader whose shard says it had no parser and that
   still has none: install the parser, then re-publish. `no freshness stamps`
   means there is no baseline yet (step 2), not that the tree moved.
4. **Rebuild when stale, and re-publish.** `build.py --target <repo>
   --incremental --publish-snapshot` (or `--workspace <toml> --out <graph-dir>`
   in place of `--target <repo>`). The publish is what moves the stamps: a
   rebuild without it refreshes the served graph and leaves `check_fresh`
   answering stale. `--incremental` rebuilds only the components whose inputs,
   parser state or dependencies moved — its output is byte-identical to a full
   build, so reach for it first; a changed extractor set or workspace config
   forces the full path on
   its own. `--only` / `--components` narrow a deliberate build; never use them
   to "freshen" — freshness is whole-graph.
5. **Validate.** MCP `validate_graph`, or CLI `graph/validate.py --graph-dir
   <graph-dir>`. Read the verdict AND the parts: every metric shows
   numerator/denominator, N/A is never graded, and `--sample N` draws a seeded
   spot-audit to eyeball real edges. Default targets are the shipped ones; a
   workspace's recalibrated floors reach the tool only explicitly (`--targets`
   file, or the `targets` argument over MCP) and belong in a dated gate record,
   never silently in code.
6. **Read what the build could not do.** The unresolved rollup (in
   `graph_status` / the validation output) and the build report are the
   zero-silent-drops contract: a reference the extractors could not resolve is
   recorded, never discarded, and a missing library or grammar yields a
   `grammar-unavailable` row per component rather than a silently shorter graph.

## Verify

- Build exit 0 and `status: ok` in the report; an empty build is exit 0 with
  the empty stated loudly — that is extractor coverage, not success theater.
- `check_fresh` answers current (exit 0) after the publish: the committed
  baseline is the tree.
- `graph_status` shows the new run id (the server re-stats per call: a rebuild
  is served without a restart).
- Validation verdict recorded; if MISSED, the parts say which metric and by
  how much — fix or record, never shrug.

## Edge Cases / Notes

- A repository no extractor understands builds EMPTY at exit 0 and says so.
  That is a statement about extractor coverage per stack, not about the repo.
- In a dependency-free environment the yaml/java readers report one
  `grammar-unavailable` row per component and the stdlib extractors still
  produce facts — degraded and saying so, never dead.
- A profile may ship `traversal_rules.yml`; the build copies it beside the
  graph and `graph_status` names the rules source being served.
- `check_fresh` needs a source tree (`--target`/`--workspace`); a graph dir
  alone cannot answer a currency question about a tree it cannot see.
- Freshness is a property of the published baseline, not of the served graph.
  Measured on a fresh one-repo tree (T226, T388): `check_fresh` answers
  `no freshness stamps` (exit 2) until the first publish, and after an edit a
  rebuild without `--publish-snapshot`, full or `--incremental`, still answers
  stale. This SOP once checked freshness before any publish and called the
  publish optional, so an adopter following it in order was refused at the
  check.
