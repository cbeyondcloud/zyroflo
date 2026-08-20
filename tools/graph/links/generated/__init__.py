"""Generated code: the declaration changes what facts mean, never whether they exist.

Item 78. Codegen committed into a source tree is where a graph gives its most
confidently wrong advice: a generated client's annotations are perfectly regular, so
extraction reads them beautifully and then names a file no human wrote or should edit.
Excluding the tree would be worse — the couplings its facts assert are REAL, and a
silent drop is this project's cardinal sin. So a declared tree is still read, and every
fact extracted from under it is stamped: `generated: true`, plus `regenerate_from` when
the workspace names the generator's input — the place a human actually makes the change.

Measured before designed, on a real workspace: 577 files carry generated-by markers
across 21 of 26 components; ZERO follow the classic name conventions (`*_pb2.py`,
`*.pb.go`, `.min.js`), so a name-based detector would have found nothing; and the graph
facts citing marker files are dominated by generated deployment config (758 of 931),
not client code. Hence: **declared, never detected** — a DO-NOT-EDIT header is a
convention some generators skip and some humans paste, while a `[[generated]]` block is
a statement of intent. And hence the stamp reaches nodes and config facts, not only
code edges.

The stamp deliberately changes neither confidence nor canonicality: a generated fact is
not a doubtful fact, it is a fact whose EDIT SITE is elsewhere. Consumers read the
props — impact renders "regenerate, don't edit" instead of pointing a human at
generator output; the coverage gate and review-diff folding (P5/P6 items) will read the
same flag when they land.

A link pass rather than an extractor concern: stamping per extractor would make every
extractor — current and future — remember to consult the declarations, and the one that
forgot would be invisible. Here it happens once, over the pooled facts, in place.
Additive edges returned by LATER passes are not re-visited; today every such edge cites
a spec file — the generator's INPUT side, which is exactly the side a human edits — so
nothing is missed. A pass that starts adding edges FROM generated files must run before
this one.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from tools.graph.core.model import Edge, ExtractResult, Node

#: What each `kind` this module emits promises beyond the common fields.
UNRESOLVED_FIELDS = {
    "generated-unmatched": ("declared_path",),
}


def _under(rel: str, tree: str) -> bool:
    """Whether `rel` lies under `tree`, at a segment boundary.

    `src/gen` covers `src/gen/Client.java` and `src/gen` itself, and does NOT cover
    `src/gencode.java` — a bare prefix match would stamp a sibling that merely shares
    letters, silently changing what its facts mean.
    """
    return rel == tree or rel.startswith(tree + "/")


class GeneratedPass:
    """Stamp every fact extracted from under a declared generated tree."""

    name = "mark-generated"

    def link(self, ctx: Any, nodes: list[Node], edges: list[Edge]) -> ExtractResult:
        res = ExtractResult()
        decls = ctx.config.get("generated", []) or []
        if not decls:
            return res

        nodes_by_id = {n.id: n for n in nodes}
        by_component: dict[str, list[dict]] = {}
        for d in decls:
            by_component.setdefault(d["component"], []).append(d)
        matched: set[tuple[str, str]] = set()

        def decl_for(component: str, rel: str) -> dict | None:
            for d in by_component.get(component, ()):
                if _under(rel, d["path"]):
                    return d
            return None

        def owner(edge: Edge) -> str:
            """The component whose file the edge cites — `source_file` is relative to
            the extracting component, so ownership follows the same ladder impact
            walks: the edge's own claim first, then its endpoints'."""
            c = edge.props.get("component")
            if c:
                return str(c)
            for end in (edge.src, edge.dst):
                n = nodes_by_id.get(end)
                if n is not None and n.props.get("component"):
                    return str(n.props["component"])
            return ""

        def stamped(props: dict, decl: dict) -> dict:
            out = {**props, "generated": True}
            if decl["from"]:
                out["regenerate_from"] = decl["from"]
            return out

        for i, node in enumerate(nodes):
            comp, path = node.props.get("component"), node.props.get("path")
            if comp and path and (decl := decl_for(str(comp), str(path))):
                nodes[i] = replace(node, props=stamped(node.props, decl))
                matched.add((decl["component"], decl["path"]))
        for i, edge in enumerate(edges):
            comp = owner(edge)
            if comp and (decl := decl_for(comp, edge.source_file)):
                edges[i] = replace(edge, props=stamped(edge.props, decl))
                matched.add((decl["component"], decl["path"]))

        for d in decls:
            if (d["component"], d["path"]) not in matched:
                res.unresolved.append({
                    "extractor": self.name, "component": d["component"],
                    "kind": "generated-unmatched", "declared_path": d["path"],
                    "source_file": "", "line": 0,
                    "why": (f"the [[generated]] declaration covering "
                            f"{d['component']}/{d['path']} stamped no fact — either the "
                            f"path is misspelled, or nothing under it produces graph "
                            f"facts. A declaration that silently does nothing is how a "
                            f"typo becomes a generated tree treated as hand-written")})
        return res


LINK_PASSES = (GeneratedPass(),)
