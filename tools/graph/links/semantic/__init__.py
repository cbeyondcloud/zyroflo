"""The semantic layer: the two declared-fact classes a scanner cannot reach.

Item 79. Extraction answers what the files say; two things the impact engine needs
are not in any file. **Entry points**: a scanner finds every endpoint and cannot
tell which is the front door — the stamp changes how rankings read a node and lets
traversal treat its fan-out as designed rather than as a hub to attenuate.
**Domain aliases**: two components naming one business entity with no config seam
between them (a nightly ETL, a DB view, a convention) — invisible to extraction by
construction, because there is nothing to extract; the declared edge is the only
in-repo trace of the bridge.

The item's contract, enforced here: every declared fact changes a ranking or a
traversal — a declaration that matches nothing becomes an unresolved row naming
what missed, never a silent no-op — and every declared fact is DISTINGUISHABLE
from extraction: `props.declared` on the alias edge, `props.entrypoint` on the
stamp, and a citation into the declaration file itself (`workspace.toml:line`),
because a hand-declared edge that blends into extraction kills the provenance
guarantee exactly where a human started typing.

An alias joins REACH, never identity — `[[canonical]]` merges identity — and it
joins shared entities only: a side owned by a single component is refused as a
row, because service identity is canonical's job and config seams inside a
component are the extractors'. The ownership test mirrors the ladder impact walks
(props.component, then the id's `@component` tail), so "would this edge mint a
file row in someone else's component" and "is this side owned" are the same
question answered the same way.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from tools.graph.core.model import Edge, ExtractResult, Node, Provenance

#: What each `kind` this module emits promises beyond the common fields.
UNRESOLVED_FIELDS = {
    "entrypoint-unmatched": ("declared_node",),
    "alias-unmatched": ("declared_node", "side"),
    "alias-owned-node": ("declared_node", "owner"),
}


def _owner(node_id: str, props_owner: dict[str, str]) -> str:
    """The component a node belongs to, by the same ladder impact's node_owner
    walks: an explicit props.component first, then an `@component` id tail."""
    if node_id in props_owner:
        return props_owner[node_id]
    tail = node_id.split(":", 1)[1] if ":" in node_id else node_id
    if "@" in tail:
        return tail.rsplit("@", 1)[1]
    return ""


class SemanticPass:
    """Stamp declared entry points; join declared aliases; refuse loudly."""

    name = "mark-semantic"
    #: An `[[entrypoint]]` or `[[alias]]` can name a node only `join-by-name` puts in the live
    #: list (T534, D534-17), so this pass runs after it by declaration, not by the name order's
    #: tie-break, and an `--only` naming it without the join is refused (C534-6).
    REQUIRES = ("join-by-name",)

    def link(self, ctx: Any, nodes: list[Node], edges: list[Edge]) -> ExtractResult:
        res = ExtractResult()
        entrypoints = ctx.config.get("entrypoint", []) or []
        aliases = ctx.config.get("alias", []) or []
        if not entrypoints and not aliases:
            return res

        ids = {n.id for n in nodes}
        props_owner: dict[str, str] = {}
        for n in nodes:
            if n.props.get("component") and n.id not in props_owner:
                props_owner[n.id] = str(n.props["component"])

        for d in entrypoints:
            if d["node"] not in ids:
                res.unresolved.append({
                    "extractor": self.name, "component": "",
                    "kind": "entrypoint-unmatched", "declared_node": d["node"],
                    "source_file": d["file"], "line": d["line"],
                    "why": (f"the [[entrypoint]] declaration names {d['node']!r}, "
                            f"which is not a node of this graph — a front door "
                            f"declared with a typo is a front door still undeclared. "
                            f"Find the id with search_nodes or the graph listings")})
                continue
            # graph.jsonl keeps one node row per emitting extractor; stamp every
            # row carrying the id, or the flag would depend on which row a
            # reader happened to pick.
            for i, n in enumerate(nodes):
                if n.id == d["node"]:
                    stamped = {**n.props, "entrypoint": True}
                    if d["note"]:
                        stamped["entrypoint_note"] = d["note"]
                    nodes[i] = replace(n, props=stamped)

        for d in aliases:
            missing = [side for side in ("a", "b") if d[side] not in ids]
            if missing:
                for side in missing:
                    res.unresolved.append({
                        "extractor": self.name, "component": "",
                        "kind": "alias-unmatched", "declared_node": d[side],
                        "side": side,
                        "source_file": d["file"], "line": d["line"],
                        "why": (f"the [[alias]] declaration's `{side}` names "
                                f"{d[side]!r}, which is not a node of this graph — "
                                f"an alias cannot mint an entity, only join two "
                                f"that exist")})
                continue
            owned = [(side, _owner(d[side], props_owner)) for side in ("a", "b")
                     if _owner(d[side], props_owner)]
            if owned:
                side, owner = owned[0]
                res.unresolved.append({
                    "extractor": self.name, "component": owner,
                    "kind": "alias-owned-node", "declared_node": d[side],
                    "owner": owner,
                    "source_file": d["file"], "line": d["line"],
                    "why": (f"the [[alias]] declaration's `{side}` names "
                            f"{d[side]!r}, which belongs to component {owner!r}. "
                            f"Aliases join SHARED entities (topics, tables, "
                            f"context-free endpoints); for service identity use "
                            f"[[canonical]], and config seams inside a component "
                            f"are the extractors' job")})
                continue
            props: dict = {"declared": True}
            if d["note"]:
                props["note"] = d["note"]
            res.edges.append(Edge(
                src=d["a"], type="DG_ALIAS_OF", dst=d["b"],
                source_file=d["file"], line=d["line"],
                extractor=self.name, confidence=1.0,
                provenance=Provenance.EXTRACTED, props=props))
        return res


LINK_PASSES = (SemanticPass(),)
