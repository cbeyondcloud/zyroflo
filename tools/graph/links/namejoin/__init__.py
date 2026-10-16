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

## `[[deploys]]`: a join declared (batch 23, T589)

A block (`service`, `component` - the declaring component, read as `[[apart]]` reads one -
`onto` and an optional `note`) states which build's node a deployment claim deploys, `onto`
spelled as a proposal spells it, or declines with `onto = ""` (decisions 42, 53). After
`[[apart]]`'s refusals and before the name rule, each block is judged in its order, the first
state that holds deciding (design pass 23, section 4): a pair equal once normalised to an
earlier block's is `deploys-duplicate`; a join onto the node an `[[apart]]` of its pair refuses
is `deploys-apart`; declined, nothing moves; no claim of the pair is `deploys-unmatched` (side
`claim`), judged as `apart-unmatched` is; no consumer among its claims, `deploys-not-a-consumer`;
no build on `onto`, `deploys-unmatched` (side `onto`); images the files contradict, in a
complete run, `deploys-contradicted` - in any other, LEFT; else joined. Its claims are gated
one by one: a consumer (a chart's claim, or a compose claim whose node says `deployment_only`)
moves; a claim known not to be one never moves; a compose claim whose node says nothing of it
is `deploys-unconfirmed`, every cause that holds named with its way on (D23-61, D23-137). A
joined claim moves onto `onto` - `mapped_by: image`, `joined_by: declared`, `declared`,
`declared_at`, the node's `component`, `extracted_from` - and drops what a refusal set on it;
the name rule then skips it (D23-4, D23-16, D23-57). A narrowed run moves consumer claims only,
onto an `onto` it holds, and judges no absence.

## The image rule: a join proposed (batch 23, T589)

After the declarations and before the name rule, the image rule PROPOSES and never moves
(decision 42; design pass 23, section 3). A consumer - a chart's claim, or a compose claim whose
node says `deployment_only` - whose image has a key is judged by its key's producer nodes: the
distinct nodes of the build claims with that key, an apart-matched consumer's leaving out the
node its block refuses (D23-66), against the node the consumer ENDS on - where the name rule
will join it (`name_target`), else where it sits (D23-21). One producer node other than that
node is `image-join-proposed`, the block that joins it spelled whole; two or more are
`image-ambiguous`; that node alone, or none, is nothing. A build whose image has no key but a
literal tail is `image-unresolved`, naming each consumer whose key ends with it (none of which
is then proposed onto a keyed build alone, since it may be that one too); a consumer whose image
has no key but a literal tail some producer's key ends with is `image-consumer-unresolved`; a
build whose image has neither is named in every proposal and ambiguity, one per package, as a
build that may be it. Each consumer is named only until its own block is followed or declined.

Only a COMPLETE run judges an image (`accounting.complete`): a narrowed run judges none, its
line saying so; a whole run holding anything compose did not read - a file, a directory, an
`include:`'s file, a base an `extends:` chain cannot reach - files ONE
`image-proposals-withheld` row naming each cause, and the `[[deploys]]` blocks it left.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from typing import Any

from tools.graph.core.lib import ids
from tools.graph.core.model import (Edge, ExtractResult, Node, Provenance, norm_id,
                                    unresolved_row)
from tools.graph.core.lib.rows import capped
from tools.graph.extractors._image import as_written, image_key, literal_tail, tail_of
from tools.graph.links import canonical
from tools.graph.links.canonical import accounting

#: The edge type that carries a claim on a service.
CLAIM_TYPE = "DG_DEPLOYS"

#: The mark an extractor puts on a claim that names its service by name alone.
JOIN_PROP = "join"

#: What each `kind` this module emits promises its reader beyond `UNRESOLVED_COMMON`.
UNRESOLVED_FIELDS = {
    "name-joined-service": ("service", "target", "claims", "components", "choices"),
    # Batch 23 (T589): the [[deploys]] rows.
    "deploys-duplicate": ("service", "ignored"),
    "deploys-apart": ("service",),
    "deploys-unmatched": ("service", "side"),
    "deploys-not-a-consumer": ("service",),
    "deploys-unconfirmed": ("service",),
    "deploys-contradicted": ("service", "node"),
    # Batch 23 (T589): the image rule's rows.
    "image-join-proposed": ("image", "node"),
    "image-ambiguous": ("image", "targets"),
    "image-unresolved": ("image", "consumers"),
    "image-consumer-unresolved": ("image", "builds"),
    "image-proposals-withheld": ("paths", "services", "blocks"),
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
    name-only claim there), a chart a refusal moved its service's estate id, a claim a
    declaration moved the node it recorded (`extracted_from`, batch 23), and any other
    claim where it sits. The prediction is read from the node extraction gave a claim,
    never from the `dst` a pass gave it (D15b-65). Asked in this order: a name join's
    `joined_by` - never a declared move's, which a truthiness test read as one (D23-27,
    D23-39, D23-46) - a refusal's move, a declaration's record, the claim's own node."""
    service = str(edge.props.get("service", ""))
    if edge.props.get("joined_by") in ("claim", "component"):
        return ids.service_id(service, canonical.component_of(edge, nodes_by_id))
    if edge.props.get("apart") == "moved":
        # `_refuse`'s snapshot rule, kept for it although no offer reaches it today: an
        # offered component's own node is never another service's estate id unless two
        # components' names normalise to one id (review B15b-20).
        return ids.component_service_id(service)
    if edge.props.get("extracted_from"):
        return str(edge.props["extracted_from"])
    return edge.dst


