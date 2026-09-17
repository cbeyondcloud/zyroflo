"""Export the graph as an OKF-shaped bundle — the third access tier. Item 54.

The plan names three tiers, widest last: **MCP tools > Python CLIs > OKF bundle**.
The first two need this harness running. This one does not: a directory of markdown
files with YAML frontmatter, where the cross-links ARE the graph. No Neo4j, no MCP,
no Python, no network — a consumer that can read a file and follow a relative link
has the domain knowledge.

That is the whole point, and it is why the bundle is plain text rather than
anything cleverer: *"format, not platform."*

**What is claimed, and what is not.** The OKF spec (Google, v0.1 Jun-2026 / v0.2
Jul-2026) is **not vendored in this repository and was not available when this was
written**, so nothing here asserts verified conformance to the published document.
What this produces is the shape the plan's own decision record describes —
*"knowledge = directory of markdown files with YAML frontmatter; markdown
cross-links form the graph"* — and `BUNDLE_SHAPE` states it in the bundle itself so
a reader can check the claim rather than trust it. Saying "OKF-conformant" without
the spec in hand would be a claim nobody could check, which this project has
already paid for twice.

**Every edge carries its provenance into the bundle.** `source_file:line`, the
extractor, the confidence, and whether the fact was EXTRACTED, INFERRED or
AMBIGUOUS. A projection that dropped provenance would be exactly the confident,
uncheckable artifact the graph exists to avoid — a human asked to trust a row with
nothing behind it.

**A dangling endpoint stays visible.** An edge whose target no extractor produced
becomes a node file that says so, never a link quietly removed. Zero silent drops,
carried into the export.

**Deterministic.** Same graph in, byte-identical bundle out: sorted everywhere, no
run id, no timestamp. The committed-snapshot rule, applied here so a bundle can be
diffed in a pull request like anything else.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import cli  # noqa: E402
from tools.graph.impact import GraphError, load_graph  # noqa: E402

SCHEMA_VERSION = 1

#: What this bundle claims to be, written into the bundle. The published OKF spec
#: is not vendored here, so this names the SHAPE rather than asserting conformance
#: to a document nobody in this repository can check against.
BUNDLE_SHAPE = "okf-shaped/0.2-draft"

NODES_DIR = "nodes"
INDEX = "README.md"
MANIFEST = "bundle.json"

#: Characters a filename may keep. Everything else becomes `-`. Deliberately
#: narrow: node ids carry `:`, `/` and `@`, and Windows refuses several of them.
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")

#: Windows refuses these as filenames whatever the extension, case-insensitively.
_RESERVED = {"con", "prn", "aux", "nul",
             *(f"com{i}" for i in range(1, 10)),
             *(f"lpt{i}" for i in range(1, 10))}


def filename_for(node_id: str) -> str:
    """A deterministic, collision-free, Windows-safe filename for one node id.

    **The hash suffix is not decoration.** Slugging alone collides - `svc:a/b` and
    `svc:a-b` both become `svc-a-b`, and two nodes sharing a file is a graph that
    lost an entity silently. The suffix is over the FULL id, so two ids can differ
    anywhere and still land in different files.

    A trailing dot or space is stripped because Windows silently drops them, which
    would make the written name differ from the linked one.
    """
    slug = _SAFE.sub("-", node_id).strip("-.") or "node"
    if slug.lower() in _RESERVED:
        slug = f"n-{slug}"
    digest = hashlib.sha1(node_id.encode("utf-8")).hexdigest()[:10]
    return f"{slug[:80]}.{digest}.md"


def _yaml_scalar(v: Any) -> str:
    """One frontmatter value. JSON for anything not a plain scalar, because YAML
    written by hand is where a projection starts lying about its own contents."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    text = str(v)
    if v is None:
        return '""'
    if not isinstance(v, str):
        text = json.dumps(v, sort_keys=True, ensure_ascii=False)
    return json.dumps(text, ensure_ascii=False)      # always quoted, always valid


def _frontmatter(fields: dict[str, Any]) -> str:
    lines = ["---"]
    for key in sorted(fields):
        lines.append(f"{key}: {_yaml_scalar(fields[key])}")
    lines.append("---")
    return "\n".join(lines)


