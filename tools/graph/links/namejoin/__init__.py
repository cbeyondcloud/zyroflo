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
joined by (b) alone gets ONE row (ruling 6), citing every file of every package that joined
it - a file two components read counts once: nothing in those files says they are that
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

## `[[apart]]`: a join refused (T594, batch 15b)

A block (`service`, `component` - the DECLARING component - and an optional `note`)
refuses every claim its component makes on its service (`apart_matches`, the one reading of
the block). In this pass, against the live config, in this order (D15b-36, D15b-64,
D15b-76):

1. **the re-targets**, every one judged against ONE snapshot of the edges as extracted, so
   the order the pass meets claims in decides nothing. A claim on the service's estate id
   (`component_service_id`) that its own component's id differs from, and that no build
   placed - exactly a chart's, at extraction - goes to its own component's node (`moved`),
   with the claims of that component the block also matches; a contest moves with them.
   Unless that node holds another package's claim (ruling 15): a refusal never places a
   claim on a node another package claims, so the chart stays where its file put it,
   unjoined (`held`), carrying `canonical=False` - `Edge.canonical` defaults to true, and a
   held claim is never ranked canonical on the node it was refused from (ruling 19, D15b-121).
   Anything else the block matches - a compose claim already on its own node, a build's, a
   chart whose two ids coincide - stays as it is (`kept`);
2. **meeting point (a)**, the declarations, from the edges as the re-targets left them - so
   a refused chart moved off a node leaves no declaration there;
3. **the name rule**, `main`'s, for marked claims that are not apart: an apart claim is
   never name-joined;
4. a node left with no edge is dropped, after the re-targets and the name moves alike.

Every matched claim carries `apart` and, where the block has one, its note as `apart_note`.
The refusal changes IDENTITY only (ruling 23, T621): the refused file's environment still
resolves, during extraction, as the configuration of the component named like the service,
and every row offering the block says so.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from typing import Any

from tools.graph.core.lib import ids
from tools.graph.core.model import Edge, ExtractResult, Node, Provenance, norm_id
from tools.graph.links import canonical

#: The edge type that carries a claim on a service.
CLAIM_TYPE = "DG_DEPLOYS"

#: The mark an extractor puts on a claim that names its service by name alone.
JOIN_PROP = "join"

#: What each `kind` this module emits promises its reader beyond `UNRESOLVED_COMMON`.
UNRESOLVED_FIELDS = {
    "name-joined-service": ("service", "target", "claims", "components", "choices"),
}


#: The backlog row naming what a refusal does not do (ruling 23): every row that offers
#: `[[apart]]` names it, so nobody is told the refusal fixed the value.
APART_GAP_ROW = "T621"


def _logical(service_id: str) -> str:
    return service_id.split(":", 1)[-1].split("@", 1)[0]


def apart_matches(block: dict, edge: Edge, nodes_by_id: dict[str, Node]) -> bool:
    """Whether an `[[apart]]` block refuses this claim: its DECLARING component - the claim's
    deployment node's `component` (`canonical.component_of`) - is the block's, and its
    `service`, normalised (`norm_id`, which keeps case), is the block's (D15b-5). The ONE
    reading of the block: this pass applies it, and `mark-semantic` judges `apart-unmatched`
    by it (D15b-89), as `_canonical` reads a block's path once."""
    return (edge.type == CLAIM_TYPE
            and canonical.component_of(edge, nodes_by_id) == str(block.get("component", ""))
            and norm_id(str(edge.props.get("service", "")))
            == norm_id(str(block.get("service", ""))))


def extracted_at(edge: Edge, nodes_by_id: dict[str, Node]) -> str:
    """The node extraction gave a claim - what `_refuse` judges ruling 15 by, its snapshot
    taken before any claim moves: a name-joined claim's own node (compose places a
    name-only claim there), a chart a refusal moved its service's estate id, and any other
    claim where it sits. The prediction is read from the node extraction gave a claim,
    never from the `dst` a pass gave it (D15b-65)."""
    service = str(edge.props.get("service", ""))
    if edge.props.get("joined_by"):
        return ids.service_id(service, canonical.component_of(edge, nodes_by_id))
    if edge.props.get("apart") == "moved":
        return ids.component_service_id(service)
    return edge.dst


def _ticked(items: Iterable[str]) -> str:
    return ", ".join(f"`{item}`" for item in items)