def is_build_claim(edge: Edge) -> bool:
    """A claim that builds its service: placed by a build, or a build that could not be placed
    (`unplaced`, whose `mapped_by` is empty, D23-60)."""
    return edge.props.get("mapped_by") == "build" or bool(edge.props.get("unplaced"))


def is_consumer(edge: Edge, nodes_by_id: dict[str, Node]) -> bool:
    """A claim the image link may join (design pass 23, section 3): a chart's, or a compose
    claim whose deployment node says `deployment_only` - a fact compose publishes, never a mark
    read as one - and whose chain names no base the graph cannot reach (D23-6, D23-34). A
    building component's services never are (ruling 13)."""
    node = nodes_by_id.get(edge.src)
    kind = node.props.get("kind") if node is not None else None
    if kind == "helm":
        return True
    return (kind == "compose" and node.props.get("deployment_only") is True
            and not edge.props.get("extends_unread"))


def known_not_consumer(edge: Edge, nodes_by_id: dict[str, Node]) -> bool:
    """A claim KNOWN not to be one: a build claim, or a compose claim whose node says the
    component builds (`deployment_only: false`) - ruling 13's answer (D23-114)."""
    node = nodes_by_id.get(edge.src)
    return is_build_claim(edge) or (node is not None and node.props.get("kind") == "compose"
                                    and node.props.get("deployment_only") is False)


def unconfirmed_node(edge: Edge, nodes_by_id: dict[str, Node]) -> bool:
    """A compose claim whose node publishes no `deployment_only`: its part in the image link is
    not known to this run (D23-61)."""
    node = nodes_by_id.get(edge.src)
    return (node is not None and node.props.get("kind") == "compose"
            and "deployment_only" not in node.props)


def key_of(edge: Edge) -> str:
    """The image key of a claim's image, with its registry where a chart's values set one."""
    return image_key(str(edge.props.get("image", "")), str(edge.props.get("registry", "")))


def written_of(edge: Edge) -> str:
    """A claim's image as its files write it, for a row (`_image.as_written`)."""
    return as_written(str(edge.props.get("image", "")), str(edge.props.get("registry", "")))


def tail_in(edge: Edge) -> str:
    """The literal tail of a claim's image (`_image.literal_tail`), "" for none."""
    return literal_tail(str(edge.props.get("image", "")))


def name_target(edge: Edge, declared: set[str], components: dict[str, str]) -> tuple[str, str]:
    """Where the name rule joins a marked claim - the estate's node for its service - and by
    which meeting point: "claim" where a declaration is already there (a), "component" where a
    component carries the name (b), "" where it joins none. The name rule's own target function,
    which the image rule reads to know where a consumer ends (batch 23, D23-21)."""
    target = ids.component_service_id(str(edge.props.get("service", "")))
    return target, ("claim" if target in declared else
                    "component" if target in components else "")


def ends_on(index: int, edge: Edge, refused: set[int], declared: set[str],
            components: dict[str, str]) -> str:
    """The node a claim ends on once the name rule has run: where it joins a marked claim it
    does not skip - `_refuse`'s set and the declared moves - else where the claim sits."""
    if not edge.props.get(JOIN_PROP) or index in refused:
        return edge.dst
    target, by = name_target(edge, declared, components)
    return target if by else edge.dst