#: How a MISSING confidence is written into a citation — a stated absence, never
#: the string "None". `_provenance` used `edge.get('confidence', 1.0)`, so a
#: present-but-null confidence (`.get` returns the default only when a key is
#: ABSENT) rendered `confidence None`, which the reference consumer's citation
#: regex cannot match — silently dropping the row's source, provenance and
#: extractor on the way through. The consumer accepts this exact token and reads it
#: back as `confidence: None`; `test_reference_consumer` welds the two ends.
CONFIDENCE_UNRECORDED = "unrecorded"


def _provenance(edge: dict) -> str:
    """One edge's citation. Everything a reader needs to go and check it."""
    where = str(edge.get("source_file") or "")
    line = edge.get("line")
    at = f"{where}:{line}" if where and line else (where or "(no source recorded)")
    conf = edge.get("confidence")
    return (f"`{at}` · {edge.get('provenance', 'EXTRACTED')} · "
            f"confidence {CONFIDENCE_UNRECORDED if conf is None else conf} · "
            f"extractor `{edge.get('extractor', '?')}`")


def node_page(node: dict, out_edges: list[dict], in_edges: list[dict],
              names: dict[str, str], stub: bool) -> str:
    """One node as markdown. The links ARE the graph."""
    nid = node["id"]
    fields = {"id": nid, "type": node.get("type", "unknown"),
              "okf_shape": BUNDLE_SHAPE}
    if stub:
        # An endpoint no extractor produced. Said out loud rather than omitted:
        # an unresolved reference is a coverage finding somebody may need to act
        # on, and a bundle that hid it would look more complete than the graph is.
        fields["unresolved"] = True
    for key, val in sorted((node.get("props") or {}).items()):
        if key not in fields:
            fields[f"prop_{key}"] = val

    body = [_frontmatter(fields), "", f"# {nid}", ""]
    if stub:
        body += ["> **Unresolved.** Something references this, and no extractor "
                 "produced it. It is here because a reference nobody could resolve "
                 "is a finding, not a row to drop.", ""]

    for title, edges, other in (("## Outgoing", out_edges, "dst"),
                                ("## Incoming", in_edges, "src")):
        if not edges:
            continue
        body += [title, ""]
        for e in edges:
            target = e[other]
            link = names.get(target)
            shown = f"[{target}]({link})" if link else f"`{target}` (not in bundle)"
            body.append(f"- **{e.get('type', '?')}** → {shown}  \n  {_provenance(e)}")
        body.append("")

    if not out_edges and not in_edges:
        body += ["*No relationships recorded for this node.*", ""]
    return "\n".join(body)