def apart_offer(service: str, deployer: str, edges: list[Edge], nodes_by_id: dict[str, Node],
                layout: dict[str, str]) -> str:
    """What a row offering `[[apart]]` for one deploying component says (T594 decisions 3
    and 5): the block; where its claims then sit - every claim the block refuses where there
    are several (D15b-88); and what the block does not do (ruling 23).

    The block refuses every claim the component makes on the service, its charts too. A
    compose claim is never re-targeted, so it sits on the component's own node; a chart
    moves there unless ruling 15 holds it - a claim of another package sits on that node
    where extraction put it, a sibling the same block refuses included (the note's T594
    decision 3) - judged exactly as `_refuse` will judge it (review B15b-9: the row said
    "then sits on" of a chart that stayed).

    What it does not do is said per reader: compose files a service's values, during
    extraction, under the component NAMED exactly as the file writes the service
    (`env_view`) - where no component carries that name, none reads them, before the
    refusal or after it, and nothing is said of them (review B15b-2) - and a chart's host
    addresses stay its own component's in the host-map ranking (review B15b-10: a chart
    files no values as any component's configuration). `layout` keys are the components'
    names as written - the one reading of them (review B15b-11)."""
    mine = [e for e in edges if e.type == CLAIM_TYPE and canonical.component_of(
        e, nodes_by_id) == deployer and norm_id(str(e.props.get("service", ""))) == norm_id(
        service)]
    files = sorted({canonical.where_of(e, nodes_by_id) for e in mine})
    own, estate = ids.service_id(service, deployer), ids.component_service_id(service)
    at_own = [(canonical.package_of(e, nodes_by_id, layout), e) for e in edges
              if e.type == CLAIM_TYPE and extracted_at(e, nodes_by_id) == own]
    stays = ""
    for e in sorted(mine, key=lambda e: canonical.where_of(e, nodes_by_id)):
        at = extracted_at(e, nodes_by_id)
        if at != estate or at == own or e.props.get("mapped_by") == "build":
            continue                      # kept where it is: `_refuse`'s structural test
        package = canonical.package_of(e, nodes_by_id, layout)
        holding = sorted({canonical.where_of(o, nodes_by_id) for p, o in at_own if p != package})
        if holding:
            stays += (f"; `{canonical.where_of(e, nodes_by_id)}` stays on `{e.dst}`, held: "
                      f"`{own}` holds the claims of {_ticked(holding)}, and a refusal never "
                      f"joins it there")
    read_as: dict[str, set[str]] = {}
    charts: set[str] = set()
    for e in mine:
        node = nodes_by_id.get(e.src)
        if node is not None and node.props.get("kind") == "compose":
            written = str(e.props.get("service", ""))
            if written in layout:
                read_as.setdefault(written, set()).add(canonical.where_of(e, nodes_by_id))
        else:
            charts.add(canonical.where_of(e, nodes_by_id))
    gaps = [f"the values {_ticked(sorted(held))} set{'s' if len(held) == 1 else ''} for "
            f"`{service}` are still read as `{name}`'s configuration"
            for name, held in sorted(read_as.items())]
    if charts:
        one = len(charts) == 1
        gaps.append(f"any host address the chart{'' if one else 's'} of "
                    f"{_ticked(sorted(charts))} publish{'es' if one else ''} is still "
                    f"`{deployer}`'s in the host-map ranking")
    return (f"if `{deployer}`'s is not that service, add [[apart]] service = \"{service}\" "
            f"component = \"{deployer}\" - its claim then sits on `{own}`, its own node"
            + stays
            + (f"; this block refuses every claim `{deployer}` makes on `{service}`: "
               f"{', '.join(files)}" if len(files) > 1 else "")
            + (f"; the block refuses the join only; {', and '.join(gaps)} (filed as "
               f"{APART_GAP_ROW})" if gaps else ""))


