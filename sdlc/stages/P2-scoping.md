# P2 · scoping — the blast radius, with provenance, before the design

**Phase** `P2` · **produces** `20-scope.md` · **gate** `G1` · **active** graph capability

## Purpose

Scoping answers a question nobody can answer by reading the diff: *what else
does this touch*. Contracts other teams consume, files reached through a
configuration seam, the tests that cover the radius — each claim carrying the
`source_file:line` that supports it, so a reviewer can check the claim rather
than trust it.

This phase runs only where a graph exists. Without one the lifecycle skips it
and the agent explores the repository directly, which is weaker and is
supposed to look weaker.

## Inputs

```
stage.py --target <repo> --run <id> --phase P2 --prompt --set component=<alias>
```

## Access tiers

The `sdlc-graph` MCP server where it is registered; the Python CLIs otherwise.
The CLI tier is the one every runner has, so an SOP step is only ever written
against it:

```
uv run --project "<harness>" python "<harness>/tools/graph/<tool>.py" ...
```

**`pack-out` is stated by the harness, not supplied.** A context pack is a
DERIVED artifact - `pack.py` says so in its own help - so it belongs in the
gitignored scratch directory and never in git, and the run folder is committed
with the code it describes. Passing `--set pack-out=` is refused rather than
ignored: a supplied value nothing reads is the same defect as a value nobody
supplies, arriving from the other side.

## Steps

1. **Check the graph is fresh**, or state that it is not. The `graph-build`
   workflow owns freshness. Scoping over a stale graph is allowed; doing it
   silently is not.
2. **Build the impact pack** — `tools/graph/pack.py --graph-dir .sdlc/graph
   --diff <file> --component <alias> --out <pack-out> --no-svg`. Without a diff
   yet, seed from the files the requirements name.
3. **Read the pack, and read what it says is missing.** An empty Tests section
   is a finding about the repository, not an empty section.
4. **Cross-run overlap.** `worktree.py --target <repo> --run <id> --overlap`
   names every other live run working in the same components. It is
   **advisory** and exits 1 when it finds one: there is no lock, because the
   ruling on file is optimistic concurrency with rebase-before-merge as the
   real enforcement. What it buys is that a human sees the collision at G1,
   while sequencing the two runs is still cheap. Put what it says in the scope
   document either way — including that it found nothing.
5. **Write the scope**: what is in, what is out, what the pack says is reached
   and by which route, and every claim's provenance.

## What this phase owes

`20-scope.md`, and **G1 stands at the end of it** — scope plus requirements are
signed off together. Finish the artifact and stop; you do not take the gate.

```
state.py --target <repo> --record <id> --artifact 20-scope.md
```