def build_bundle(nodes: list[dict], edges: list[dict]) -> dict[str, str]:
    """`{relative_path: content}` for the whole bundle. Pure, so a test can read
    it without touching a filesystem and a caller can diff two of them."""
    # **The same node id can appear more than once in a merged graph**, with
    # different props: two extractors produce the same entity and one knows more
    # about it. Measured on a real fixture - one endpoint arrived twice, once with
    # a `path` prop and once with none.
    #
    # Merging is not tidiness. Letting the last row win drops whatever the others
    # knew, and the empty one is as likely to be last as the full one, so the
    # bundle would lose a fact depending on iteration order. Union the props, and
    # prefer a real type over `unknown`.
    merged: dict[str, dict] = {}
    for n in nodes:
        seen = merged.setdefault(n["id"], {"id": n["id"], "type": "unknown",
                                           "props": {}})
        if str(n.get("type") or "unknown") != "unknown":
            seen["type"] = n["type"]
        for key, val in (n.get("props") or {}).items():
            if val is not None and val != "":
                seen["props"][key] = val

    known = set(merged)
    stubs = sorted({e[k] for e in edges for k in ("src", "dst")} - known)
    every = [merged[k] for k in sorted(merged)] + [
        {"id": s, "type": "unknown", "props": {}} for s in stubs]

    # Two spellings, because they are read from two places. A node page lives IN
    # `nodes/`, so its links to siblings are BARE filenames - prefixing them with
    # `nodes/` resolves to `nodes/nodes/...` and every cross-link in the bundle is
    # dead, which is the one property this format exists for. The index sits at the
    # root and needs the prefix.
    paths = {n["id"]: f"{NODES_DIR}/{filename_for(n['id'])}" for n in every}
    names = {n["id"]: filename_for(n["id"]) for n in every}
    out_by: dict[str, list[dict]] = {}
    in_by: dict[str, list[dict]] = {}
    for e in sorted(edges, key=lambda e: (e["src"], e.get("type", ""), e["dst"],
                                          str(e.get("source_file", "")),
                                          e.get("line", 0))):
        out_by.setdefault(e["src"], []).append(e)
        in_by.setdefault(e["dst"], []).append(e)

    files: dict[str, str] = {}
    stub_ids = set(stubs)
    for n in every:
        files[paths[n["id"]]] = node_page(
            n, out_by.get(n["id"], []), in_by.get(n["id"], []), names,
            n["id"] in stub_ids)

    by_type: dict[str, int] = {}
    for n in every:
        t = str(n.get("type", "unknown"))
        by_type[t] = by_type.get(t, 0) + 1
    edge_types: dict[str, int] = {}
    for e in edges:
        t = str(e.get("type", "?"))
        edge_types[t] = edge_types.get(t, 0) + 1

    files[MANIFEST] = json.dumps({
        "schema_version": SCHEMA_VERSION, "okf_shape": BUNDLE_SHAPE,
        "nodes": len(every), "edges": len(edges), "unresolved": len(stubs),
        "nodes_by_type": dict(sorted(by_type.items())),
        "edges_by_type": dict(sorted(edge_types.items())),
    }, indent=2, sort_keys=True) + "\n"

    files[INDEX] = "\n".join([
        _frontmatter({"okf_shape": BUNDLE_SHAPE, "nodes": len(every),
                      "edges": len(edges), "unresolved": len(stubs)}),
        "", "# Domain knowledge bundle", "",
        "A directory of markdown files whose cross-links are the graph. **Nothing "
        "is needed to read it** — no database, no server, no Python, no network. "
        "Open a file, follow a link.", "",
        "Every relationship carries where it came from: `source_file:line`, the "
        "extractor that found it, a confidence, and whether it was EXTRACTED, "
        "INFERRED or AMBIGUOUS. A row you are asked to trust is a row you can go "
        "and check.", "",
        f"- **{len(every)}** nodes, **{len(edges)}** relationships",
        f"- **{len(stubs)}** unresolved — referenced by something, produced by no "
        f"extractor, and kept visible rather than dropped", "",
        "## Nodes by type", "",
        *[f"- `{t}` — {n}" for t, n in sorted(by_type.items())], "",
        "## Relationships by type", "",
        *[f"- `{t}` — {n}" for t, n in sorted(edge_types.items())], "",
        "---", "",
        f"*Shape: `{BUNDLE_SHAPE}`. The published OKF specification is not "
        f"vendored in the harness that produced this, so this bundle states the "
        f"shape it targets rather than asserting verified conformance to a "
        f"document it could not check itself against.*", "",
    ])
    return files


def write_bundle(files: dict[str, str], where: Path) -> int:
    where = Path(where)
    (where / NODES_DIR).mkdir(parents=True, exist_ok=True)
    for rel, content in sorted(files.items()):
        path = where / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
    return len(files)


def export(out_dir: Path, where: Path) -> dict:
    """Read a built graph and write the bundle. Returns what went into it."""
    out_dir, where = Path(out_dir), Path(where)
    # The caller checks the graph EXISTS - `GraphError`'s own recorded convention,
    # and the one this package's newest caller got wrong once already.
    if not (out_dir / "graph.jsonl").is_file():
        raise GraphError(
            f"{out_dir / 'graph.jsonl'} does not exist - there is no graph here to "
            f"export. Build one (tools/graph/build.py) rather than writing an "
            f"empty bundle somebody will read as a workspace with nothing in it")
    nodes, edges = load_graph(out_dir)
    files = build_bundle(nodes, edges)
    written = write_bundle(files, where)
    manifest = json.loads(files[MANIFEST])
    return {"files": written, **{k: manifest[k]
                                 for k in ("nodes", "edges", "unresolved")}}


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--graph", required=True, metavar="DIR",
                    help="a build's output directory, holding graph.jsonl")
    ap.add_argument("--out", required=True, metavar="DIR",
                    help="where to write the bundle")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    found = export(Path(args.graph), Path(args.out))
    if args.json:
        print(json.dumps(found, indent=2, sort_keys=True))
    else:
        print(f"bundle: {found['files']} file(s) in {args.out}")
        print(f"  {found['nodes']} node(s), {found['edges']} relationship(s)")
        print(f"  {found['unresolved']} unresolved, kept visible")
    return 0


def main(argv: list[str] | None = None) -> int:
    """The door. `2` is could-not-look, matching every other graph CLI."""
    cli.use_utf8()
    return cli.door(_main, argv, "the OKF export", 2)


if __name__ == "__main__":
    raise SystemExit(main())