class NameJoinPass:
    """Move a name-only claim onto the estate's node for that name, where one exists."""

    name = "join-by-name"

    def link(self, ctx: Any, nodes: list[Node], edges: list[Edge]) -> ExtractResult:
        res = ExtractResult()
        config = ctx.config or {}
        blocks = [b for b in config.get("apart") or [] if isinstance(b, dict)]
        marked = [i for i, e in enumerate(edges)
                  if e.type == CLAIM_TYPE and e.props.get(JOIN_PROP)]
        if not marked and not blocks:
            return res
        components = {ids.component_service_id(str(c["name"])): str(c["name"])
                      for c in config.get("components") or []}
        present = {n.id for n in nodes}
        moved: set[str] = set()
        refused = self._refuse(blocks, config, nodes, edges, present, moved)
        # Meeting point (a): claims that DECLARE the name - a chart's, or a build's. Never
        # one placed by name equality alone (D534-16). From the edges as the re-targets
        # left them, so the name rule never meets a refused chart on the node it left.
        declared = {e.dst for e in edges if e.type == CLAIM_TYPE
                    and not e.props.get(JOIN_PROP) and e.props.get("mapped_by") != "name"}

        #: Per target joined by (b) alone, its claims - one row per SERVICE (ruling 6).
        by_component: dict[str, list[tuple[int, Edge]]] = {}
        for i in marked:
            edge = edges[i]
            if i in refused:
                # An apart claim is never name-joined: the mark is spent, the claim stays.
                edges[i] = replace(edge, props={k: v for k, v in edge.props.items()
                                                if k != JOIN_PROP})
                continue
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

    def _refuse(self, blocks: list[dict], config: dict, nodes: list[Node], edges: list[Edge],
                present: set[str], moved: set[str]) -> set[int]:
        """Apply the `[[apart]]` blocks; return the indices of the claims they match.

        Every re-target is judged against ONE snapshot of the edges as extracted (D15b-36):
        which packages claim a component's own node is read before any claim moves, so two
        charts of one component that one block matches both move, in either order (D15b-64),
        and a node a build claims holds a refused chart back (ruling 15)."""
        if not blocks:
            return set()
        nodes_by_id = {n.id: n for n in nodes}
        layout = canonical.workspace_layout(config)
        snapshot = list(edges)
        claimants: dict[str, set[str]] = {}
        for edge in snapshot:
            if edge.type == CLAIM_TYPE:
                claimants.setdefault(edge.dst, set()).add(
                    canonical.package_of(edge, nodes_by_id, layout))
        refused: set[int] = set()
        for i, edge in enumerate(snapshot):
            if edge.type != CLAIM_TYPE:
                continue
            # The first block decides: a later one naming the same pair is `apart-duplicate`.
            block = next((b for b in blocks if apart_matches(b, edge, nodes_by_id)), None)
            if block is None:
                continue
            refused.add(i)
            service = str(edge.props.get("service", ""))
            declaring = canonical.component_of(edge, nodes_by_id)
            own = ids.service_id(service, declaring)
            note = {"apart_note": str(block["note"])} if block.get("note") else {}
            # The structural test reads ids alone, never a reader's prop, so a block applies
            # over a shard published before batch 15b (D15b-44): exactly a chart's claim.
            if (edge.dst != ids.component_service_id(service) or own == edge.dst
                    or edge.props.get("mapped_by") == "build"):
                edges[i] = replace(edge, props={**edge.props, "apart": "kept", **note})
                continue
            if claimants.get(own, set()) - {canonical.package_of(edge, nodes_by_id, layout)}:
                edges[i] = replace(edge, canonical=False,
                                   props={**edge.props, "apart": "held", **note})
                continue
            # The placement is declared, not inferred: the provenance stays as extracted.
            edges[i] = replace(edge, dst=own, props={**edge.props, "component": declaring,
                                                     "apart": "moved", **note})
            moved.add(edge.dst)
            if own not in present:
                nodes.append(Node(id=own, type="svc", props={"component": declaring}))
                present.add(own)
        return refused

    def _rows(self, res: ExtractResult, config: dict, nodes: list[Node], edges: list[Edge],
              by_component: dict[str, list[tuple[int, Edge]]],
              components: dict[str, str]) -> None:
        """One `name-joined-service` row per service joined by a component's name alone,
        counting PACKAGES as `mark-canonical` does (ruling 6) - a file two components read
        is one, and so is compose's default pair (T588) - and citing each file once.

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
            standing = [canonical.representative(group, nodes_by_id)
                        for group in packages.values()]
            deployers = sorted({canonical.component_of(e, nodes_by_id) for e in standing}
                               - {""})
            cited = canonical.cited(packages, nodes_by_id, layout)
            # Judged over every package, offered for the CITED ones only (D534-49): a choice
            # for a package the row never names would rank a deployment its reader never saw.
            by_package = canonical.choices_by_package(every, nodes_by_id)
            offered = [by_package[key] for key in sorted(packages)]
            others = len(every) - len(packages)
            logical = _logical(target)
            n = len(standing)
            res.unresolved.append({
                "extractor": self.name, "kind": "name-joined-service", "component": "",
                "components": deployers, "claims": cited, "choices": offered,
                "service": logical, "target": target,
                "source_file": sorted(e.source_file for e in standing)[0], "line": 0,
                "why": (f"{canonical.counted(n, len(cited))} "
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
                           if others else "")
                        + "".join(f". Or, {apart_offer(logical, d, edges, nodes_by_id, layout)}"
                                  for d in deployers)
                        + "; the row stands until every claim it names is refused or the "
                          "service is decided")})


LINK_PASSES = (NameJoinPass(),)
