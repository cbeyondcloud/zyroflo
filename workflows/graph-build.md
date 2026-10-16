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
   deduped node/edge counts, traversal-rules source, unresolved rollup, and
   whether the build was clean (`clean_build`: the zero-silent-drops verdict
   step 5 grades by, each problem it found named). The component names listed
   are the aliases the other tools expect; module-scoped aliases beyond them
   are counted, and a wrong alias is refused with the complete list.
2. **Publish the baseline, once.** `build.py --target <repo> --publish-snapshot`
   (or `build.py --workspace <toml> --out <graph-dir> --publish-snapshot`)
   writes per-component shards + stamps — the committed team baseline that
   `check_fresh` compares against, `--incremental` reuses and `--from-snapshot`
   reloads; commit it with the tree. Until it exists, step 3 has nothing to
   compare against. Once a baseline is committed, start at step 3.
   `--check-snapshot` verifies a committed snapshot reproduces the source build.
3. **Check freshness.** MCP `check_fresh`, or CLI `build.py --target <repo>
   --check-fresh` (or `--workspace <toml>` in place of `--target <repo>`).
   Content decides, not commits: it hashes what the extractors would read - the
   harness's own extraction code and the files each shard read in other
   components included - and compares against the committed shards' stamps.
   Per component: fresh; stale with the reason - its own files, the extractors
   or their code (every shard is stale once after a harness upgrade that touches
   the extraction path: re-publish once), the workspace config, a parser or its
   version, or what it read from another component (files there that changed
   since its publish, a reader no longer or newly active there); or REFUSED, a
   shard it cannot trust, with its remedy. A file of the extraction code that
   cannot be read, or is missing, makes every shard that reaches the code's
   check stale, its line naming the file and the way on in parentheses (make it
   readable, or restore it); the publish refuses until it reads. `build.py`
   itself is the exception: run as a script while it cannot be read, it answers
   exit 0 with nothing printed, and missing, "can't open file" - run the module
   form, `python -m tools.graph.build`, which cached bytecode serves, to get
   the answer. A NOTE, at
   exit 0 as at exit 2, names a deployment reader whose shard says it had no
   parser and that still has none, with the parser its rows name, and ends with
   the way on. Where every row names a parser: install the parser each
   `grammar-unavailable` row names, then re-publish the shards it names. Where
   a row names no parser its reader declares, there is none to install, and
   whether one is installed cannot be asked: name, in each
   `grammar-unavailable` row that names none, a parser its reader declares,
   then re-publish the shards it names. A reader with rows of both kinds is
   given both. MCP `check_fresh`'s result carries, per reader, its components
   (`unparsed`) and the parsers its rows name (`unparsed_parsers`, `""` for a
   row naming none it declares), but one way on for the whole set
   (`unparsed_way`), joining both ways when the set needs both.
   `no freshness stamps` means there is no baseline yet (step 2), not that the
   tree moved.
4. **Rebuild when stale, and re-publish.** `build.py --target <repo>
   --incremental --publish-snapshot` (or `--workspace <toml> --out <graph-dir>`
   in place of `--target <repo>`). The publish is what moves the stamps: a
   rebuild without it refreshes the served graph and leaves `check_fresh`
   answering stale. `--incremental` rebuilds only the components whose own
   inputs, parser versions, or inputs read from another component moved — its
   output is byte-identical to a full build, so reach for it first; a change to
   the extractors or their code (a harness upgrade), or to the workspace config,
   rebuilds every component on its own. `--only` / `--components` narrow a
   deliberate build; never use them to "freshen" — freshness is whole-graph.
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
