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

The damping table lives in `tools/graph/rules.py` since item 21 examined it (two
constants changed under measurement — the examination its predecessor demanded), and a
workspace overrides it with a `traversal_rules.yml` beside the graph. Traversal also
records each node's hop depth and best-path parent — a blast radius someone cannot ask
"why is this here?" of is a mood ring.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from tools.graph.rules import (DEFAULT_RULES, RULES_BASENAME, Rules, RulesError,
                               discover_rules)

SCHEMA_VERSION = 1

DEFAULT_HOPS = DEFAULT_RULES.hops


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


#: `@@ -a,b +c,d @@` — the new-side start and count are what a post-change file means.
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def parse_lines(spec: str) -> list[tuple[int, int]]:
    """`"5"`, `"12-40,55"` → [(12,40),(55,55)]. Refuses junk loudly — a mistyped range
    that silently matched nothing would read exactly like a change with no impact."""
    out: list[tuple[int, int]] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", part)
        if not m:
            raise ValueError(f"--lines wants N or A-B, comma-separated; got {part!r}")
        a, b = int(m.group(1)), int(m.group(2)) if m.group(2) else int(m.group(1))
        if b < a:
            raise ValueError(f"--lines range is backwards: {part!r}")
        out.append((a, b))
    if not out:
        raise ValueError("--lines named no lines")
    return out


def parse_diff(text: str) -> dict[str, list[tuple[int, int]] | None]:
    """A unified diff → {path: new-side hunk ranges, or None for \"the whole file\"}.

    Paths come from `+++ b/…` with the `a/`/`b/` prefix stripped. Header lines are
    recognised STATEFULLY — `+++ ` counts only immediately after `--- ` — because an
    added content line may itself begin with plus signs. A deleted file (`+++
    /dev/null`) maps its OLD path to None: in a graph built before the change its
    facts ARE the change, and there is no new-side line to point at. A file whose
    headers carry no hunks (a mode-only change) also maps to None — touched, with no
    line information. A zero-count hunk (`+c,0`, pure deletion) touches `(c, c)`, the
    removal site. Quoted paths (spaces) are not handled — a bounded blind spot: such
    a file simply contributes no seeds, it is never misattributed.
    """
    files: dict[str, list[tuple[int, int]] | None] = {}
    current: str | None = None
    prev_minus: str | None = None
    for line in text.splitlines():
        if line.startswith("--- "):
            prev_minus = line[4:].split("\t")[0].strip()
            continue
        if line.startswith("+++ ") and prev_minus is not None:
            new_path = line[4:].split("\t")[0].strip()
            if new_path == "/dev/null":
                old = prev_minus[2:] if prev_minus.startswith(("a/", "b/")) else prev_minus
                if old != "/dev/null":
                    files[old] = None
                current = None
            else:
                current = new_path[2:] if new_path.startswith(("a/", "b/")) else new_path
                files.setdefault(current, [])
            prev_minus = None
            continue
        prev_minus = None
        m = _HUNK.match(line)
        if m and current is not None and files.get(current) is not None:
            start = int(m.group(1))
            count = int(m.group(2)) if m.group(2) is not None else 1
            files[current].append((start, start + max(count, 1) - 1))
    return {p: (r if r else None) for p, r in files.items()}


def _provenance_seeds(component: str, path: str, nodes: list[dict], edges: list[dict],
                      nodes_by_id: dict[str, dict], known: set[str],
                      lines: list[tuple[int, int]] | None = None
                      ) -> tuple[dict[str, float], bool, bool]:
    """The facts one file declares, hunk-refined when ranges are given.

    Returns (seeds, refined, lines_missed). `refined` — the ranges actually selected
    a line-addressed subset. `lines_missed` — ranges were given, line-addressed facts
    exist, and none intersect: the whole file stands in and the seed block must say
    so, because a fallback that does not name itself overstates its evidence. A
    line-0 fact is a whole-file claim and seeds whenever the file is touched at all;
    likewise the file's own node.
    """
    file_edges = [e for e in edges if e["source_file"] == path
                  and edge_owner(e, nodes_by_id, known) == component]
    file_nodes = [n for n in nodes
                  if n.get("props", {}).get("component") == component
                  and n.get("props", {}).get("path") == path]

    def endpoints(sel: list[dict]) -> dict[str, float]:
        s: dict[str, float] = {}
        for e in sel:
            s[e["src"]] = 1.0
            s[e["dst"]] = 1.0
        for n in file_nodes:
            s[n["id"]] = 1.0
        return s

    if lines is None:
        return endpoints(file_edges), False, False
    addressed = [e for e in file_edges if e.get("line", 0) > 0]
    hit = [e for e in addressed if any(a <= e["line"] <= b for a, b in lines)]
    if hit:
        whole_file = [e for e in file_edges if e.get("line", 0) == 0]
        return endpoints(hit + whole_file), True, False
    return endpoints(file_edges), False, bool(addressed)


