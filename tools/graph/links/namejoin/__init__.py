"""Join a claim that names its service by name alone, where the estate already has that name.

T534 (the owner's ruling 1). A deployment package that only NAMES a service - a compose file in a
component that builds nothing, `orders: image: org/orders:1` - says nothing about which of
the workspace's services it means. Its extractor claims it on its own component's node and
marks the claim; this pass moves the claim onto the estate's node for that name only where
the estate already has one:

- **(a) a declaration is already there** - a chart names the service, or a compose file
  builds a component under that name. Two packages now claim one node, so the canonical
  pass sees the collision T534 is about. A claim that is itself only a name - a compose
  file naming a service after its own component - is not a declaration and does not count
  (D534-16); or
- **(b) a component carries the name.** The package and that component's node now share a
  node, which is what lets impact analysis reach a deployment from the code it runs.

Otherwise the claim stays where it is, as it always did. Two marked claims never meet each
other directly: three deployment repositories each running a stock `db` are three
databases (D9-2). They DO meet on a component's node when a component carries the name,
each with its own row and, between them, a contest (D534-22).

## A name is not a declaration

The join is recorded as INFERRED - confidence stays 1.0, since the fact that the file
deploys a service named so is certain; which service that is, is the inference. A service
joined by (b) alone gets ONE row (ruling 6), naming every package that joined it - a file
two nested components read counts once: nothing in those files says they are that
component's service, and whether the component "has code" is not something the edge set
can answer (D534-19). The row stops only when a `[[canonical]]` block DECIDES the service
(ruling 7): its owner, or its owner with a path, selects exactly one package by
`mark-canonical`'s own rule, so following the row both silences it and ranks the
deployments. The row offers only such choices (`canonical.choices`), and a block that
decides nothing - an owner that reads two of the packages, or the component the name
belongs to - leaves it standing.

## Why a link pass

The join depends on other components' files - a chart in another repository - so it
cannot be an extractor's decision without making a component's facts depend on which
others a build read (ruling 3). Shards keep the claim as extracted; `--from-snapshot`
recomputes the join, as it does every derived fact - which also means a load missing the
shard that held the meeting point joins differently (D534-26).

The target node goes into the LIVE node list, not the returned result (D534-17): the pipeline
merges returned nodes only after every pass has run, so a later pass - `mark-semantic`
checking a declared `[[entrypoint]]` - would not see it, and a node already there would
gain a second record with different props.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from tools.graph.core.lib import ids
from tools.graph.core.model import Edge, ExtractResult, Node, Provenance
from tools.graph.links import canonical

#: The edge type that carries a claim on a service.
CLAIM_TYPE = "DG_DEPLOYS"

#: The mark an extractor puts on a claim that names its service by name alone.
JOIN_PROP = "join"

#: What each `kind` this module emits promises its reader beyond `UNRESOLVED_COMMON`.
UNRESOLVED_FIELDS = {
    "name-joined-service": ("service", "target", "claims", "components", "choices"),
}


def _logical(service_id: str) -> str:
    return service_id.split(":", 1)[-1].split("@", 1)[0]


class NameJoinPass:
    """Move a name-only claim onto the estate's node for that name, where one exists."""

    name = "join-by-name"

    def link(self, ctx: Any, nodes: list[Node], edges: list[Edge]) -> ExtractResult:
        res = ExtractResult()
        marked = [i for i, e in enumerate(edges)
                  if e.type == CLAIM_TYPE and e.props.get(JOIN_PROP)]
        if not marked:
            return res
        config = ctx.config or {}
        components = {ids.component_service_id(str(c["name"])): str(c["name"])
                      for c in config.get("components") or []}
        # Meeting point (a): claims that DECLARE the name - a chart's, or a build's. Never
        # one placed by name equality alone (D534-16).
        declared = {e.dst for e in edges if e.type == CLAIM_TYPE
                    and not e.props.get(JOIN_PROP) and e.props.get("mapped_by") != "name"}
        declarer = {n.id: str(n.props.get("component", "")) for n in nodes
                    if n.type == "deploy"}
        present = {n.id for n in nodes}

        moved: set[str] = set()
        #: Per target joined by (b) alone, its claims - one row per SERVICE (ruling 6).
        by_component: dict[str, list[tuple[int, Edge]]] = {}
        for i in marked:
            edge = edges[i]
            props = {k: v for k, v in edge.props.items() if k != JOIN_PROP}
            target = ids.component_service_id(str(props.get("service", "")))
            by = ("claim" if target in declared else
                  "component" if target in components else "")
            if not by or target == edge.dst:
                edges[i] = replace(edge, props=props)
                continue
            component = components.get(target, "")
            edges[i] = replace(edge, dst=target, provenance=Provenance.INFERRED, props={
                **props, "component": component, "mapped_by": "name", "joined_by": by})
            moved.add(edge.dst)
            if target not in present:
                nodes.append(Node(id=target, type="svc", props={"component": component}))
                present.add(target)
            if by == "component":
                by_component.setdefault(target, []).append((i, edges[i]))
        if moved:
            live = {x for e in edges for x in (e.src, e.dst)}
            nodes[:] = [n for n in nodes if n.id not in moved or n.id in live]
        self._rows(res, config, nodes, edges, by_component, components)
        return res

    def _rows(self, res: ExtractResult, config: dict, nodes: list[Node], edges: list[Edge],
              by_component: dict[str, list[tuple[int, Edge]]],
              components: dict[str, str]) -> None:
        """One `name-joined-service` row per service joined by a component's name alone,
        naming each PACKAGE once (ruling 6): a file two nested components read is one
        package, as `mark-canonical` counts it, and its most specific reader names it.

        It stands until a block DECIDES the service (ruling 7), judged over every package
        claiming the node - the joined ones and any other - exactly as `mark-canonical`
        will judge it. One row, two frames, as the contested row (D534-14): `claims` are
        qualified by component, `source_file` is a component-relative path beside an empty
        `component` - read the claimants from `claims`."""
        if not by_component:
            return              # the common case: nothing joined by a name alone (D534-60)
        nodes_by_id = {n.id: n for n in nodes}
        layout = canonical.workspace_layout(config)
        # Every claim on each joined node, indexed in ONE pass over the edges (D534-47): a
        # scan per joined service made the pass the product of the two counts.
        on_node: dict[str, dict[str, list[tuple[int, Edge]]]] = {t: {} for t in by_component}
        for i, edge in enumerate(edges):
            if edge.type == CLAIM_TYPE and edge.dst in on_node:
                on_node[edge.dst].setdefault(canonical.package_of(edge, nodes_by_id, layout),
                                             []).append((i, edge))
        for target, joined in sorted(by_component.items()):
            every = on_node[target]
            decided, _ = canonical.select_package(every, canonical.decl_for(config, target),
                                                  nodes_by_id)
            if decided is not None:
                continue
            packages: dict[str, list[tuple[int, Edge]]] = {}
            for i, edge in joined:
                packages.setdefault(canonical.package_of(edge, nodes_by_id, layout),
                                    []).append((i, edge))
            standing = [canonical.representative(group, nodes_by_id, layout)
                        for group in packages.values()]
            deployers = sorted({canonical.component_of(e, nodes_by_id) for e in standing}
                               - {""})
            cited = sorted(f"{canonical.where_of(e, nodes_by_id)}:{e.line}" for e in standing)
            # Judged over every package, offered for the CITED ones only (D534-49): a choice
            # for a package the row never names would rank a deployment its reader never saw.
            by_package = canonical.choices_by_package(every, nodes_by_id, layout)
            offered = [by_package[key] for key in sorted(packages)]
            others = len(every) - len(packages)
            logical = _logical(target)
            n = len(standing)
            res.unresolved.append({
                "extractor": self.name, "kind": "name-joined-service", "component": "",
                "components": deployers, "claims": cited, "choices": offered,
                "service": logical, "target": target,
                "source_file": sorted(e.source_file for e in standing)[0], "line": 0,
                "why": (f"{n} deployment package{'s' if n > 1 else ''} "
                        f"name{'' if n > 1 else 's'} `{logical}` and nothing more, and "
                        f"component `{components.get(target, '')}` carries that name, so the "
                        f"graph joins them (INFERRED): {'each is' if n > 1 else 'it is'} now "
                        f"that component's service, though nothing in the files says so. If "
                        f"that is right, add a [[canonical]] block naming service = "
                        f"\"{logical}\" and one of: "
                        f"{' | '.join(canonical.choice_text(c) for c in offered)} - this row "
                        f"stops once the block decides"
                        + ("; the package it selects is the one the graph then ranks "
                           "canonical" if len(every) > 1 else "")
                        + (f". {others} other package{'s' if others > 1 else ''} claim"
                           f"{'' if others > 1 else 's'} this node too; the contested row "
                           f"carries {'their' if others > 1 else 'its'} choices"
                           if others else ""))})


LINK_PASSES = (NameJoinPass(),)
