"""Context packs: the blast radius delivered as the prompt's first page.

Item 22. The delivery contract: a stage agent's prompt opens with `pack.md` already
listing what a change touches — every row carrying `source_file:line` provenance and
the graph route that put it there — before the agent reads any code; `pack.json` is
the same content, complete and versioned, for machine consumers (CI checks, the MCP
server, the eval corpus scorer). The text pack is canonical; the optional d2 render
is garnish for human review and its absence is stated, never silent.

Sections, and what earns a row a place:

* **Seeds** — item 20's seed records verbatim: per file, its mode (hunk / provenance /
  none), seed count, and whether given line ranges missed.
* **Contracts** — the interface nodes the change reaches (endpoints, topics, tables),
  plus the files that SPECIFY them: the surface other teams see.
* **Direct** (the winning fact sits ≤1 hop from the change) / **Indirect** (further) —
  the code files, split by the hop depth item 21 records.
* **Config coupling** — files whose winning fact is configuration (a `DG_CONFIGURES`
  edge, or a fact touching a cfg/envkey node).
* **Tests to run-or-edit** — files whose winning fact is a `DG_TESTS` claim or a test
  node: the suites covering what the change reaches.
* **Docs** — honest and empty: the graph holds no documentation facts yet, and a
  section that silently vanished would read as "no docs affected".

Classification priority (a file appears exactly once): tests → contracts → config →
direct/indirect. The budget trims `pack.md` GLOBALLY by ascending score and each
section states how many rows it lost — a trimmed list that does not admit it reads
exactly like a complete one. `pack.json` is never trimmed. Token counts use the
stated chars/4 approximation; the budget is a target, not a contract.

Packs are derived artifacts: computed on demand from a completed build, written where
told, never committed — the graph is the durable thing, a pack is a view of it.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from tools import cli  # noqa: E402
from tools.graph.impact import (GraphError, diff_seeds, edge_owner, entrypoints,
                                known_components, load_graph, parse_diff,
                                parse_lines, score_files, traverse_paths)
from tools.graph.rules import DEFAULT_RULES, Rules, RulesError, discover_rules

cli.use_utf8()

PACK_SCHEMA_VERSION = 1
DEFAULT_BUDGET = 4000
#: Interface node types — the contract surface a change is visible through.
CONTRACT_TYPES = ("ep", "topic", "table")


def token_estimate(text: str) -> int:
    """The stated approximation: one token per four characters, rounded up."""
    return (len(text) + 3) // 4


def route_of(node_id: str, parents: dict[str, str], seeds: dict[str, float],
             limit: int = 12) -> list[str]:
    """The best path from a seed to this node, seed first. Bounded, cycle-safe."""
    chain = [node_id]
    current = node_id
    while current not in seeds and current in parents and len(chain) < limit:
        current = parents[current]
        chain.append(current)
    return list(reversed(chain))


def _cite(node_id: str, touching: list[dict], nodes_by_id: dict[str, dict],
          known: set[str]) -> str:
    """Where a contract node is declared: its strongest touching edge, deterministically.

    `touching` is the node's own edges, pre-indexed by the caller: scanning every
    edge per contract node made this quadratic, and item 23's server measured that
    at ~18s of an estate pack's wall clock (3,972 contracts × 26,470 edges). The
    winner is unchanged — same candidates, same sort.
    """
    if not touching:
        return ""
    e = sorted(touching, key=lambda e: (-e.get("confidence", 1.0),
                                        not e.get("canonical", True),
                                        e["source_file"], e["line"]))[0]
    comp = edge_owner(e, nodes_by_id, known)
    return f"{comp}/{e['source_file']}:{e['line']}" if comp else \
        f"{e['source_file']}:{e['line']}"


def _section_of(entry: dict, nodes_by_id: dict[str, dict]) -> str:
    edge, node = entry.get("edge"), entry.get("node")
    def ntype(node_id: str) -> str:
        return nodes_by_id.get(node_id, {}).get("type", "")
    if (edge and edge["type"] == "DG_TESTS") or (node and node.get("type") == "test"):
        return "tests"
    if edge and edge["type"] == "DG_SPECIFIES":
        return "contracts"
    if edge and (edge["type"] == "DG_CONFIGURES"
                 or ntype(edge["src"]) in ("cfg", "envkey")
                 or ntype(edge["dst"]) in ("cfg", "envkey")):
        return "config"
    if node and node.get("type") in ("cfg", "envkey"):
        return "config"
    return "direct" if entry["hops"] <= 1 else "indirect"


def build_pack(nodes: list[dict], edges: list[dict], component: str,
               targets: list[tuple[str, list[tuple[int, int]] | None]],
               hops: int | None = None,
               rules: Rules = DEFAULT_RULES,
               graph_dir: Path | None = None) -> dict | None:
    """The whole pack as data. None when the component is not in this graph."""
    known = known_components(nodes)
    if component not in known or not targets:
        return None
    hops = rules.hops if hops is None else hops
    nodes_by_id = {n["id"]: n for n in nodes}
    seeds, per_file, mode = diff_seeds(component, targets, nodes, edges,
                                       nodes_by_id, known)
    best, hop_of, parents = traverse_paths(seeds, edges, hops, rules,
                                           entrypoints(nodes))
    exclude = {(component, p) for p, _ in targets}
    entries = score_files(best, nodes, edges, nodes_by_id, known, exclude, hop_of)

    sections: dict[str, list[dict]] = {
        "contracts": [], "direct": [], "indirect": [], "config": [], "tests": []}
    for (comp, path), entry in entries.items():
        edge, node = entry.get("edge"), entry.get("node")
        if edge is not None:
            far = max((edge["src"], edge["dst"]), key=lambda n: hop_of.get(n, 0))
            why = (f"{edge['type']} {edge['src']} -> {edge['dst']} "
                   f"@ {path}:{edge['line']}")
        else:
            far = node["id"] if node else ""
            why = f"{node['type']} node @ {path}" if node else ""
        row = {"component": comp, "path": path,
               "score": round(entry["score"], 6), "hops": entry["hops"],
               "why": why, "route": route_of(far, parents, seeds) if far else []}
        if entry.get("generated"):
            row["generated"] = True
            if entry.get("regenerate_from"):
                row["regenerate_from"] = entry["regenerate_from"]
        sections[_section_of(entry, nodes_by_id)].append(row)
    for rows in sections.values():
        rows.sort(key=lambda r: (-r["score"], r["component"], r["path"]))

    touching: dict[str, list[dict]] = {}
    for e in edges:
        touching.setdefault(e["src"], []).append(e)
        if e["dst"] != e["src"]:
            touching.setdefault(e["dst"], []).append(e)

    contract_nodes = []
    contract_seen: set[str] = set()
    for n in nodes:
        # graph.jsonl keeps one node row per emitting extractor (the store merges
        # them; this reader must too, or a twice-declared table lists twice).
        if (n.get("type") in CONTRACT_TYPES and best.get(n["id"], 0.0) > 0.0
                and n["id"] not in contract_seen):
            contract_seen.add(n["id"])
            contract_nodes.append(
                {"id": n["id"], "type": n["type"], "score": round(best[n["id"]], 6),
                 "hops": hop_of.get(n["id"], 0),
                 "route": route_of(n["id"], parents, seeds),
                 "declared_at": _cite(n["id"], touching.get(n["id"], []),
                                      nodes_by_id, known),
                 # Item 79: the stamp rides every serving surface — a front door
                 # in the radius is the user-visible half of the blast.
                 **({"entrypoint": True}
                    if n.get("props", {}).get("entrypoint") else {})})
    contract_nodes.sort(key=lambda r: (-r["score"], r["id"]))

    graph_meta: dict = {}
    if graph_dir is not None and (graph_dir / "build_report.json").exists():
        report = json.loads((graph_dir / "build_report.json").read_text(
            encoding="utf-8"))
        graph_meta = {"run_id": report.get("run_id", ""),
                      "workspace": report.get("workspace", {}).get("name", "")}

    return {
        "schema_version": PACK_SCHEMA_VERSION,
        "graph": graph_meta,
        "seed": {"component": component, "mode": mode, "hops": hops,
                 "files": per_file},
        "contracts": contract_nodes,
        "sections": sections,
        "docs": {"rows": [], "note": "the graph holds no documentation facts yet — "
                                     "absence of rows is absence of capability, not "
                                     "absence of affected docs"},
    }


# ── rendering ─────────────────────────────────────────────────────────────────

_TITLES = (("contracts_nodes", "Contracts — interface nodes"),
           ("contracts", "Contracts — specification files"),
           ("direct", "Direct (winning fact <=1 hop from the change)"),
           ("indirect", "Indirect"),
           ("config", "Config coupling"),
           ("tests", "Tests to run-or-edit"))


def _row_line(row: dict) -> str:
    mark = ""
    if row.get("generated"):
        src = row.get("regenerate_from", "")
        mark = ("  [generated — regenerate, don't edit"
                + (f"; from: {src}" if src else "") + "]")
    route = " -> ".join(row["route"])
    return (f"- {row['score']:.4f}  {row['component']}/{row['path']}  "
            f"(hop {row['hops']}){mark}\n    why: {row['why']}\n"
            f"    route: {route}")


def _node_line(row: dict) -> str:
    route = " -> ".join(row["route"])
    mark = "  [entry point]" if row.get("entrypoint") else ""
    return (f"- {row['score']:.4f}  {row['id']}  (hop {row['hops']}, declared at "
            f"{row['declared_at'] or 'nowhere the graph cites'}){mark}"
            f"\n    route: {route}")


def render_md(pack: dict, budget: int = DEFAULT_BUDGET) -> str:
    """The prose pack, trimmed globally by ascending score to the token budget.

    Seeds are never trimmed — a pack that hides what it started from cannot be
    checked. Every trimmed section states its loss and points at pack.json.
    """
    all_rows: list[tuple[str, float, str]] = []
    for row in pack["contracts"]:
        all_rows.append(("contracts_nodes", row["score"], _node_line(row)))
    for name, rows in pack["sections"].items():
        for row in rows:
            all_rows.append((name, row["score"], _row_line(row)))

    head = [f"# Context pack (schema v{pack['schema_version']})", ""]
    if pack["graph"]:
        head.append(f"graph: run `{pack['graph'].get('run_id', '')}`")
        head.append("")
    seed = pack["seed"]
    head += [f"## Seeds — {seed['component']} (mode: {seed['mode']}, "
             f"hops: {seed['hops']})", ""]
    for f in seed["files"]:
        missed = "  (lines missed — whole file)" if f.get("lines_missed") else ""
        head.append(f"- {f['mode']:>10}  {f['seeds']:>3} seed(s)  {f['path']}{missed}")
    head.append("")
    overhead = "\n".join(head) + "\n"

    # Greedy keep by descending score until the budget is spent; order within the
    # document stays by section.
    kept: set[int] = set()
    used = token_estimate(overhead) + 40 * len(_TITLES)
    for i, (_, _, line) in sorted(enumerate(all_rows),
                                  key=lambda iv: (-iv[1][1], iv[0])):
        cost = token_estimate(line + "\n")
        if used + cost > budget:
            continue
        kept.add(i)
        used += cost

    lines = list(head)
    elided: dict[str, int] = {}
    for section, title in _TITLES:
        rows = [(i, line) for i, (name, _, line) in enumerate(all_rows)
                if name == section]
        lines.append(f"## {title}")
        lines.append("")
        shown = [line for i, line in rows if i in kept]
        lines += shown or ["(none reached)"]
        lost = len(rows) - len(shown)
        if lost:
            elided[section] = lost
            lines.append(f"… {lost} more row(s) elided by the token budget — "
                         f"the full list is in pack.json")
        lines.append("")
    lines.append("## Docs")
    lines.append("")
    lines.append(pack["docs"]["note"])
    lines.append("")
    pack["budget"] = {"budget": budget,
                      "estimated_tokens": token_estimate("\n".join(lines)),
                      "elided": elided}
    return "\n".join(lines)


def render_d2(pack: dict, cap: int = 40) -> str:
    """The blast radius as d2 source: seeds, contracts, and the top files, joined by
    their routes. Bounded and deterministic; the text pack stays canonical."""
    def key(name: str) -> str:
        return "n" + "".join(ch if ch.isalnum() else "_" for ch in name)

    lines = ["direction: right"]
    declared: set[str] = set()

    def declare(name: str, shape: str = "") -> None:
        if name not in declared:
            declared.add(name)
            lines.append(f'{key(name)}: "{name}"'
                         + (f" {{shape: {shape}}}" if shape else ""))

    for f in pack["seed"]["files"]:
        declare(f"seed:{f['path']}", "page")
    drawn: set[tuple[str, str]] = set()
    rows = (pack["contracts"][:10]
            + pack["sections"]["direct"][:10] + pack["sections"]["indirect"][:5])
    for row in rows:
        if len(drawn) >= cap:
            break
        route = row.get("route", [])
        for a, b in zip(route, route[1:]):
            declare(a)
            declare(b)
            if (a, b) not in drawn:
                drawn.add((a, b))
                lines.append(f"{key(a)} -> {key(b)}")
    return "\n".join(lines) + "\n"


def _render_svg(d2_path: Path, svg_path: Path) -> str:
    """Best-effort d2 render via the vendored binary. Returns a status string —
    the absence of the garnish is stated, never silent, and never fatal."""
    from tools import third_party as tp
    plat = tp.current_platform()
    exe = tp.bin_dir(plat) / ("d2.exe" if plat.startswith("windows") else "d2")
    if not exe.exists():
        return f"not rendered: vendored d2 missing at {exe} (fetch: python " \
               f"tools/third_party.py --fetch)"
    try:
        proc = subprocess.run([str(exe), str(d2_path), str(svg_path)],
                              capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"not rendered: {type(exc).__name__}: {exc}"
    if proc.returncode != 0 or not svg_path.exists():
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return f"not rendered: d2 exited {proc.returncode}" \
               + (f" ({detail[-1]})" if detail else "")
    return svg_path.name


def seed_problem(seed: str, known: set[str]) -> str:
    """Why this `--seed` cannot name a component, in words its author can act on.

    Item 128, found by walk 3. A seed is `COMPONENT/RELPATH`, and an ABSOLUTE
    PATH contains a forward slash — so it sails past the emptiness check at the
    call site and splits into a component the graph has never heard of. The
    refusal then read `component 'D:' is not in this graph`, which is true and
    names the wrong thing: nothing in it says the SEED was malformed, so a
    runner goes looking for a component called `D:`. That is exactly what
    happened, and it cost a paid call.

    Item 117's argument, one module along: a refusal nobody can act on is a
    refusal nobody acts on.

    **A known component always wins.** The shapes below are judged only when the
    head names no component in this graph, so a workspace that really does alias
    something oddly keeps working and gets the old answer.

    Both separators are recognised, because a seed is typed on one machine and
    read on any — the rule CLAUDE.md records after a vendored tool matched a
    backslash path on Windows and silently returned nothing.
    """
    head = seed.split("/", 1)[0]
    if head in known:
        return ""
    absolute = (head == ""                                # a POSIX root
                or "\\" in head                           # mixed separators
                or (len(head) == 2 and head[1] == ":"     # a drive letter
                    and head[0].isalpha()))
    if not absolute:
        return ""
    return (f"--seed is COMPONENT/RELPATH and {seed!r} is an absolute path - "
            f"write the component this graph knows, then the path INSIDE it "
            f"(known: {', '.join(sorted(known)) or 'none'})")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--graph-dir", required=True, metavar="DIR")
    ap.add_argument("--seed", metavar="COMPONENT/RELPATH")
    ap.add_argument("--lines", metavar="RANGES",
                    help="hunk ranges inside --seed's file (item 20)")
    ap.add_argument("--diff", metavar="FILE",
                    help="a unified diff; every touched file seeds (item 20)")
    ap.add_argument("--component", metavar="ALIAS",
                    help="the component the diff's paths are relative to")
    ap.add_argument("--out", required=True, metavar="DIR",
                    help="where pack.md / pack.json / pack.d2 are written — packs "
                         "are derived artifacts and belong in scratch, never in git")
    ap.add_argument("--budget", type=int, default=DEFAULT_BUDGET, metavar="TOKENS")
    ap.add_argument("--rules", metavar="FILE")
    ap.add_argument("--hops", type=int, default=None)
    ap.add_argument("--no-svg", action="store_true",
                    help="skip the d2 render (the .d2 source is still written)")
    args = ap.parse_args(argv)

    out_dir = Path(args.graph_dir)
    if not (out_dir / "graph.jsonl").exists():
        print(f"!! {out_dir / 'graph.jsonl'} does not exist — point --graph-dir at "
              f"a completed build's output directory")
        return 2
    if bool(args.seed) == bool(args.diff):
        print("!! exactly one of --seed and --diff")
        return 2
    if args.diff and not args.component:
        print("!! --diff needs --component")
        return 2
    if args.lines and not args.seed:
        print("!! --lines refines --seed's file")
        return 2
    if args.budget < 200:
        print(f"!! --budget {args.budget} cannot even hold the seed block — "
              f"200 is the floor")
        return 2

    try:
        nodes, edges = load_graph(out_dir)
        rules, rules_source = discover_rules(
            out_dir, Path(args.rules) if args.rules else None)
    except (GraphError, RulesError) as exc:
        print(f"!! {exc}")
        return 2

    if args.diff:
        diff_path = Path(args.diff)
        if not diff_path.exists():
            print(f"!! {diff_path} does not exist")
            return 2
        component = args.component
        targets = sorted(parse_diff(
            diff_path.read_text(encoding="utf-8", errors="replace")).items())
        if not targets:
            print(f"!! {diff_path} contains no file headers a unified diff would have")
            return 2
    else:
        if "/" not in args.seed:
            print("!! --seed must be COMPONENT/RELPATH")
            return 2
        # Item 128: judged BEFORE the split, so the message is about
        # the seed rather than about a component nobody wrote.
        if problem := seed_problem(args.seed, known_components(nodes)):
            print(f"!! {problem}")
            return 2
        component, path = args.seed.split("/", 1)
        lines = None
        if args.lines:
            try:
                lines = parse_lines(args.lines)
            except ValueError as exc:
                print(f"!! {exc}")
                return 2
        targets = [(path, lines)]

    pack = build_pack(nodes, edges, component, targets, hops=args.hops,
                      rules=rules, graph_dir=out_dir)
    if pack is None:
        print(f"!! component {component!r} is not in this graph\n"
              f"   components: {', '.join(sorted(known_components(nodes)))}")
        return 2
    pack["rules"] = rules_source

    dest = Path(args.out)
    dest.mkdir(parents=True, exist_ok=True)
    md = render_md(pack, budget=args.budget)   # also fills pack["budget"]
    d2_src = render_d2(pack)
    (dest / "pack.d2").write_text(d2_src, encoding="utf-8")
    if args.no_svg:
        pack["svg"] = "not rendered: --no-svg"
    else:
        pack["svg"] = _render_svg(dest / "pack.d2", dest / "pack.svg")
    (dest / "pack.md").write_text(md, encoding="utf-8")
    (dest / "pack.json").write_text(
        json.dumps(pack, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    counts = {name: len(rows) for name, rows in pack["sections"].items()}
    elided = pack["budget"]["elided"]
    print(f"pack: {dest / 'pack.md'} (~{pack['budget']['estimated_tokens']} tokens "
          f"of {args.budget}) + pack.json + pack.d2"
          + ("" if pack["svg"].endswith(".svg") else f"; svg {pack['svg']}"))
    print(f"  contracts: {len(pack['contracts'])} node(s); files: " +
          ", ".join(f"{k} {v}" for k, v in counts.items())
          + (f"; elided in md: {elided}" if elided else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