def seed_scores(component: str, path: str, nodes: list[dict], edges: list[dict],
                nodes_by_id: dict[str, dict], known: set[str],
                lines: list[tuple[int, int]] | None = None) -> tuple[dict, str, bool]:
    """Seed nodes for one file, with the mode that names how much evidence that is."""
    seeds, refined, missed = _provenance_seeds(
        component, path, nodes, edges, nodes_by_id, known, lines)
    if seeds:
        return seeds, ("hunk" if refined else "provenance"), missed

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
        return seeds, "directory", False

    for n in nodes:
        if n.get("type") == "svc" and n.get("props", {}).get("component") == component:
            seeds[n["id"]] = 0.5
    return seeds, "component", False


def traverse_paths(seeds: dict[str, float], edges: list[dict],
                   hops: int | None = None, rules: Rules = DEFAULT_RULES
                   ) -> tuple[dict[str, float], dict[str, int], dict[str, str]]:
    """Bounded max-product spread: score = best path's product of damp × confidence,
    with the item-21 hub rule. Returns (scores, hop-depth, best-path parent).

    A node the traversal merely REACHED spreads at strength × threshold/fan once its
    fan through one edge type exceeds `rules.hub_threshold` — measured on a real
    workspace, a transitively-reached service dumped 1,429 test files one hop later,
    and no per-type constant can express "this NODE is a hub". Seeds are exempt
    (their fan is the query, and the seed component's own facts ranking first is the
    A/B gate's recorded winning mechanism); `wide` types are exempt (a topic reached
    transitively SHOULD hand you every consumer — that coupling is the point).
    """
    hops = rules.hops if hops is None else hops
    deg_along: dict[tuple[str, str], int] = {}
    deg_against: dict[tuple[str, str], int] = {}
    for e in edges:
        key_a, key_b = (e["src"], e["type"]), (e["dst"], e["type"])
        deg_along[key_a] = deg_along.get(key_a, 0) + 1
        deg_against[key_b] = deg_against.get(key_b, 0) + 1

    best = dict(seeds)
    hop_of = {node: 0 for node in seeds}
    parent: dict[str, str] = {}
    frontier = dict(seeds)
    for depth in range(1, hops + 1):
        advanced: dict[str, float] = {}
        via: dict[str, str] = {}
        for e in edges:
            along, against = rules.damping(e["type"])
            wide = rules.wide(e["type"])
            factor = e.get("confidence", 1.0) * (
                rules.noncanonical if not e.get("canonical", True) else 1.0)
            for here, there, damp, fan in (
                    (e["src"], e["dst"], along,
                     deg_along.get((e["src"], e["type"]), 1)),
                    (e["dst"], e["src"], against,
                     deg_against.get((e["dst"], e["type"]), 1))):
                if here in frontier:
                    attenuation = 1.0
                    if (here not in seeds and not wide
                            and fan > rules.hub_threshold):
                        attenuation = rules.hub_threshold / fan
                    score = frontier[here] * damp * factor * attenuation
                    if (score > best.get(there, 0.0) + 1e-12
                            and score > advanced.get(there, 0.0)):
                        advanced[there] = score
                        via[there] = here
        if not advanced:
            break
        for node, score in advanced.items():
            if score > best.get(node, 0.0):
                best[node] = score
                hop_of[node] = depth
                parent[node] = via[node]
        frontier = advanced
    return best, hop_of, parent


