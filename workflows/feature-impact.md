# feature-impact — find where and how to implement a feature

## Purpose

Turn a feature ask, phrased in domain words, into an implementation map read
off the graph: which components own the seams the feature crosses, which
contracts (endpoints, topics, tables) it will touch, which files declare them,
and which tests cover the ground — every claim carrying `source_file:line`
provenance instead of folklore.

## Inputs

A feature description with its domain nouns extracted: the entities, events,
endpoints, or settings the ask is about.

## Access tiers

MCP tools (`sdlc-graph` server) first; Python CLIs as the runner-agnostic
fallback:

```
uv run --project <harness> python <harness>/tools/graph/<tool>.py ...
```

## Steps

1. **Fresh graph.** The `graph-build` workflow's freshness step; exploration
   over a stale graph finds seams that may have moved.
2. **Hunt the vocabulary.** MCP `search_nodes` for each domain noun —
   case-insensitive substring over node ids and file-backed node paths,
   `node_type` to narrow (topic, ep, table, cfg, svc). Matches are the
   candidate seams. Source files are cited on edges rather than stored as
   nodes, so a file question goes to `impact`, not search.
3. **Walk the seams.** MCP `node_neighbors` on each candidate: who produces,
   consumes, writes, exposes — every edge with its declaring file and line,
   confidence, and canonicality; truncation is counted, never silent. Two
   hops of this usually separates the owning component from the audience.
4. **Measure the ground.** MCP `impact` (or CLI `impact.py --graph-dir
   <graph-dir> --seed <alias>/<relpath>`) on the files the neighbors cite as
   declaration sites: the ranked radius says how loud a change at that seam
   is, and rows flagged generated redirect the edit to `regenerate_from`.
5. **Pack the chosen seed.** MCP `context_pack` (or `pack.py --graph-dir
   <graph-dir> --seed <alias>/<relpath> --out <dir>`) on the file you propose
   to change: its Contracts section is the interface story, Tests
   to run-or-edit is the coverage story, Config coupling is the deployment
   story — the implementation map in one page.
6. **Write the proposal with provenance.** Components and files ranked, the
   contracts touched with their `declared_at` citations, the tests to extend,
   and — explicitly — which parts of the ask the graph could NOT locate (see
   the limits below), so the reader knows which claims are evidence and which
   are judgment.

## Verify

- Every "implement it here" claim cites a declaration site the graph knows.
- Every contract named in the proposal appears in the pack's Contracts section
  with a citation.
- The limits paragraph is present when any domain noun found nothing.

## Edge Cases / Notes

- **The semantic layer is item 79 and has not landed.** The graph cannot yet
  mark entry points (every controller looks alike — nothing says which is the
  front door), and it cannot join two components naming one business entity
  with no config seam between them (there is nothing to extract). Proposals
  must say so where it bites, not paper over it.
- Search is lexical: a synonym or a renamed concept misses. An empty search
  result means "this graph has no node matching that string", never "the
  concept does not exist in the workspace".
- A noun that matches only cfg nodes is a settings-shaped feature; its radius
  runs through config consumers, and the seams are the files `node_neighbors`
  cites, not the config file itself.
