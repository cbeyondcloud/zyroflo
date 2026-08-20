"""Impact prototype: from one seed file to a ranked blast radius, read off the graph.

Item 18. The M1 kill gate asks a falsifiable question — given the file a change starts
from, does graph-derived impact rank what else really changed higher than a competent
grep does? — and this is the graph's side of that A/B (`docs/m0/judging-protocol.md`
section 3.2, Condition B). It reads a completed build's output directory and answers:

* **seed** — the facts the seed file itself declares. Every edge cites the file and line
  it was read from, so "the facts in this file" is a provenance lookup, not a guess.
  When the file declared nothing (a brand-new source file), it falls back to the facts
  declared nearest it by directory, and *says so* — a fallback ranking presented as a
  provenance ranking would overstate the evidence.
* **traversal** — bounded, direction-aware, damped. A producer change must reach its
  consumers at nearly full strength while a consumer change barely disturbs its
  producer; a change in a shared module must reach every dependent. Damping multiplies
  per hop with the edge's own confidence, and non-canonical variants are damped ×0.5,
  never dropped — the standing canonical rule.
* **ranking** — a file scores the *minimum* of its fact's two endpoint scores: a fact is
  only as reached as its least-reached side, which is what keeps the direction doctrine
  visible in the file list and not just in the node scores.

The damping table below is the prototype's own and is deliberately small. Item 21
replaces it with `traversal_rules.yml` and real blast-radius scoring; if these constants
survive to M2 unexamined, that is a defect in item 21, not a feature of this file.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

SCHEMA_VERSION = 1

#: (along, against) per edge type — "along" traverses src->dst. Transcribed from the
#: scoping plan's design table where it names a number, defaults elsewhere.
DAMPING = {
    "DG_CALLS": (0.3, 0.9),
    "DG_PRODUCES": (1.0, 0.2),
    "DG_CONSUMES": (0.2, 0.9),
    "DG_WRITES_TABLE": (0.6, 0.8),
    "DG_DEPENDS_ON": (0.6, 0.9),
    "DG_BUILT_FROM": (0.9, 0.9),
    "DG_EXPOSES": (0.9, 0.9),
    "DG_OWNS_TABLE": (0.8, 0.8),
    "DG_CONFIGURES": (0.8, 0.8),
    "DG_TESTS": (0.8, 0.6),
    "DG_SPECIFIES": (0.7, 0.7),
    "DG_DEPLOYS": (0.6, 0.6),
    "DG_IN_REPO": (0.2, 0.2),
}
DEFAULT_DAMP = (0.5, 0.5)
NONCANONICAL_FACTOR = 0.5
DEFAULT_HOPS = 3


def load_graph(out_dir: Path) -> tuple[list[dict], list[dict]]:
    """A build's graph.jsonl as (nodes, edges), the same discrimination validate.py uses."""
    rows = [json.loads(line) for line
            in (out_dir / "graph.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()]
    rows = [r for r in rows if r.get("kind") != "header"]
    return [r for r in rows if "src" not in r], [r for r in rows if "src" in r]


def known_components(nodes: list[dict]) -> set[str]:
    known = {n["id"].split(":", 1)[1] for n in nodes if n.get("type") == "repo"}
    known |= {n.get("props", {}).get("component", "") for n in nodes}
    return known - {""}


def node_owner(node_id: str, nodes_by_id: dict[str, dict], known: set[str]) -> str:
    """Which component a node belongs to, or '' for genuinely shared ones (topics,
    tables, endpoints, module coordinates) — those are the joins, not anyone's file."""
    props = nodes_by_id.get(node_id, {}).get("props", {})
    if props.get("component"):
        return props["component"]
    tail = node_id.split(":", 1)[1] if ":" in node_id else node_id
    if "@" in tail:
        return tail.rsplit("@", 1)[1]
    head, slash, _ = tail.partition("/")
    return head if slash and head in known else ""


def edge_owner(e: dict, nodes_by_id: dict[str, dict], known: set[str]) -> str:
    """The component whose file declared this edge. Extractors stamp props.component
    when neither endpoint is component-bound (the write-mapping shape)."""
    return (e.get("props", {}).get("component", "")
            or node_owner(e["src"], nodes_by_id, known)
            or node_owner(e["dst"], nodes_by_id, known))


def seed_scores(component: str, path: str, nodes: list[dict], edges: list[dict],
                nodes_by_id: dict[str, dict], known: set[str]) -> tuple[dict, str]:
    """Seed nodes for one file, with the mode that names how much evidence that is."""
    seeds: dict[str, float] = {}
    for e in edges:
        if e["source_file"] == path and edge_owner(e, nodes_by_id, known) == component:
            seeds[e["src"]] = 1.0
            seeds[e["dst"]] = 1.0
    for n in nodes:
        p = n.get("props", {})
        if p.get("component") == component and p.get("path") == path:
            seeds[n["id"]] = 1.0
    if seeds:
        return seeds, "provenance"

    owned = [e for e in edges if edge_owner(e, nodes_by_id, known) == component]
    if owned:
        def shared_depth(a: str) -> int:
            want = path.rsplit("/", 1)[0].split("/") if "/" in path else []
            have = a.rsplit("/", 1)[0].split("/") if "/" in a else []
            depth = 0
            for x, y in zip(want, have):
                if x != y:
                    break
                depth += 1
            return depth
        best = max(shared_depth(e["source_file"]) for e in owned)
        for e in owned:
            if shared_depth(e["source_file"]) == best:
                seeds[e["src"]] = 0.8
                seeds[e["dst"]] = 0.8
        return seeds, "directory"

    for n in nodes:
        if n.get("type") == "svc" and n.get("props", {}).get("component") == component:
            seeds[n["id"]] = 0.5
    return seeds, "component"


def traverse(seeds: dict[str, float], edges: list[dict],
             hops: int = DEFAULT_HOPS) -> dict[str, float]:
    """Bounded max-product spread: score = best path's product of damp x confidence."""
    best = dict(seeds)
    frontier = dict(seeds)
    for _ in range(hops):
        advanced: dict[str, float] = {}
        for e in edges:
            along, against = DAMPING.get(e["type"], DEFAULT_DAMP)
            factor = e.get("confidence", 1.0) * (
                NONCANONICAL_FACTOR if not e.get("canonical", True) else 1.0)
            for here, there, damp in ((e["src"], e["dst"], along),
                                      (e["dst"], e["src"], against)):
                if here in frontier:
                    score = frontier[here] * damp * factor
                    if score > best.get(there, 0.0) + 1e-12:
                        advanced[there] = max(advanced.get(there, 0.0), score)
        if not advanced:
            break
        for node, score in advanced.items():
            best[node] = max(best.get(node, 0.0), score)
        frontier = advanced
    return best


def rank_files(best: dict[str, float], nodes: list[dict], edges: list[dict],
               nodes_by_id: dict[str, dict], known: set[str],
               exclude: tuple[str, str]) -> list[dict]:
    scores: dict[tuple[str, str], float] = {}
    # Item 78: a file whose facts are stamped `generated` must SAY so in the ranking —
    # the score stands (the coupling is real), but the human action changes from "edit
    # this" to "regenerate this". Keys are added only when true, so an unstamped file's
    # row is byte-identical to what it was before the flag existed.
    gen: dict[tuple[str, str], dict] = {}

    def note(key: tuple[str, str], props: dict) -> None:
        if props.get("generated"):
            flag = gen.setdefault(key, {"generated": True})
            if props.get("regenerate_from") and "regenerate_from" not in flag:
                flag["regenerate_from"] = props["regenerate_from"]

    for e in edges:
        reached = min(best.get(e["src"], 0.0), best.get(e["dst"], 0.0))
        if reached <= 0.0:
            continue
        comp = edge_owner(e, nodes_by_id, known)
        if not comp or (comp, e["source_file"]) == exclude:
            continue
        key = (comp, e["source_file"])
        scores[key] = max(scores.get(key, 0.0), reached)
        note(key, e.get("props", {}))
    for n in nodes:
        p = n.get("props", {})
        if p.get("component") and p.get("path"):
            key = (p["component"], p["path"])
            reached = best.get(n["id"], 0.0)
            if reached > 0.0 and key != exclude:
                scores[key] = max(scores.get(key, 0.0), reached)
                note(key, p)
    return [{"component": c, "path": pth, "score": round(s, 6), **gen.get((c, pth), {})}
            for (c, pth), s in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))]