def traverse(seeds: dict[str, float], edges: list[dict],
             hops: int | None = None,
             rules: Rules = DEFAULT_RULES) -> dict[str, float]:
    return traverse_paths(seeds, edges, hops, rules)[0]


def score_files(best: dict[str, float], nodes: list[dict], edges: list[dict],
                nodes_by_id: dict[str, dict], known: set[str],
                exclude: set[tuple[str, str]],
                hop_of: dict[str, int] | None = None) -> dict[tuple[str, str], dict]:
    """Rich per-file entries — the one scoring implementation behind both views.

    `rank_files` formats the CLI rows from this; the pack (item 22) reads the same
    entries for the winning FACT behind each score, so a pack row can say why a file
    is listed without a second scoring loop that could drift.
    """
    hop_of = hop_of or {}
    entries: dict[tuple[str, str], dict] = {}

    def note(key: tuple[str, str], props: dict) -> None:
        # Item 78: any contributing stamped fact flags the row — the score stands,
        # the human action changes from "edit this" to "regenerate this".
        if props.get("generated"):
            entry = entries[key]
            entry["generated"] = True
            if props.get("regenerate_from") and "regenerate_from" not in entry:
                entry["regenerate_from"] = props["regenerate_from"]

    def take(key: tuple[str, str], reached: float, hops: int,
             edge: dict | None, node: dict | None) -> None:
        entry = entries.setdefault(key, {"score": 0.0, "hops": 0,
                                         "edge": None, "node": None})
        if reached > entry["score"]:
            entry.update(score=reached, hops=hops, edge=edge, node=node)

    for e in edges:
        reached = min(best.get(e["src"], 0.0), best.get(e["dst"], 0.0))
        if reached <= 0.0:
            continue
        comp = edge_owner(e, nodes_by_id, known)
        if not comp or (comp, e["source_file"]) in exclude:
            continue
        key = (comp, e["source_file"])
        take(key, reached, max(hop_of.get(e["src"], 0), hop_of.get(e["dst"], 0)),
             e, None)
        note(key, e.get("props", {}))
    for n in nodes:
        p = n.get("props", {})
        if p.get("component") and p.get("path"):
            key = (p["component"], p["path"])
            reached = best.get(n["id"], 0.0)
            if reached > 0.0 and key not in exclude:
                take(key, reached, hop_of.get(n["id"], 0), None, n)
                note(key, p)
    return entries


def rank_files(best: dict[str, float], nodes: list[dict], edges: list[dict],
               nodes_by_id: dict[str, dict], known: set[str],
               exclude: set[tuple[str, str]],
               hop_of: dict[str, int] | None = None) -> list[dict]:
    entries = score_files(best, nodes, edges, nodes_by_id, known, exclude, hop_of)
    return [{"component": c, "path": pth, "score": round(entry["score"], 6),
             "hops": entry["hops"],
             **({"generated": True} if entry.get("generated") else {}),
             **({"regenerate_from": entry["regenerate_from"]}
                if entry.get("regenerate_from") else {})}
            for (c, pth), entry in sorted(entries.items(),
                                          key=lambda kv: (-kv[1]["score"], kv[0]))]


def rank_services(best: dict[str, float], nodes: list[dict],
                  hop_of: dict[str, int] | None = None) -> list[dict]:
    hop_of = hop_of or {}
    rows = [{"id": n["id"], "score": round(best[n["id"]], 6),
             "hops": hop_of.get(n["id"], 0)}
            for n in nodes if n.get("type") == "svc" and best.get(n["id"], 0.0) > 0.0]
    return sorted(rows, key=lambda r: (-r["score"], r["id"]))


