"""Copy a fixture, apply string mutations, build the copy. One implementation, deliberately.

Both mutation suites (and item 14's third) need exactly this, and each carrying its own
copy is two chances for the honesty rules to drift apart silently: the line-count guard,
the replace-EVERY-occurrence semantics, and the copy-never-the-committed-tree rule are the
parts that make a mutation test mean something, and a module that quietly lost one of them
would keep passing while measuring less.

The rules, and why each is load-bearing:

* **The copy is what keeps this honest.** The committed fixture is the answer key's
  subject; a test that edited it in place would leave the repository in whichever state
  the last test happened to produce.
* **An edit replaces EVERY occurrence in its file.** One-at-a-time replacement looks more
  surgical and is not: a value declared in several places has several sources, and cutting
  one of them cuts nothing — which is how the first version of the orderflow suite
  reported a seam as intact when it had never been severed.
* **A mutation must not change the line count**, or every surviving hop's cited line moves
  and the assertions about them stop meaning anything.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import tools.graph.build as B


def build_mutated(fixture: Path, tmp_path: Path, rel: str = "", old: str = "", new: str = "",
                  drop_component: str = "", edits: list[tuple[str, str, str]] | None = None):
    """Copy `fixture`, apply the edits, build. Returns (report, edges, unresolved)."""
    work = tmp_path / "ws"
    shutil.copytree(fixture, work)

    for one_rel, one_old, one_new in ([(rel, old, new)] if rel else []) + (edits or []):
        path = work / one_rel
        text = path.read_text(encoding="utf-8")
        assert one_old in text, f"nothing to mutate: {one_old!r} is not in {one_rel}"
        mutated = text.replace(one_old, one_new)
        assert mutated.count("\n") == text.count("\n"), (
            "a mutation must not change the line count, or every surviving hop's cited line "
            "moves and the assertions about them stop meaning anything")
        path.write_text(mutated, encoding="utf-8")

    if drop_component:
        shutil.rmtree(work / drop_component)
        cfg = work / "workspace.toml"
        body = cfg.read_text(encoding="utf-8")
        block = f'[[component]]\nname = "{drop_component}"\npath = "{drop_component}"\n'
        assert block in body, "the component block to remove is not where it was expected"
        cfg.write_text(body.replace(block, ""), encoding="utf-8")

    out = tmp_path / "out"
    report = B.build(work / "workspace.toml", out, run_id="mutated")
    edges = [json.loads(line)
             for line in (out / "graph.jsonl").read_text(encoding="utf-8").splitlines()
             if '"kind": "edge"' in line]
    unresolved = [json.loads(line) for line in
                  (out / "unresolved.jsonl").read_text(encoding="utf-8").splitlines() if line]
    return report, edges, unresolved