def _image_at(edge: Edge, nodes_by_id: dict[str, Node]) -> str:
    """Where a base writes a build's image, qualified by its component as the build's own file
    is, or "" where its own declaration writes it (`image_from`, D23-86, D23-96)."""
    at = str(edge.props.get("image_from", ""))
    component = canonical.component_of(edge, nodes_by_id)
    return f"{component}/{at}" if at and component else at


def _unreadable(files: list[str], subject: str, tail: str = "") -> str:
    """The clause naming the builds whose image has neither a key nor a literal tail: every
    proposal and ambiguity names them, since each may be the build a consumer runs (D23-20)."""
    if not files:
        return ""
    one = len(files) == 1
    return (f"; {accounting.capped_ticked(files)} {'builds an image' if one else 'build images'} "
            f"{subject} cannot read{tail}")


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
        # `_refuse`'s own `- {package_of(edge)}`: a chart's own package never holds it back.
        # No offered chart sits on its own node where extraction put it, so nothing reaches
        # this filter today; it is kept so the two rules cannot drift (review B15b-20).
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
        deploys = [b for b in config.get("deploys") or [] if isinstance(b, dict)]
        marked = [i for i, e in enumerate(edges)
                  if e.type == CLAIM_TYPE and e.props.get(JOIN_PROP)]
        # Batch 23: a claim carrying an image wakes the pass too - the image rule judges every
        # consumer whose image has a key or a literal tail, in a workspace declaring no block at
        # all as in one declaring only [[deploys]] (D23-3).
        imaged = any(e.type == CLAIM_TYPE and e.props.get("image") for e in edges)
        if not marked and not blocks and not deploys and not imaged:
            return res
        components = {ids.component_service_id(str(c["name"])): str(c["name"])
                      for c in config.get("components") or []}
        present = {n.id for n in nodes}
        moved: set[str] = set()
        refused = self._refuse(blocks, config, nodes, edges, present, moved)
        # Batch 23: the declarations, after the refusals and before the name rule, which skips
        # every claim they moved, by index (D23-4, D23-16).
        moving = self._declare(res, deploys, blocks, config, nodes, edges, moved)
        refused |= moving
        # Meeting point (a): claims that DECLARE the name - a chart's, or a build's. Never
        # one placed by name equality alone (D534-16). From the edges as the re-targets and
        # the declarations left them, so the name rule never meets a refused chart on the node
        # it left, nor a declared claim on the node it was moved off (D23-22).
        declared = {e.dst for e in edges if e.type == CLAIM_TYPE
                    and not e.props.get(JOIN_PROP) and e.props.get("mapped_by") != "name"}
        # Batch 23: the image rule, after the declarations and before the name rule - it moves
        # nothing, and reads where the name rule will join each consumer (D23-21). A consumer
        # whose own block was followed or declined is settled: no image row names it.
        self._propose(res, config, nodes, edges, refused, moving | self.declined, declared,
                      components)

        #: Per target joined by (b) alone, its claims - one row per SERVICE (ruling 6).
        by_component: dict[str, list[tuple[int, Edge]]] = {}
        for i in marked:
            edge = edges[i]
            if i in refused:
                # An apart claim is never name-joined: the mark is spent, the claim stays - and
                # a claim a declaration moved is moved by neither rule again (batch 23).
                edges[i] = replace(edge, props={k: v for k, v in edge.props.items()
                                                if k != JOIN_PROP})
                continue
            props = {k: v for k, v in edge.props.items() if k != JOIN_PROP}
            target, by = name_target(edge, declared, components)
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

    def _declare(self, res: ExtractResult, deploys: list[dict], apart_blocks: list[dict],
                 config: dict, nodes: list[Node], edges: list[Edge], moved: set[str]) -> set[int]:
        """Apply the `[[deploys]]` blocks (batch 23, design pass 23, section 4); return the
        indices of the claims they moved. See the module's docstring for the order. Records the
        blocks it left (`self.left`) and the claims of the blocks that decline (`self.declined`),
        which the image rule reads."""
        self.left = []
        self.declined: set[int] = set()
        if not deploys:
            return set()
        nodes_by_id = {n.id: n for n in nodes}
        whole = config.get("narrowed") is False
        complete = accounting.complete(config, nodes, edges)
        unread, unreached = accounting.compose_unread(config, nodes, edges)
        first: dict[tuple[str, str], tuple[int, dict]] = {}
        moving: set[int] = set()
        for number, block in enumerate(deploys, start=1):
            component, written = str(block.get("component", "")), str(block.get("service", ""))
            onto = str(block.get("onto", "")).strip()
            src, line = str(block.get("file") or ""), int(block.get("line") or 0)
            at = f"{src}:{line}"

            key = (component, norm_id(written))
            if key in first:
                kept_at, kept = first[key]
                res.unresolved.append(unresolved_row(
                    self.name, component, "deploys-duplicate", src, line,
                    f"[[deploys]] block #{number} at {at} (service = \"{written}\", component = "
                    f"\"{component}\") names the same service of the same component as block "
                    f"#{kept_at} at {kept.get('file')}:{kept.get('line')} once spellings are "
                    f"normalised, so it decides nothing: block #{kept_at} is the one every "
                    f"reader takes. Remove one of them",
                    service=norm_id(written), ignored=written))
                continue
            first[key] = (number, block)
            refusal = next((b for b in apart_blocks if str(b.get("component", "")) == component
                            and norm_id(str(b.get("service", ""))) == norm_id(written)), None)
            if onto and refusal is not None and onto == ids.component_service_id(written):
                res.unresolved.append(unresolved_row(
                    self.name, component, "deploys-apart", src, line,
                    f"the [[deploys]] block at {at} joins `{written}` of `{component}` onto "
                    f"`{onto}`, the node the [[apart]] block at {refusal.get('file')}:"
                    f"{refusal.get('line')} refuses it - the refusal wins, as it runs first, so "
                    f"the join is not followed: point `onto` at the build's node, or remove one "
                    f"of the blocks", service=written))
                continue
            if not onto:
                # Declined (decision 53): nothing moves, and no image row names its claims.
                self.declined |= {j for j, e in enumerate(edges)
                                  if apart_matches(block, e, nodes_by_id)}
                continue
            # The block names a claim as `[[apart]]` names one - its declaring component and
            # its service, normalised - so `apart_matches` is its reading too.
            pair = [(j, e) for j, e in enumerate(edges) if apart_matches(block, e, nodes_by_id)]
            if not pair:
                if accounting.read_gate(config, component, nodes, edges):
                    r = accounting.reading(config, component, nodes, edges, nodes_by_id)
                    head = (f"The graph read no claim of `{component}` on a service "
                            f"`{written}`, so this block joins nothing it read.")
                    res.unresolved.append(unresolved_row(
                        self.name, component, "deploys-unmatched", src, line,
                        accounting.none_read(r, component, head, mode=accounting.JOINING),
                        service=written, side="claim"))
                elif whole:
                    res.unresolved.append(unresolved_row(
                        self.name, component, "deploys-unconfirmed", src, line,
                        self._unconfirmed_block(written, component, config), service=written))
                continue
            consumers = [(j, e) for j, e in pair if is_consumer(e, nodes_by_id)]
            known_not = [(j, e) for j, e in pair
                         if not is_consumer(e, nodes_by_id) and known_not_consumer(e, nodes_by_id)]
            said = False
            for _, e in pair:
                if is_consumer(e, nodes_by_id) or known_not_consumer(e, nodes_by_id):
                    continue
                said = True
                res.unresolved.append(unresolved_row(
                    self.name, component, "deploys-unconfirmed", src, line,
                    self._unconfirmed(e, written, component, unread, unreached, nodes_by_id)
                    if unconfirmed_node(e, nodes_by_id) else
                    f"names `{written}` from `{component}`: this run cannot say why "
                    f"`{canonical.where_of(e, nodes_by_id)}`'s claim is not a deployment it "
                    f"joins; report it as a defect, with the block and the claim's file",
                    service=written))
            if not consumers:
                if accounting.read_gate(config, component, nodes, edges):
                    for _, e in known_not:
                        res.unresolved.append(unresolved_row(
                            self.name, component, "deploys-not-a-consumer", src, line,
                            f"names `{written}` from `{component}`, which is not a deployment "
                            f"the image link joins - a build, or a building component's own "
                            f"service (`{canonical.where_of(e, nodes_by_id)}`)", service=written))
                elif whole and not said:
                    res.unresolved.append(unresolved_row(
                        self.name, component, "deploys-unconfirmed", src, line,
                        self._unconfirmed_block(written, component, config), service=written))
                continue
            builds = [e for e in edges if e.type == CLAIM_TYPE and e.dst == onto
                      and is_build_claim(e)]
            if not builds:
                if whole:
                    res.unresolved.append(unresolved_row(
                        self.name, component, "deploys-unmatched", src, line,
                        self._onto_unmatched(onto, unread, unreached), service=written,
                        side="onto"))
                else:
                    self.left.append(block)   # a narrowed run leaves it for a whole one
                continue
            keyed = {k for e in builds if not e.props.get("extends_unread") and (k := key_of(e))}
            unreachable = any(e.props.get("extends_unread") for e in builds)
            follow: list[tuple[int, Edge]] = []
            for j, e in consumers:
                k = key_of(e)
                if not k or k in keyed or (not keyed and not unreachable):
                    follow.append((j, e))     # agreeing, or nothing to compare
                elif complete and keyed:
                    res.unresolved.append(unresolved_row(
                        self.name, component, "deploys-contradicted", src, line,
                        f"`{canonical.where_of(e, nodes_by_id)}` runs `{k}` and `{onto}` builds "
                        f"{accounting.capped_ticked(sorted(keyed))}; the declaration at {at} joins "
                        f"two images, so it is not followed: correct it or remove it",
                        service=written, node=onto))
                elif block not in self.left:
                    self.left.append(block)   # its images could not be judged by this run
            target = nodes_by_id.get(onto)
            for j, e in follow:
                extracted = extracted_at(e, nodes_by_id)
                props = {k: v for k, v in e.props.items()
                         if k not in (JOIN_PROP, "apart", "apart_note")}
                edges[j] = replace(e, dst=onto, provenance=Provenance.INFERRED, canonical=True,
                                   props={**props, "component": str(
                                       target.props.get("component", "") if target else ""),
                                       "mapped_by": "image", "joined_by": "declared",
                                       "declared": True, "declared_at": at,
                                       "extracted_from": extracted})
                if e.dst != onto:
                    moved.add(e.dst)
                moving.add(j)
        return moving

    def _propose(self, res: ExtractResult, config: dict, nodes: list[Node], edges: list[Edge],
                 refused: set[int], settled: set[int], declared: set[str],
                 components: dict[str, str]) -> None:
        """The image rule (batch 23, design pass 23, section 3): rows that propose, and never a
        move. Judged in a COMPLETE run alone; a whole run that is not files one
        `image-proposals-withheld` row, a narrowed run none - its line says what it did not
        judge (D23-1, D23-73). Nothing is judged where no consumer's image has a key or a tail."""
        nodes_by_id = {n.id: n for n in nodes}
        claims = [(i, e) for i, e in enumerate(edges) if e.type == CLAIM_TYPE]
        consumers = [(i, e) for i, e in claims
                     if is_consumer(e, nodes_by_id) and (key_of(e) or tail_in(e))]
        if not consumers or config.get("narrowed") is not False:
            return
        if not accounting.complete(config, nodes, edges):
            self._withheld(res, config, nodes, edges)
            return
        layout = canonical.workspace_layout(config)

        def where(edge: Edge) -> str:
            return canonical.where_of(edge, nodes_by_id)

        def package(edge: Edge) -> str:
            return canonical.package_of(edge, nodes_by_id, layout)

        def standing(group: list[tuple[int, Edge]], *, each_build: bool = False) -> list[Edge]:
            """One claim per package - its representative - in file order; with `each_build`,
            one per service of a package: compose's default pair is one service's two claims,
            but two services of one file are two builds, each compared on its own."""
            by_package: dict[tuple[str, str], list[tuple[int, Edge]]] = {}
            for j, e in group:
                service = str(e.props.get("service", "")) if each_build else ""
                by_package.setdefault((package(e), service), []).append((j, e))
            return sorted((canonical.representative(g, nodes_by_id) for g in by_package.values()),
                          key=lambda e: (where(e), e.line))

        # The producers, by published props alone, each in at most one class, in order: an image
        # written with neither a key nor a literal tail is UNREADABLE; one with a key is KEYED;
        # none written is NO producer - compose names that image after its project and service,
        # and no deployment runs it by name (D23-65). One with no key but a literal tail is in no
        # class: it is compared per consumer, by `image-unresolved` (D23-99). COMPLETE leaves no
        # claim carrying `extends_unread`, so every build here was read whole (D23-111).
        keyed: dict[str, list[tuple[int, Edge]]] = {}
        unreadable: list[tuple[int, Edge]] = []
        tailed: list[tuple[int, Edge]] = []
        for j, e in claims:
            if not is_build_claim(e):
                continue
            image = str(e.props.get("image", "")).strip()
            if key_of(e):
                keyed.setdefault(key_of(e), []).append((j, e))
            elif image and tail_in(e):
                tailed.append((j, e))
            elif image:
                unreadable.append((j, e))
        unreadables = [where(e) for e in standing(unreadable)]
        open_ = [(i, e) for i, e in consumers if i not in settled]

        # A build whose image only its literal tail can compare: every consumer whose key ends
        # with it, named until that consumer's own block is followed or declined (D23-28).
        named: set[int] = set()
        for b in standing(tailed, each_build=True):
            them = [(i, e) for i, e in open_ if tail_of(key_of(e), tail_in(b))]
            if not them:
                continue
            named |= {i for i, _ in them}
            files = sorted({where(e) for _, e in them})
            one = len(files) == 1
            res.unresolved.append(unresolved_row(
                self.name, canonical.component_of(b, nodes_by_id), "image-unresolved",
                b.source_file, b.line,
                f"`{where(b)}:{b.line}` builds `{written_of(b)}`, which may be the image "
                f"{accounting.capped_ticked(files)} {'runs' if one else 'run'} - the graph cannot "
                f"read it; write the repository without interpolation, or declare the join",
                image=written_of(b), consumers=files))

        for i, c in open_:
            component = canonical.component_of(c, nodes_by_id)
            service = str(c.props.get("service", ""))
            k = key_of(c)
            if not k:
                # Its twin on the consumer's side (D23-35): a tail some producer's key ends with.
                builds = [f"{where(e)}:{e.line}" for key, group in sorted(keyed.items())
                          if tail_of(key, tail_in(c)) for e in standing(group, each_build=True)]
                if builds:
                    one = len(builds) == 1
                    res.unresolved.append(unresolved_row(
                        self.name, component, "image-consumer-unresolved", c.source_file, c.line,
                        f"`{where(c)}` runs `{written_of(c)}`, which may be the image "
                        f"{accounting.capped_ticked(builds)} {'builds' if one else 'build'} - the "
                        f"graph cannot read it; write the repository without interpolation, or "
                        f"declare the join", image=written_of(c), builds=builds))
                continue
            producers = keyed.get(k, [])
            if c.props.get("apart"):
                # The block says the claim is not the node named like its service's, and section
                # 4 refuses that join: a build there produces nothing for it (D23-66).
                refused_node = ids.component_service_id(service)
                producers = [(j, e) for j, e in producers if e.dst != refused_node]
            targets = sorted({e.dst for _, e in producers})
            end = ends_on(i, c, refused, declared, components)
            if not targets or targets == [end]:
                continue

            def build_on(node: str) -> Edge:
                return canonical.representative([(j, e) for j, e in producers if e.dst == node],
                                                nodes_by_id)

            if len(targets) > 1:
                froms = [f"`{node}`'s `image:` written at `{at}`" for node in targets
                         if (at := _image_at(build_on(node), nodes_by_id))]
                res.unresolved.append(unresolved_row(
                    self.name, component, "image-ambiguous", c.source_file, c.line,
                    f"`{where(c)}` runs `{k}`, which {len(targets)} builds the graph read produce "
                    f"({accounting.capped_ticked(targets)}{'; ' if froms else ''}"
                    f"{'; '.join(froms)})" + _unreadable(unreadables, "it")
                    + f"; declare which it deploys - `[[deploys]] service = \"{service}\" "
                      f"component = \"{component}\" onto = \"<one of them>\"` - or "
                      f"`onto = \"\"` if none", image=k, targets=targets))
                continue
            if i in named:
                continue          # a build the graph cannot read may be it too (D23-12)
            (node,) = targets
            b = build_on(node)
            at = _image_at(b, nodes_by_id)
            pair = [(j, e) for j, e in claims
                    if apart_matches({"component": component, "service": service}, e, nodes_by_id)]
            mine = {j for j, e in pair if is_consumer(e, nodes_by_id)}
            moving = sorted({where(e) for j, e in pair if j in mine})
            unjoined = sorted({where(e) for j, e in pair
                               if j not in mine and known_not_consumer(e, nodes_by_id)})
            # The name-only claims joined onto this consumer's node through it today (meeting
            # point (a)), which a declaration moving it leaves on their own (D23-22).
            here = c.dst
            through: list[str] = []
            if here not in components and not any(
                    e.dst == here and j not in mine and not e.props.get(JOIN_PROP)
                    and e.props.get("mapped_by") != "name" for j, e in claims):
                through = sorted({where(e) for j, e in claims
                                  if e.props.get(JOIN_PROP) and j not in refused and j not in mine
                                  and e.dst != here
                                  and name_target(e, declared, components) == (here, "claim")})
            res.unresolved.append(unresolved_row(
                self.name, component, "image-join-proposed", c.source_file, c.line,
                f"`{where(c)}` runs `{k}`, which `{where(b)}:{b.line}` builds (`{node}`)"
                + (f", its `image:` written at `{at}`" if at else "")
                + " - the one build of it the graph read"
                + _unreadable(unreadables, "the graph", ", which may be it too")
                + f". If it deploys that build, declare it - `[[deploys]] service = \"{service}\" "
                  f"component = \"{component}\" onto = \"{node}\"`, which moves the deployment "
                  f"claims `{component}` makes on `{service}` "
                  f"({accounting.capped_ticked(moving)}), and none other"
                + (f" - {accounting.capped_ticked(unjoined)}, which the image link does not join "
                   f"(a build, a building component's own service), "
                   f"{'stays where it is' if len(unjoined) == 1 else 'stay where they are'}"
                   if unjoined else "")
                + (f", and leaves {accounting.capped_ticked(through)}, joined by name onto "
                   f"`{here}` through it today, on {'its' if len(through) == 1 else 'their'} own"
                   if through else "")
                + "; if it does not, declare `onto = \"\"` and this stops - nothing joins by "
                  "image until declared"
                + (f". The name rule joins it onto `{end}` today" if end != c.dst else ""),
                image=k, node=node))

    def _withheld(self, res: ExtractResult, config: dict, nodes: list[Node],
                  edges: list[Edge]) -> None:
        """ONE `image-proposals-withheld` row for a whole run that did not read everything
        compose merges (design pass 23, section 3): each cause named, against the first in its
        own order - by component, then path, then service - at that cause's line (D23-156,
        D23-163); the fields whole, the words capped (`capped`)."""
        unread, unreached = accounting.compose_unread(config, nodes, edges)
        causes = sorted([((u.component, u.path, ""), (u.component, u.file, u.line))
                         for u in unread]
                        + [((u.component, u.file, u.service), (u.component, u.file, u.line))
                           for u in unreached])
        if not causes:
            return      # COMPLETE names a cause wherever a whole run is not complete
        component, src, line = causes[0][1]
        why_of: dict[str, str] = {}
        for u in unread:
            why_of.setdefault(f"{u.component}/{u.path}", u.why)
        paths = list(why_of)
        services = [f"{u.service} of {u.component}/{u.file}" for u in unreached]
        blocks = [f"{b.get('file')}:{b.get('line')}" for b in self.left]
        one = len(unreached) == 1
        res.unresolved.append(unresolved_row(
            self.name, component, "image-proposals-withheld", src, line,
            "the graph did not read, as each component's own, everything compose merges into the "
            "services it holds"
            + (" - " + capped([f"`{p}` ({why_of[p]})" for p in paths]) if paths else "")
            + ("; " + capped([f"`{u.service}` of `{u.component}/{u.file}` (`{u.extends}` - its "
                              f"`file:` {u.reason})" for u in unreached])
               + f" extend{'s' if one else ''} a base it cannot reach" if unreached else "")
            + ", so which builds produce which images is not known until it does: no image join "
              "is proposed, no image named unreadable, and no `[[deploys]]` block refused for its "
              "images - one is followed only where they agree over what it read, or cannot be "
              "compared"
            + (f", and the `[[deploys]]` block{'' if len(blocks) == 1 else 's'} at "
               f"{accounting.capped_ticked(blocks)}, whose images it could not judge, "
               f"{'is' if len(blocks) == 1 else 'are'} left unfollowed until then"
               if blocks else "")
            + ". Fix each as its own row says - a file an `include:` names by making the "
              "`include:` name a compose file this component's reader reads - and rebuild",
            paths=paths, services=services, blocks=blocks))

    @staticmethod
    def _unconfirmed(edge: Edge, written: str, component: str, unread: list, unreached: list,
                     nodes_by_id: dict[str, Node]) -> str:
        """`deploys-unconfirmed` for a claim whose node says nothing of `deployment_only`: every
        cause that holds, in order, and the way on beside it (D23-137, D23-142, D23-150)."""
        files = sorted({u.path for u in unread
                        if u.component == component and u.why != accounting.NOT_LISTED})
        dirs = sorted({u.path for u in unread
                       if u.component == component and u.why == accounting.NOT_LISTED})
        siblings = sorted({u.service for u in unreached
                           if u.component == component and u.service != written})
        causes, ways = [], []
        if files:
            causes.append(f"{accounting.capped_ticked(files)} "
                          f"{'was' if len(files) == 1 else 'were'} not read")
            ways.append("fix them as their own rows say - an `include:`'s by naming a compose "
                        "file the reader reads - and rebuild")
        if dirs:
            causes.append(f"{accounting.capped_ticked(dirs)} could not be listed")
            ways.append("allow listing them, then rebuild")
        if edge.props.get("extends_unread"):
            causes.append("its `extends:` chain names a base the graph cannot reach")
            ways.append("fix what its `compose-extends-unresolved` row names, and rebuild")
        if siblings:
            one = len(siblings) == 1
            causes.append(f"{'another service' if one else 'other services'} of `{component}`, "
                          f"{accounting.capped_ticked(siblings)}, extend{'s' if one else ''} a "
                          f"base the graph cannot reach, which may declare a build")
            ways.append(f"fix what {'its' if one else 'their'} `compose-extends-unresolved` "
                        f"row{'' if one else 's'} name{'s' if one else ''}, and rebuild")
        if not causes:
            causes.append("its shard does not record it")
            ways.append("re-publish the shard")
        where = canonical.where_of(edge, nodes_by_id)
        return (f"names `{written}` from `{component}` (`{where}`), "
                f"which this run cannot confirm is a deployment the image link joins - "
                f"{'; '.join(causes)} - so it is not followed until it can be: "
                f"{'; '.join(ways)}")

    @staticmethod
    def _unconfirmed_block(written: str, component: str, config: dict) -> str:
        """`deploys-unconfirmed` for a block whose component no deployment reader read, or
        whose read the accounting gate cannot trust, in a whole run - never silent (D23-78)."""
        read = ((config.get("deployment_inputs") or {}).get(component)
                or (config.get("deployment_off_case") or {}).get(component))
        cause, way = (("its shard does not record what the image link reads",
                       "re-publish the shard") if read else
                      (f"no deployment file of `{component}` was read",
                       "correct the block's `component`, or add the deployment file it means"))
        return (f"names `{written}` from `{component}`, which this run cannot confirm is a "
                f"deployment the image link joins - {cause} - so it is not followed until it "
                f"can be: {way}")

    @staticmethod
    def _onto_unmatched(onto: str, unread: list, unreached: list) -> str:
        """`deploys-unmatched`'s `onto` side, naming what compose did not read, which may hold
        the build (D23-115, D23-121, D23-124) - compared as an id, never parsed (D23-59)."""
        paths = sorted({f"{u.component}/{u.path}" for u in unread})
        services = sorted({u.service for u in unreached})
        said = f"names `{onto}`, which no build the graph read is on"
        if paths:
            said += (f" - {accounting.capped_ticked(paths)} were not read and may hold it: fix "
                     f"them as their own rows say - an `include:`'s by naming a compose file "
                     f"the reader reads - and rebuild")
        if services:
            said += (f" - {accounting.capped_ticked(services)} extend a base the graph cannot "
                     f"reach, which may place a build on it: fix what their "
                     f"`compose-extends-unresolved` rows name, and rebuild")
        return (said + ". Correct the block's `onto`, as an `image-join-proposed` row spells "
                       "it, or remove it")

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
            decided, _ = canonical.select_package(
                every, canonical.block_for(config, target, every, nodes_by_id)[0], nodes_by_id)
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
            # The block names each claim as the claim's own file spells its service (D23-7), the
            # contest row's offer alike: both match it once normalised.
            refusable = sorted({(canonical.component_of(e, nodes_by_id),
                                 str(e.props.get("service", ""))) for _, e in joined
                                if canonical.component_of(e, nodes_by_id)})
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
                        + "".join(f". Or, {apart_offer(own, d, edges, nodes_by_id, layout)}"
                                  for d, own in refusable)
                        + "; the row stands until every claim it names is refused or the "
                          "service is decided")})


LINK_PASSES = (NameJoinPass(),)