def impact(nodes: list[dict], edges: list[dict], component: str, path: str,
           hops: int | None = None,
           lines: list[tuple[int, int]] | None = None,
           rules: Rules = DEFAULT_RULES) -> dict | None:
    """The full result for one seed, or None when the component is not in this graph.

    `lines` narrows the seed to the facts cited inside those ranges (item 20): a
    thirty-key config file changed at one key seeds one fact, not thirty. The seed
    block records the ranges and whether they missed.
    """
    known = known_components(nodes)
    if component not in known:
        return None
    hops = rules.hops if hops is None else hops
    nodes_by_id = {n["id"]: n for n in nodes}
    seeds, mode, missed = seed_scores(component, path, nodes, edges, nodes_by_id,
                                      known, lines)
    best, hop_of, _ = traverse_paths(seeds, edges, hops, rules)
    seed_block: dict = {"component": component, "path": path, "mode": mode,
                        "hops": hops}
    if lines is not None:
        seed_block["lines"] = [[a, b] for a, b in lines]
        seed_block["lines_missed"] = missed
    return {
        "schema_version": SCHEMA_VERSION,
        "seed": seed_block,
        "services": rank_services(best, nodes, hop_of),
        "files": rank_files(best, nodes, edges, nodes_by_id, known,
                            {(component, path)}, hop_of),
    }


def diff_seeds(component: str,
               targets: list[tuple[str, list[tuple[int, int]] | None]],
               nodes: list[dict], edges: list[dict],
               nodes_by_id: dict[str, dict],
               known: set[str]) -> tuple[dict[str, float], list[dict], str]:
    """The seed layer of a whole diff: (union, per-file records, aggregate mode).

    One implementation for `impact_diff` and the pack — the fallback-only-when-
    nothing-seeded rule must not exist twice.
    """
    union: dict[str, float] = {}
    per_file: list[dict] = []
    any_hunk = False
    for path, ranges in targets:
        seeds, refined, missed = _provenance_seeds(
            component, path, nodes, edges, nodes_by_id, known, ranges)
        any_hunk = any_hunk or refined
        entry: dict = {"path": path,
                       "mode": ("hunk" if refined else
                                "provenance" if seeds else "none"),
                       "seeds": len(seeds)}
        if ranges is not None:
            entry["lines_missed"] = missed
        per_file.append(entry)
        for node_id, score in seeds.items():
            union[node_id] = max(union.get(node_id, 0.0), score)

    mode = "hunk" if any_hunk else ("provenance" if union else "")
    if not union:
        union, mode, _ = seed_scores(component, targets[0][0], nodes, edges,
                                     nodes_by_id, known)
    return union, per_file, mode