def rank_services(best: dict[str, float], nodes: list[dict]) -> list[dict]:
    rows = [{"id": n["id"], "score": round(best[n["id"]], 6)}
            for n in nodes if n.get("type") == "svc" and best.get(n["id"], 0.0) > 0.0]
    return sorted(rows, key=lambda r: (-r["score"], r["id"]))


def impact(nodes: list[dict], edges: list[dict], component: str, path: str,
           hops: int = DEFAULT_HOPS) -> dict | None:
    """The full result for one seed, or None when the component is not in this graph."""
    known = known_components(nodes)
    if component not in known:
        return None
    nodes_by_id = {n["id"]: n for n in nodes}
    seeds, mode = seed_scores(component, path, nodes, edges, nodes_by_id, known)
    best = traverse(seeds, edges, hops)
    return {
        "schema_version": SCHEMA_VERSION,
        "seed": {"component": component, "path": path, "mode": mode, "hops": hops},
        "services": rank_services(best, nodes),
        "files": rank_files(best, nodes, edges, nodes_by_id, known, (component, path)),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--graph-dir", required=True, metavar="DIR",
                    help="a completed build's output directory (graph.jsonl)")
    ap.add_argument("--seed", required=True, metavar="COMPONENT/RELPATH",
                    help="the file the change starts from: component alias, then the "
                         "component-relative path exactly as extractors cite it")
    ap.add_argument("--hops", type=int, default=DEFAULT_HOPS)
    ap.add_argument("--top", type=int, default=20, metavar="N",
                    help="files to list (0 = all)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    out_dir = Path(args.graph_dir)
    if not (out_dir / "graph.jsonl").exists():
        print(f"!! {out_dir / 'graph.jsonl'} does not exist — point --graph-dir at a "
              f"completed build's output directory")
        return 2
    if "/" not in args.seed:
        print("!! --seed must be COMPONENT/RELPATH")
        return 2
    component, path = args.seed.split("/", 1)

    nodes, edges = load_graph(out_dir)
    result = impact(nodes, edges, component, path, hops=args.hops)
    if result is None:
        print(f"!! component {component!r} is not in this graph\n"
              f"   components: {', '.join(sorted(known_components(nodes)))}")
        return 2
    if args.top:
        result["files"] = result["files"][:args.top]

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    seed = result["seed"]
    print(f"seed {seed['component']}/{seed['path']}  (mode: {seed['mode']}, "
          f"hops: {seed['hops']})")
    print("\nservices:")
    for s in result["services"]:
        print(f"  {s['score']:>8.4f}  {s['id']}")
    print("\nfiles:")
    for f in result["files"]:
        mark = ""
        if f.get("generated"):
            src = f.get("regenerate_from", "")
            mark = (f"  [generated — regenerate, don't edit"
                    + (f"; from: {src}" if src else "") + "]")
        print(f"  {f['score']:>8.4f}  {f['component']}/{f['path']}{mark}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
