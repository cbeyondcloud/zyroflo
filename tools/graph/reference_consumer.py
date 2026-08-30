"""The reference consumer — item 54: the OKF bundle, read by something outside.

`export_okf.py` claims a bundle is usable with no Neo4j, no MCP, no harness and no
network. This is that claim as a program: it opens a bundle, parses the markdown
itself, follows the cross-links, and answers a real question — *what does changing
this touch?* — from the files alone.

**It imports nothing from the harness.** Not `tools.cli`, not the model, not the
graph package it sits beside. A consumer that quietly reached into `tools/` would
prove nothing about a consumer that cannot, which is exactly the argument
`reference_runner.py` makes for adapters and the reason that rule is enforced by a
test rather than remembered.

**What it proves, and what it does not.** It proves the FORMAT is consumable: the
frontmatter parses, the links resolve, the provenance survives, and a blast radius
computed from markdown alone matches what the graph would say. It proves nothing
about an agent — there is no model here and it makes no judgements. A green run of
this is a **green FORMAT, never a green agent**, which is the same distinction
`RUNNER_CONTRACT` §8 draws for the reference runner.

M6's C1 asks for a bundle consumed by a non-Claude *agent*. This is not that, and
does not pretend to be. Its job is to make that demonstration cheap and to make its
failure meaningful: when a foreign agent is finally pointed at a bundle, a failure
says something about the agent, because the path it walks has already been walked.

**Deliberately naive.** It re-implements frontmatter and link parsing in thirty
lines of standard library rather than reaching for a YAML or markdown package,
because "any tool can read this" is a claim about the format, and a consumer that
needed three dependencies to make it would be evidence against itself.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

#: This module is an OUTSIDE PROCESS and must import nothing from the harness.
#: Declared here rather than listed in a test, so adding another such module is a
#: line in the module itself instead of a roster somebody has to remember - the
#: shape that let one CLI list go eighteen entries stale.
OUTSIDE_PROCESS = True

# The console rule, hand-rolled rather than `cli.use_utf8()` - which is the whole
# reason modules declaring OUTSIDE_PROCESS are exempt from the sweep that forbids
# copies of this block. It is not optional here: this prints node ids and
# `source_file:line` citations taken straight from somebody else's repository, so
# a path outside the console's codepage would make the render itself raise. The
# guard caught this module having no console rule at all.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

NODES_DIR = "nodes"
INDEX = "README.md"

_FM = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.S)
_FM_LINE = re.compile(r"^([A-Za-z0-9_]+):\s*(.*)$")
#: `- **TYPE** → [id](file.md)` then an indented citation line. Written against the
#: shape the index describes, not against the exporter's source.
_EDGE = re.compile(r"^-\s+\*\*(?P<type>[^*]+)\*\*\s*(?:→|->)\s*"
                   r"(?:\[(?P<label>[^\]]*)\]\((?P<link>[^)]+)\)|`(?P<bare>[^`]+)`)")
_CITE = re.compile(r"^\s+`(?P<at>[^`]*)`\s*·\s*(?P<prov>\w+)\s*·\s*"
                   r"confidence\s*(?P<conf>[0-9.]+)\s*·\s*extractor\s*`(?P<ext>[^`]*)`")


def _unquote(raw: str) -> object:
    raw = raw.strip()
    if raw.startswith('"') and raw.endswith('"'):
        try:
            return json.loads(raw)
        except ValueError:
            return raw[1:-1]
    if raw in ("true", "false"):
        return raw == "true"
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        return raw


def read_page(path: Path) -> dict:
    """One node page as `{fields, edges}`. Parsed, not imported."""
    text = path.read_text(encoding="utf-8")
    fields: dict[str, object] = {}
    m = _FM.match(text)
    if m:
        for line in m.group(1).splitlines():
            f = _FM_LINE.match(line)
            if f:
                fields[f.group(1)] = _unquote(f.group(2))

    edges, lines = [], text.splitlines()
    for i, line in enumerate(lines):
        e = _EDGE.match(line)
        if not e:
            continue
        cite = _CITE.match(lines[i + 1]) if i + 1 < len(lines) else None
        edges.append({
            "type": e.group("type").strip(),
            "target": (e.group("label") or e.group("bare") or "").strip(),
            "link": e.group("link"),
            "at": cite.group("at") if cite else "",
            "provenance": cite.group("prov") if cite else "",
            "confidence": float(cite.group("conf")) if cite else None,
            "extractor": cite.group("ext") if cite else "",
        })
    return {"fields": fields, "edges": edges, "path": path}


def load(bundle: Path) -> dict[str, dict]:
    """Every node in the bundle, keyed by its declared id.

    **A page whose frontmatter has no `id` is REFUSED, not skipped.** A bundle with
    unreadable pages is not a smaller bundle: a consumer that quietly dropped them
    would answer a blast-radius question over part of the graph and present it as
    the whole one, which is the failure the format exists to make impossible.
    """
    bundle = Path(bundle)
    folder = bundle / NODES_DIR
    if not folder.is_dir():
        raise SystemExit(f"{folder} does not exist - this is not an OKF bundle. "
                         f"A missing directory is not an empty graph.")
    out, broken = {}, []
    for path in sorted(folder.glob("*.md")):
        page = read_page(path)
        node_id = page["fields"].get("id")
        if not isinstance(node_id, str) or not node_id:
            broken.append(path.name)
            continue
        out[node_id] = page
    if broken:
        raise SystemExit(f"{len(broken)} page(s) carry no id and cannot be placed "
                         f"in the graph: {', '.join(broken[:5])}")
    if not out:
        raise SystemExit(f"{folder} holds no node pages - refusing to answer over "
                         f"an empty graph as though it were a workspace with "
                         f"nothing in it")
    return out


def touches(nodes: dict[str, dict], start: str, hops: int = 2) -> list[dict]:
    """What changing `start` reaches, breadth-first, with the path that got there.

    Every row carries the citation the bundle gave it, because a blast radius a
    human cannot check is one they should not act on - which is the whole reason
    the provenance is in the file rather than in a database somewhere.
    """
    if start not in nodes:
        raise SystemExit(f"{start!r} is not in this bundle. "
                         f"{len(nodes)} node(s) are; try --list.")
    seen = {start}
    frontier = [(start, 0)]
    found: list[dict] = []
    while frontier:
        current, depth = frontier.pop(0)
        if depth >= hops:
            continue
        for edge in nodes[current]["edges"]:
            target = edge["target"]
            if not target or target in seen:
                continue
            seen.add(target)
            found.append({"id": target, "hop": depth + 1, "via": current,
                          "type": edge["type"], "at": edge["at"],
                          "provenance": edge["provenance"],
                          "confidence": edge["confidence"],
                          "extractor": edge["extractor"]})
            if target in nodes:
                frontier.append((target, depth + 1))
    found.sort(key=lambda r: (r["hop"], r["id"]))
    return found


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--bundle", required=True, metavar="DIR")
    ap.add_argument("--changed", metavar="NODE_ID",
                    help="the node whose blast radius to compute")
    ap.add_argument("--hops", type=int, default=2, metavar="N")
    ap.add_argument("--list", action="store_true", help="every node id")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    nodes = load(Path(args.bundle))
    if args.list or not args.changed:
        ids = sorted(nodes)
        if args.json:
            print(json.dumps({"nodes": ids}, indent=2))
        else:
            print(f"{len(ids)} node(s) in this bundle:")
            for nid in ids:
                print(f"  {nid}")
        return 0

    rows = touches(nodes, args.changed, args.hops)
    if args.json:
        print(json.dumps({"changed": args.changed, "hops": args.hops,
                          "touches": rows}, indent=2, sort_keys=True))
    else:
        print(f"changing {args.changed} touches {len(rows)} node(s) "
              f"within {args.hops} hop(s):")
        for r in rows:
            print(f"  [{r['hop']}] {r['id']}")
            print(f"      via {r['via']} --{r['type']}--> "
                  f"({r['at'] or 'no citation'}, {r['provenance'] or '?'}, "
                  f"confidence {r['confidence']})")
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        return _main(argv)
    except SystemExit:
        raise
    except Exception as exc:                    # noqa: BLE001 - a door, not a guess
        print(f"the bundle could not be read: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