def impact_diff(nodes: list[dict], edges: list[dict], component: str,
                targets: list[tuple[str, list[tuple[int, int]] | None]],
                hops: int | None = None,
                rules: Rules = DEFAULT_RULES) -> dict | None:
    """One component's whole diff → one blast radius (item 20).

    `targets` is [(path, ranges-or-None)], the shape `parse_diff` produces. Per-file
    provenance/hunk seeds union under MAX — a node two files both touch is not twice
    as reached. Measured before designed: on a real workspace all three judged A/B
    seed files declared ZERO facts, so the single-seed prototype never provenance-
    seeded at all; a diff's OTHER files were where the evidence was. The directory /
    component fallback therefore applies only when NO file seeded anything — per-file
    fallback would let each factless file of a 96-file merge pull in its whole
    directory at 0.8 — and it is taken from the first target, with the mode naming
    what happened.
    """
    if not targets:
        return None
    known = known_components(nodes)
    if component not in known:
        return None
    hops = rules.hops if hops is None else hops
    nodes_by_id = {n["id"]: n for n in nodes}
    union, per_file, mode = diff_seeds(component, targets, nodes, edges,
                                       nodes_by_id, known)
    best, hop_of, _ = traverse_paths(union, edges, hops, rules)
    exclude = {(component, p) for p, _ in targets}
    return {
        "schema_version": SCHEMA_VERSION,
        "seed": {"component": component, "mode": mode, "hops": hops,
                 "files": per_file},
        "services": rank_services(best, nodes, hop_of),
        "files": rank_files(best, nodes, edges, nodes_by_id, known, exclude, hop_of),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--graph-dir", required=True, metavar="DIR",
                    help="a completed build's output directory (graph.jsonl)")
    ap.add_argument("--seed", metavar="COMPONENT/RELPATH",
                    help="the file the change starts from: component alias, then the "
                         "component-relative path exactly as extractors cite it")
    ap.add_argument("--lines", metavar="RANGES",
                    help="hunk ranges inside --seed's file, e.g. 5 or 12-40,55 — "
                         "seeds only the facts cited there (item 20)")
    ap.add_argument("--diff", metavar="FILE",
                    help="a unified diff; every touched file becomes a seed, "
                         "hunk-refined where it declares facts (item 20)")
    ap.add_argument("--component", metavar="ALIAS",
                    help="the component the diff's paths are relative to "
                         "(required with --diff)")
    ap.add_argument("--rules", metavar="FILE",
                    help=f"a {RULES_BASENAME} overriding the built-in damping table "
                         f"(default: {RULES_BASENAME} beside the graph if the build "
                         f"placed one there, else the built-ins)")
    ap.add_argument("--hops", type=int, default=None,
                    help="traversal depth (default: the rules' hops)")
    ap.add_argument("--top", type=int, default=20, metavar="N",
                    help="files to list (0 = all)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    out_dir = Path(args.graph_dir)
    if not (out_dir / "graph.jsonl").exists():
        print(f"!! {out_dir / 'graph.jsonl'} does not exist — point --graph-dir at a "
              f"completed build's output directory")
        return 2
    if bool(args.seed) == bool(args.diff):
        print("!! exactly one of --seed and --diff")
        return 2
    if args.diff and not args.component:
        print("!! --diff needs --component: the diff's paths are component-relative "
              "and nothing in a diff says which component it is from")
        return 2
    if args.lines and not args.seed:
        print("!! --lines refines --seed's file; with --diff the hunks come from "
              "the diff itself")
        return 2

    nodes, edges = load_graph(out_dir)
    try:
        rules, rules_source = discover_rules(
            out_dir, Path(args.rules) if args.rules else None)
    except RulesError as exc:
        print(f"!! {exc}")
        return 2
    if rules is not DEFAULT_RULES:
        graph_types = {e["type"] for e in edges}
        unmatched = sorted(set(rules.table) - graph_types)
        if unmatched:
            print(f"note: the rules name {len(unmatched)} edge type(s) this graph "
                  f"does not contain: {', '.join(unmatched)}")

    if args.diff:
        diff_path = Path(args.diff)
        if not diff_path.exists():
            print(f"!! {diff_path} does not exist")
            return 2
        targets = sorted(parse_diff(
            diff_path.read_text(encoding="utf-8", errors="replace")).items())
        if not targets:
            print(f"!! {diff_path} contains no file headers a unified diff would have")
            return 2
        result = impact_diff(nodes, edges, args.component, targets, hops=args.hops,
                             rules=rules)
        if result is None:
            print(f"!! component {args.component!r} is not in this graph\n"
                  f"   components: {', '.join(sorted(known_components(nodes)))}")
            return 2
    else:
        if "/" not in args.seed:
            print("!! --seed must be COMPONENT/RELPATH")
            return 2
        component, path = args.seed.split("/", 1)
        lines = None
        if args.lines:
            try:
                lines = parse_lines(args.lines)
            except ValueError as exc:
                print(f"!! {exc}")
                return 2
        result = impact(nodes, edges, component, path, hops=args.hops, lines=lines,
                        rules=rules)
        if result is None:
            print(f"!! component {component!r} is not in this graph\n"
                  f"   components: {', '.join(sorted(known_components(nodes)))}")
            return 2
    if args.top:
        result["files"] = result["files"][:args.top]
    result["rules"] = rules_source

    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    seed = result["seed"]
    rules_note = "" if rules_source == "defaults" else f", rules: {Path(rules_source).name}"
    if "files" in seed:
        print(f"seed {seed['component']} diff  (mode: {seed['mode']}, "
              f"hops: {seed['hops']}{rules_note})")
        for f in seed["files"]:
            missed = "  (lines missed — whole file)" if f.get("lines_missed") else ""
            print(f"  {f['mode']:>10}  {f['seeds']:>3} seed(s)  {f['path']}{missed}")
    else:
        ranges = (",".join(f"{a}-{b}" if b > a else str(a)
                           for a, b in seed.get("lines", []))
                  if "lines" in seed else "")
        note = (f", lines: {ranges}" + (" — MISSED, whole file stands in"
                                        if seed.get("lines_missed") else "")
                if ranges else "")
        print(f"seed {seed['component']}/{seed['path']}  (mode: {seed['mode']}, "
              f"hops: {seed['hops']}{note}{rules_note})")
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
