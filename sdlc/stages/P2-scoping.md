---
id: P2
name: scoping
after: requirements
inputs: [10-requirements.md]
outputs: [20-scope.md]
conditional: []
evidence: []
after_gate: []
needs: graph
gate: G1
---
# P2 · scoping — the blast radius, with provenance, before the design

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
tools/sdlc/stage.py --target <repo> --run <id> --phase P2 --prompt --set component=<alias>
```

Plus `10-requirements.md` — what this run is FOR. **Step 2 seeds the pack from
a file this document names**, so read it first. The prompt states it and says
whether it is there; it is not something to go hunting for.

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

1. **Check the graph is fresh**, or state that it is not:

   ```
   tools/graph/build.py --target <repo> --check-fresh
   ```

   The `graph-build` workflow owns the POLICY; that line is the command that
   answers. **Exit 2 means stale or refused, and is not a stop** — it names
   each stale component and why, and each shard it refused with its remedy.
   Scoping over a stale graph is allowed; doing it silently is not, so put the
   answer in the scope document either way.
2. **Build the impact pack.** Once a diff exists:

   ```
   tools/graph/pack.py --graph-dir .sdlc/graph --diff <file> --component <alias> --out <pack-out> --no-svg
   ```

   At P2 there usually is none — the change does not exist until P4 — so seed
   from a file that `10-requirements.md` names. **Walk 4 stopped here**: this
   step said "a file the requirements name" and nothing told the runner that
   the requirements were a document, or where it was, so a runner that had done
   everything else right asked a human and stopped. Read the requirements, pick
   a file they name, and write down which one you seeded from and why:

   ```
   tools/graph/pack.py --graph-dir .sdlc/graph --seed <component>/<path inside it> --component <alias> --out <pack-out> --no-svg
   ```

   **A seed is `COMPONENT/RELPATH`, and never an absolute path.** An absolute
   path contains a separator too, so it parses as a component this graph has
   never heard of; `pack.py` says so by name and exits 2. The component is the
   alias the graph knows, not a directory on disk.

   **Read the pack's Seeds section before you trust the rest of the pack.** This
   is an INTEGRATION graph — endpoints, topics, configs, build dependencies —
   and not an index of every source file, so an ordinary class often has no node
   and nothing can seed from it. When that happens `pack.py` still exits 0 and
   still renders a full pack, so the Seeds section says in words that the answer
   is **directory-scope and not an impact map**. **Walk 4 spent three scope
   drafts and two human rejections** arguing about a pack that described the
   component rather than the change, because nothing said which one it was.
   If the Seeds section carries that statement, say so in `20-scope.md` and
   state how you bounded the change by hand — never present it as an impact map.
3. **Read the pack, and read what it says is missing.** An empty Tests section
   is a finding about the repository, not an empty section. A Seeds section
   saying it scoped by directory is the same kind of finding — about what this
   graph knows of the file you seeded, not about the change.
4. **Cross-run overlap.** `tools/sdlc/worktree.py --target <repo> --run <id> --overlap`
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
tools/sdlc/state.py --target <repo> --record <id> --artifact 20-scope.md
```
