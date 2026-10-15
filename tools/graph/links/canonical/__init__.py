"""When several deployment packages claim one service, decide which one is authoritative.

## Why this cannot be inferred

Two packages declaring the same logical service are, to a scanner, byte-for-byte equally
real: both name it, both are committed, both may be deployed somewhere. Which one the
workspace considers *the* service — the one configuration and interface links should attach
to — is a statement about intent, and intent is not in the files.

Every available heuristic is a guess dressed as a fact. Newest modification date rewards
whoever last touched a dead tree. A directory called `legacy` is a naming convention, not a
declaration, and the fixture this was built against has the current tree under a generic
name and the superseded one under the service's own name — so that heuristic gets it exactly
backwards. **Guessing here attaches real edges to the wrong service while looking entirely
correct**, which is the failure mode that makes a graph confidently useless rather than
visibly broken.

So the decision comes from the workspace's own `[[canonical]]` declaration, and where the
declaration does not decide it, the collision is REPORTED and nothing is marked.

## The paperwork is drafted, the decision is not (item 10)

On a real estate the contested list is long and REGULAR: 206 services contested in only
10 distinct claimant-sets, because a handful of deployment trees each declare the whole
platform. The curation is ~10 answers wearing 206 blocks, so `propose()` writes the 206
blocks — grouped by claimant-set, every DECISION slot blank, each service's claims cited
file:line — and a developer answers per group by filling one slot and repeating it. What
is fact rather than decision the tool does fill: a single-component collision's `owner`
is just the report restated, and only its `path` is a choice. An unreviewed paste cannot
silently pick winners either way: a blank `owner` is refused by the workspace parser
loudly, and a blank `path` under a filled owner matches every claim at once, decides
none of them, and stays reported.

## Damped, never dropped

A non-canonical claim keeps its edge at halved confidence with `canonical=False`. Dropping
it would remove a real coupling from the graph — and a superseded variant that is about to
break is precisely what a blast radius exists to find. It has to be visible and it has to
rank below the real one; those are different requirements and both are met.
"""
from __future__ import annotations

import posixpath
from dataclasses import replace
from typing import Any

from tools.graph.core.lib import ids
from tools.graph.core.model import Edge, ExtractResult, Node, norm_id, unresolved_row
from tools.graph.extractors import _canonical
from tools.graph.links.canonical import accounting

#: The edge type that carries a claim on a service.
CLAIM_TYPE = "DG_DEPLOYS"

#: What a losing claim's confidence is multiplied by. Visible, and ranked below the winner.
DAMPING = 0.5


#: One normalising reading of the component list, shared with compose and the join so the
#: three cannot disagree about where a component is (D534-42). Re-exported here because
#: the package helpers below are its consumers.
workspace_layout = _canonical.workspace_layout


def blocks_for(config: dict, service_id: str) -> list[dict]:
    """Every `[[canonical]]` block naming this node's service, in file order.

    Matched on the logical name inside the id rather than on the id itself: a developer
    writes `service = "orders-api"`, not the internal identity string, and requiring the
    latter would make the file unwritable without reading this source. Normalised as the
    id is (D534-24): `my api` and `my-api` name one service. Several may name it (T623,
    decision 41); which decides a node is `block_for`'s answer.
    """
    logical = service_id.split(":", 1)[-1].split("@", 1)[0]
    return [block for block in config.get("canonical", []) or []
            if isinstance(block, dict) and norm_id(str(block.get("service", ""))) == logical]


def block_for(config: dict, service_id: str, packages: dict[str, list[tuple[int, Edge]]],
              nodes_by_id: dict[str, Node]) -> tuple[dict | None, list[dict]]:
    """(the block deciding this node, the later blocks designating one of its packages too) -
    `_canonical.deciding_block` over every package claiming the node, a held one included,
    since a block ranks held packages too (ruling 22). None where no block for the service
    designates one of them (T623, decision 41)."""
    return _canonical.deciding_block(
        blocks_for(config, service_id),
        [(component_of(e, nodes_by_id), _node_props(e, nodes_by_id), e.source_file)
         for group in packages.values() for _, e in group])


def component_of(edge: Edge, nodes_by_id: dict[str, Node]) -> str:
    """The component a claim was made from: its deployment node's own `component`."""
    return str(nodes_by_id.get(edge.src, Node(id="x", type="x")).props.get("component", ""))


def where_of(edge: Edge, nodes_by_id: dict[str, Node]) -> str:
    """A claim's file, qualified by its component (D9-4): `docker-compose.yml` names a file
    in every repository that has one."""
    component = component_of(edge, nodes_by_id)
    return f"{component}/{edge.source_file}" if component else edge.source_file


def declared_package(edge: Edge, nodes_by_id: dict[str, Node]) -> str:
    """The file whose package a claim's deployment node says it is part of, "" for none:
    compose's default override names its base (T588), a file of the same component.

    Read only from a deployment node - a `ui` node's `package` is an npm package's name -
    and only as a relative path that does not climb out of the component: one climbing out,
    or an absolute one that two components could spell alike, would fold two claims into
    one package and hide the contest (reviews B15a-4, B15a-11). `package_of` also refuses
    one naming a file of a component declared inside this one. Compose writes none of
    these, and no node of another type makes a claim; such a claim keys on its own file. A
    spelling `posixpath` reads as neither - a Windows `..\\` climb or a drive letter - is
    kept, and keys under the declaring component's own path, so it folds nothing (measured,
    review B15a-16)."""
    node = nodes_by_id.get(edge.src)
    if node is None or node.type != "deploy":
        return ""
    package = node.props.get("package")
    if not isinstance(package, str) or not package:
        return ""
    normal = posixpath.normpath(package)
    return "" if normal == ".." or normal.startswith(("../", "/")) else normal


def _in_a_declared_child(path: str, component: str, layout: dict[str, str]) -> bool:
    """Whether the workspace path `path` lies under a component declared inside
    `component` - whose file it then is by T591's rule, not `component`'s (review B15a-11:
    the root component's path holds every other). Asked of the one declared-child rule
    the build reads a file's reader by (`_canonical.child_holding`, T616): by whole path
    segments, `dep` holds `dep/x`, never `deploy/x` (T530's trap; review B15a-13), and two
    components over one directory hold neither."""
    mine = layout[component]
    if mine == ".":
        rel = path
    elif path.startswith(mine + "/"):
        rel = path[len(mine) + 1:]
    else:
        return False
    return _canonical.child_holding(
        rel, _canonical.declared_children(component, layout)) is not None


def package_of(edge: Edge, nodes_by_id: dict[str, Node], layout: dict[str, str]) -> str:
    """The package a claim belongs to: one FILE is one package (D534-18), unless its
    deployment node names the file whose package it is part of (T588: compose's default
    pair is one package, named by its base) - a file of the declaring component's own.

    Two components declared over one directory both read its files (T615's shape; since T591
    a parent no longer reads its declared children's), and two readings of one file are not
    two deployments. Keyed on the file's workspace-relative path; on its deploy node when the
    component's path is not known - which, with the layout from `config["components"]`, is
    only a caller that gave none - and a named package is read first, so with no layout the
    pair still keys on its base's deploy node (D15a-32).
    """
    component = component_of(edge, nodes_by_id)
    named = declared_package(edge, nodes_by_id)
    if component not in layout:
        return ids.deployment_id(component, named) if named else edge.src
    if named:
        declared = posixpath.normpath(posixpath.join(layout[component], named))
        if not _in_a_declared_child(declared, component, layout):
            return declared
    return posixpath.normpath(posixpath.join(layout[component], edge.source_file))


def file_of(edge: Edge, nodes_by_id: dict[str, Node], layout: dict[str, str]) -> str:
    """What makes a claim's file one file (D15a-37): its normalised WORKSPACE path, so a file
    two components read is one file and two components' `docker-compose.yml` are two - never
    the component-relative path alone (D9-4). With no layout, an in-process caller's case,
    the component-qualified path `where_of` renders (D15a-46)."""
    component = component_of(edge, nodes_by_id)
    if component not in layout:
        return where_of(edge, nodes_by_id)
    return posixpath.normpath(posixpath.join(layout[component], edge.source_file))


def representative(claims: list[tuple[int, Edge]], nodes_by_id: dict[str, Node]) -> Edge:
    """The one claim that stands for a package when it is named: its base's claim - the base
    is the file a package is named by, and declares no package of its own - when it has one,
    otherwise the first by component name, then file and line; never by edge order (D534-30,
    D534-42, D15a-8). Every choice therefore names a file that HAS a claim, so following it
    decides: a base that does not parse has no claim, and its override stands for the
    package. A claim whose declared package `package_of` refuses still sorts as declaring
    one, which decides nothing: its package then holds only readings of its own file, which
    all sort alike (review B15a-15).

    It used to prefer the MOST SPECIFIC reader, a nested child over its parent. Since T591 a
    parent no longer reads its children's files, and the one shape left with two readers -
    two components declared over one directory (T615) - puts both at one depth, so the depth
    order could never decide and is retired (design review D15a-14)."""
    return min((edge for _, edge in claims),
               key=lambda edge: (bool(declared_package(edge, nodes_by_id)),
                                 component_of(edge, nodes_by_id), edge.source_file, edge.line))


def cited(packages: dict[str, list[tuple[int, Edge]]], nodes_by_id: dict[str, Node],
          layout: dict[str, str]) -> list[str]:
    """Every file of every package, once, as `where:line` (D15a-6, D15a-18): one per
    distinct file and line, by its first reader by name - a file two components read is
    cited once."""
    first: dict[tuple[str, int], Edge] = {}
    for edge in sorted((edge for group in packages.values() for _, edge in group),
                       key=lambda edge: (component_of(edge, nodes_by_id), edge.source_file,
                                         edge.line)):
        first.setdefault((file_of(edge, nodes_by_id, layout), edge.line), edge)
    return sorted(f"{where_of(edge, nodes_by_id)}:{edge.line}" for edge in first.values())


def counted(packages: int, files: int) -> str:
    """`2 deployment packages (3 files)`, in the singular where a count is one (D15a-48)."""
    return (f"{packages} deployment package{'' if packages == 1 else 's'} "
            f"({files} file{'' if files == 1 else 's'})")


def is_held(claims: list[tuple[int, Edge]]) -> bool:
    """Whether a package's claims on a node were refused there and held (ruling 15): an
    `[[apart]]` block named them, and their component's own node holds another package's
    claim, so they stay where their file put them, unjoined."""
    return any(edge.props.get("apart") == "held" for _, edge in claims)


def held_on(edge: Edge, edges: list[Edge], nodes_by_id: dict[str, Node],
            layout: dict[str, str]) -> tuple[str, list[str], list[str]]:
    """A held claim's component's own node, the files of every other package's claims on
    it (ruling 15; D15b-140 i), and those of them a build placed - one reading for the
    contest row's held clause and `apart-held` alike."""
    own = ids.service_id(str(edge.props.get("service", "")), component_of(edge, nodes_by_id))
    mine = package_of(edge, nodes_by_id, layout)
    there = [e for e in edges if e.type == CLAIM_TYPE and e.dst == own
             and package_of(e, nodes_by_id, layout) != mine]
    return (own, sorted({where_of(e, nodes_by_id) for e in there}),
            sorted({where_of(e, nodes_by_id) for e in there
                    if e.props.get("mapped_by") == "build"}))


def _node_props(edge: Edge, nodes_by_id: dict[str, Node]) -> dict:
    """The facts the claim's reader published on its deployment node (`kind`,
    `ships_in`), which `_canonical` dispatches a block's reading on."""
    node = nodes_by_id.get(edge.src)
    return dict(node.props) if node is not None else {}


def by_path(decl: dict, packages: dict[str, list[tuple[int, Edge]]],
            nodes_by_id: dict[str, Node]) -> str | None:
    """The package a block's `path` selects, by each reader's own rule (batch 15b, T573
    decision 2), or None: one designated package is selected; of two or more, the most
    specific wins by an ORDERED test - first the packages whose own file the path names,
    and only where none is, those whose own chart it names (ruling 14, D15b-119) - and
    where even that leaves none or several, nothing is decided, as on `main`."""
    def holds(key: str, test) -> bool:
        return any(test(edge) for _, edge in packages[key])

    designated = [key for key in packages if holds(key, lambda e: _canonical.designates(
        decl, component_of(e, nodes_by_id), _node_props(e, nodes_by_id), e.source_file))]
    if len(designated) <= 1:
        return designated[0] if designated else None
    for rank in (2, 1):
        exact = [key for key in designated if holds(key, lambda e, r=rank: _canonical.
                 names_exactly(decl, component_of(e, nodes_by_id), _node_props(e, nodes_by_id),
                               e.source_file) == r)]
        if exact:
            return exact[0] if len(exact) == 1 else None
    return None


def select_package(packages: dict[str, list[tuple[int, Edge]]], decl: dict | None,
                   nodes_by_id: dict[str, Node]) -> tuple[str | None, str]:
    """Which PACKAGE the declaration selects, and how. `None` means it did not decide.

    Decided per package, so one package's claims on a service (a `web` and a `worker`
    built from one image) win or lose together (D9-21). A package read by two components
    matches an `owner` naming EITHER of them (D534-30) - and an `owner` that reads two of
    the service's packages (a component holding two of them, such as a compose base and
    its prod file) selects NEITHER, because it names no one package: it takes a `path` as
    well (D534-39). `choices` says which owners and paths decide, so no draft or row offers
    one that does not.

    A package held on this node after its component refused it (`apart: held`) is never
    selected, by either branch (ruling 19): declared apart from this service, it is no
    variant of it. A path that reads as the owner's root takes the owner branch, recorded
    `owner=` (D15b-9, D15b-30)."""
    if decl is None:
        return None, ""
    packages = {key: group for key, group in packages.items() if not is_held(group)}

    # `path` first: it is the narrower statement, and the only one that can separate two
    # packages living in ONE component — which `owner` cannot, since both would name it.
    # Matched by each reader's own rule (batch 15b): `covers` for a compose file, the
    # chart rule for a chart's - so a block naming a chart's template, its values overlay
    # or its `Chart.yaml` decides a contest exactly as it ranks the host map.
    path = (decl.get("path") or "").strip().strip("/")
    if path and not _canonical.is_root_path(decl):
        winner = by_path(decl, packages, nodes_by_id)
        return (winner, f"path={path}") if winner is not None else (None, "")

    owner = (decl.get("owner") or "").strip()
    if owner:
        matches = [src for src, claims in packages.items()
                   if any(component_of(e, nodes_by_id) == owner for _, e in claims)]
        if len(matches) == 1:
            return matches[0], f"owner={owner}"
    return None, ""


def choices_by_package(packages: dict[str, list[tuple[int, Edge]]],
                       nodes_by_id: dict[str, Node]) -> dict[str, dict]:
    """For each package, the smallest `[[canonical]]` fields that select exactly it, by
    `select_package`'s own rule (D534-39, ruling 7): an owner that reads no other package
    of this service - the package's representative first - or, where every reader also
    reads another, the representative's owner with its path. Following any choice decides;
    no choice offered decides nothing. Keyed by package, so a row about some of a node's
    packages offers only theirs (D534-49)."""
    # A held package is never offered here (ruling 19): no choice may name it.
    packages = {key: group for key, group in packages.items() if not is_held(group)}
    reads: dict[str, set[str]] = {}
    for key, group in packages.items():
        for _, edge in group:
            reads.setdefault(component_of(edge, nodes_by_id), set()).add(key)
    out: dict[str, dict] = {}
    for key, group in sorted(packages.items()):
        rep = representative(group, nodes_by_id)
        rep_owner = component_of(rep, nodes_by_id)
        readers = sorted({component_of(e, nodes_by_id) for _, e in group} - {""},
                         key=lambda c: (c != rep_owner, c))
        owner = next((c for c in readers if reads.get(c) == {key}), "")
        out[key] = ({"owner": owner} if owner else
                    {"owner": rep_owner, "path": rep.source_file})
    return out


def choices(packages: dict[str, list[tuple[int, Edge]]],
            nodes_by_id: dict[str, Node]) -> list[dict]:
    """Every package's choice, in package order - what the contested row offers."""
    return list(choices_by_package(packages, nodes_by_id).values())


def choice_text(choice: dict) -> str:
    """One choice as the fields a developer writes."""
    text = f'owner = "{choice["owner"]}"'
    return text + (f', path = "{choice["path"]}"' if choice.get("path") else "")


#: What each `kind` this module emits promises its reader beyond `UNRESOLVED_COMMON`.
#: Declared HERE rather than in core: a central table would make core learn every
#: extractor's vocabulary, so adding a stack would force a core edit. The pipeline
#: checks these after every run and fails the build when a row does not carry them.
UNRESOLVED_FIELDS = {
    "contested-service": ("service", "claims", "components", "choices"),
    "canonical-duplicate": ("service", "ignored"),
    "canonical-overlap": ("service", "node"),
    "canonical-unmatched": ("service", "path"),
    "apart-held": ("service", "node"),
}

class CanonicalPass:
    """Resolve contested services from the workspace's own declaration."""

    name = "mark-canonical"
    #: T534 v4: a name-only claim is moved onto the estate's node before contests are read.
    REQUIRES = ("join-by-name",)

    def link(self, ctx: Any, nodes: list[Node], edges: list[Edge]) -> ExtractResult:
        res = ExtractResult()
        nodes_by_id = {n.id: n for n in nodes}
        layout = workspace_layout(ctx.config)
        # A config finding, found here because this pass is the one that reads the
        # blocks: absent from a build whose `--only` leaves this pass out (D534-44).
        self._duplicates(res, ctx.config or {})

        by_service: dict[str, list[tuple[int, Edge]]] = {}
        for i, edge in enumerate(edges):
            if edge.type == CLAIM_TYPE:
                by_service.setdefault(edge.dst, []).append((i, edge))

        for service, claims in sorted(by_service.items()):
            packages: dict[str, list[tuple[int, Edge]]] = {}
            for i, edge in claims:
                packages.setdefault(package_of(edge, nodes_by_id, layout), []).append((i, edge))
            if len(packages) < 2:
                # One package's claims are not a contest (D9-21); held, it is said alone.
                self._held(res, ctx.config or {}, service, packages, None, edges,
                           nodes_by_id, layout)
                continue
            decl, overlapping = block_for(ctx.config or {}, service, packages, nodes_by_id)
            # A later block `_duplicates` names is that row's alone, judged once (review
            # B21-6), as the unmatched loop below judges it.
            judged = {_same_block(decl)} if decl is not None else set()
            for later in overlapping:
                if _same_block(later) in judged:
                    continue
                judged.add(_same_block(later))
                self._overlap(res, ctx.config or {}, service, decl, later, packages,
                              nodes_by_id)
            winner, how = select_package(packages, decl, nodes_by_id)
            ranked = self._rank_held(edges, packages, decl, nodes_by_id)
            # One representative per PACKAGE (D534-31): a file two components read is one
            # package, and so is compose's default pair (T588).
            standing = {key: representative(group, nodes_by_id)
                        for key, group in packages.items()}
            if winner is None:
                # `component` is empty ON PURPOSE and `components` carries the answer. This
                # finding is about a collision BETWEEN components, so naming one of them
                # would file a shared problem against a single owner and imply a decision
                # this pass has just refused to make. Empty is a real state here; absent
                # was not, and 206 rows in a real workspace carried neither — so a reader
                # could see that some service was contested and not by whom.
                # One row, two frames (D534-14): `claims` are qualified by component, while
                # `source_file` stays the first claim's component-relative path beside that
                # empty `component` - read the claimants from `claims`, never by joining the
                # two. `claims` cite every file of every package once (D15a-6). `choices`
                # are the blocks that decide (D534-39): an owner reading several of these
                # packages is offered only with a path.
                claimants = sorted({component_of(e, nodes_by_id)
                                    for e in standing.values()} - {""})
                offered = choices(packages, nodes_by_id)
                files = cited(packages, nodes_by_id, layout)
                res.unresolved.append({
                    "extractor": self.name, "kind": "contested-service",
                    "component": "", "components": claimants,
                    "service": service,
                    "claims": files,
                    "choices": offered,
                    "source_file": sorted(e.source_file for e in standing.values())[0],
                    "line": 0,
                    "why": self._contest_why(ctx.config or {}, service, packages, decl,
                                             ranked, offered, files, claims, edges,
                                             nodes_by_id, layout),
                })
                if decl is not None:
                    # T619: the block that designates these packages and decides none, so the
                    # draft changes it rather than adding a second block that would overlap.
                    res.unresolved[-1]["block"] = (f"{decl.get('file')}:{decl.get('line')}"
                                                   if decl.get("file") else
                                                   "the workspace config")
                continue

            won = where_of(standing[winner], nodes_by_id)
            for i, edge in claims:
                if apart_state(edge) == "held":
                    # Ruling 19 (D15b-121): declared apart from this service, a held claim is
                    # no superseded variant of it - never ranked, damped or stamped by the
                    # node's decision; `canonical=False` is its own, written at the refusal.
                    continue
                if package_of(edge, nodes_by_id, layout) == winner:
                    edges[i] = replace(edge, canonical=True,
                                       props={**edge.props, "canonical_by": how})
                else:
                    # Damped, not dropped. The superseded variant stays in the graph because
                    # a variant about to break is exactly what blast radius exists to find.
                    edges[i] = replace(edge, canonical=False,
                                       confidence=round(edge.confidence * DAMPING, 4),
                                       props={**edge.props, "canonical_by": how,
                                              "superseded_by": won})
            self._held(res, ctx.config or {}, service, packages, won, edges, nodes_by_id,
                       layout)
        # T573: a block that designates nothing the graph read is a row - one per block,
        # since several may name one service (T623), each judged on its own; a duplicate
        # `_duplicates` names is judged once. Appended here, at its emission site (D15b-97).
        config = ctx.config or {}
        seen: set[tuple[str, str, str]] = set()
        for block in config.get("canonical") or []:
            if not isinstance(block, dict) or _same_block(block) in seen:
                continue
            seen.add(_same_block(block))
            why = self._unmatched(config, block, nodes, edges, nodes_by_id, layout)
            if why:
                res.unresolved.append(unresolved_row(
                    self.name, str(block.get("owner", "")), "canonical-unmatched",
                    str(block.get("file") or ""), int(block.get("line") or 0), why,
                    service=str(block.get("service", "")), path=str(block.get("path") or "")))
        return res

    def _overlap(self, res: ExtractResult, config: dict, service: str, decl: dict,
                 later: dict, packages: dict[str, list[tuple[int, Edge]]],
                 nodes_by_id: dict[str, Node]) -> None:
        """T623: a block designating a node an earlier block designates too decides nothing
        about that node, and is named at its line, as `canonical-duplicate` names a respelt
        name: the first block designating a node decides it. Compose still ranks by it the
        environment values of the files it designates - it ranks them one component at a time
        (ruling 9), so its decision is over that component's files alone - and the row says so
        rather than claim every reader takes the first (review B21-29). Each is said with the
        packages it designates the node through, which need not be one (review B21-1), and the
        ways on are the two that end the row - remove this block, or the earlier one if this
        is the one meant. Narrowing this block cannot: the earlier still designates the
        node."""
        blocks = [b for b in config.get("canonical") or [] if isinstance(b, dict)]
        first = next((i for i, b in enumerate(blocks, 1) if b is decl), 0)
        mine = next((i for i, b in enumerate(blocks, 1) if b is later), 0)

        def where(block: dict) -> str:
            return f" at {block.get('file')}:{block.get('line')}" if block.get("file") else ""

        def through(block: dict) -> str:
            return ", ".join(f"`{key}`" for key, group in sorted(packages.items())
                             if any(_canonical.block_designates(
                                 block, component_of(e, nodes_by_id),
                                 _node_props(e, nodes_by_id), e.source_file)
                                 for _, e in group))

        fields = ", ".join(f'{key} = "{later[key]}"' for key in ("service", "owner", "path")
                           if str(later.get(key) or "").strip())
        res.unresolved.append(unresolved_row(
            self.name, str(later.get("owner", "")), "canonical-overlap",
            str(later.get("file") or ""), int(later.get("line") or 0),
            f"[[canonical]] block #{mine}{where(later)} ({fields}) designates `{service}` "
            f"through {through(later)}, and block #{first}{where(decl)} designates it too, "
            f"through {through(decl)}; the first block designating a node decides it, so "
            f"block #{mine} decides nothing about this node - though compose, which ranks "
            f"environment values one component at a time, still ranks by it the values of the "
            f"files it designates. Keep the one you mean: remove this block, or remove block "
            f"#{first} if this is the one",
            service=str(later.get("service", "")), node=service))

    def _held(self, res: ExtractResult, config: dict, service: str,
              packages: dict[str, list[tuple[int, Edge]]], won: str | None, edges: list[Edge],
              nodes_by_id: dict[str, Node], layout: dict[str, str]) -> None:
        """The owner's ruling 25: a held claim no contest row reports - alone on its node,
        or on one a block decides (`won`, the winner's file) - is said by a row of its own,
        cited at the block that refused it: where it stays and why, that it is never
        canonical there, and its way on. Held states are always said, once (rulings 19,
        22): on a contested node the contest row's held clause says it.

        "No other package claims the node" judges absence, so it is said only of a whole
        reading (the note's third rule): a narrowed run says what it read (review B15b-16),
        and so does a run holding a deployment file it could not read a service from,
        which narrows nothing yet may claim the node (review B15b-22) - every such file,
        since what it deploys is unknown whatever lost it (B15b-37): a refused component's,
        read, may be held on this very node or kept on its own node where that is this one
        (review B15b-30, which measured round 5's filter concluding absence there).
        Concluded, the absence ends the row with what the graph does not read, as the
        accounting rows end (B15b-29, B15b-34). Where the block refuses more than this
        claim, the row names every one, as a row offering the block does (D15b-88), since
        removing it releases them all (review B15b-19)."""
        from tools.graph.links.namejoin import apart_matches
        blocks = [b for b in config.get("apart") or [] if isinstance(b, dict)]
        whole = config.get("narrowed") is False
        lost = sum(1 for n in nodes_by_id.values()
                   if n.type == "deploy" and n.props.get("unread"))
        for _, group in sorted(packages.items()):
            if not is_held(group):
                continue
            edge = representative(group, nodes_by_id)
            block = next((b for b in blocks if apart_matches(b, edge, nodes_by_id)), {})
            own, others, builds = held_on(edge, edges, nodes_by_id, layout)
            note = f' ("{edge.props["apart_note"]}")' if edge.props.get("apart_note") else ""
            component = component_of(edge, nodes_by_id)
            named = str(block.get("service") or edge.props.get("service", ""))
            absent = not won and whole and not lost
            refused = sorted({where_of(e, nodes_by_id) for e in edges
                              if e.type == CLAIM_TYPE and block
                              and apart_matches(block, e, nodes_by_id)})
            why = (f"`{where_of(edge, nodes_by_id)}` is declared apart "
                   f"(`{block.get('file', '')}:{block.get('line', 0)}`){note} but stays on "
                   f"`{service}`: its component's own node `{own}` holds the claims of "
                   f"{', '.join(f'`{o}`' for o in others)}, and a refusal never joins it "
                   f"there. Declared apart from this service, it is never canonical here"
                   + (f"; `{won}` is, by the workspace's block." if won else
                      f", and no other package this run read claims `{service}`; the "
                      f"report's note says what it could not see, so a whole-workspace "
                      f"build may find one." if not whole else
                      f", and no other package this run read claims `{service}`; what "
                      f"{lost} deployment file{' deploys' if lost == 1 else 's deploy'} is "
                      f"unknown (see {'its row' if lost == 1 else 'their rows'}), and "
                      f"{'it' if lost == 1 else 'any of them'} may claim `{service}`."
                      if lost else
                      f", and no other package claims `{service}`, so it has no canonical "
                      f"deployment.")
                   + (f" This block refuses every claim `{component}` makes on `{named}`: "
                      f"{', '.join(refused)}; removing it releases them all."
                      if len(refused) > 1 else "")
                   + " If the chart is that service after all, remove the block"
                   + (f"; if it deploys what {', '.join(f'`{b}`' for b in builds)} "
                      f"build{'s' if len(builds) == 1 else ''}, the graph cannot place it "
                      f"there yet (the image link, filed as T589)" if builds else "")
                   + "; otherwise this row is the record of where it stays"
                   + (f". {accounting.NOT_READ}" if absent else ""))
            res.unresolved.append(unresolved_row(
                self.name, component, "apart-held", str(block.get("file") or ""),
                int(block.get("line") or 0), why, service=named, node=service))

    def _unmatched(self, config: dict, block: dict, nodes: list[Node], edges: list[Edge],
                   nodes_by_id: dict[str, Node], layout: dict[str, str]) -> str:
        """The sentence of a `canonical-unmatched` row for this block, or "" where it
        designates something the graph read - or is not to be judged (T573 decision 3).

        (a) the graph read no claim of the owner on the service, and the path, if any,
        reaches no host map of the owner (a path ranking host maps is used whatever its
        service, D15b-95); (b) the owner claims the service, but the path reaches none of
        those files. One row per block; (a) wins over (b). A path naming one of the owner's
        unread files says so directly."""
        owner, service = str(block.get("owner", "")), str(block.get("service", ""))
        if not accounting.read_gate(config, owner, nodes, edges):
            return ""
        r = accounting.reading(config, owner, nodes, edges, nodes_by_id)
        path = _canonical.claimed_path(block, owner) or ""
        written = str(block.get("path") or "").strip()
        if path and not _canonical.is_root_path(block):
            for node in r.unread:
                if str(node.props.get("path", "")).strip("/") == path:
                    return accounting.named_unread(written, owner, node)
        # A path ranking the owner's host maps is used, whatever its service (D15b-95) - a
        # root path too, which ranks every one of them: the host-map clause names `.` for a
        # chart at the root, and followed, that is accepted (review B15b-3).
        if written and any(_canonical.designates(block, owner, n.props,
                                                 str(n.props.get("path", "")))
                           for n in r.host_maps):
            return ""
        mine = [e for e in r.claims if _logical(e.dst) == norm_id(service)]
        if path and not _canonical.is_root_path(block):
            if any(_canonical.designates(block, owner, _node_props(e, nodes_by_id),
                                         e.source_file) for e in mine):
                return ""
        elif mine:
            return ""
        if mine:
            files = sorted({e.source_file for e in mine})
            one = len({package_of(e, nodes_by_id, layout) for e in mine}) == 1
            return (accounting.prefix(r)
                    + f"`{written}` designates no file of `{owner}` deploying `{service}` - a "
                      f"typo, or a file no reader reads. The files that do: "
                      f"{', '.join(f'`{f}`' for f in files)}; name one of them"
                    + (", or remove the `path` to let `owner` decide alone" if one else ""))
        head = (f"The graph read no deployment of `{service}` in `{owner}`, so this block "
                f"designates nothing it read.")
        return accounting.none_read(r, owner, head, refusing=False)

    def _rank_held(self, edges: list[Edge], packages: dict[str, list[tuple[int, Edge]]],
                   decl: dict | None, nodes_by_id: dict[str, Node]) -> tuple[str, str] | None:
        """Ruling 22: held claims rank among themselves. A block whose `owner` refused its
        claims here, and whose `path` selects one of that owner's held packages among them by
        decision 2's rule, ranks them: the others damped and stamped `superseded_by` the named
        one's file; the named one keeps its confidence; none becomes canonical, and the node's
        own decision among its other packages is untouched (ruling 19). An owner-only block
        selects none of two held packages, and ranks nothing. Returns (the named file, the
        owner) where it ranked."""
        if decl is None or not _canonical.claimed_path(decl, str(decl.get("owner") or "")):
            return None
        owner = str(decl.get("owner") or "").strip()
        mine = {key: group for key, group in packages.items()
                if is_held(group) and any(component_of(e, nodes_by_id) == owner
                                          for _, e in group)}
        if len(mine) < 2:
            return None
        named = by_path(decl, mine, nodes_by_id)
        if named is None:
            return None
        named_file = where_of(representative(mine[named], nodes_by_id), nodes_by_id)
        for key, group in mine.items():
            if key == named:
                continue
            for i, _ in group:
                edges[i] = replace(edges[i], confidence=round(edges[i].confidence * DAMPING, 4),
                                   props={**edges[i].props, "superseded_by": named_file})
        return named_file, owner

    def _contest_why(self, config: dict, service: str,
                     packages: dict[str, list[tuple[int, Edge]]], decl: dict | None,
                     ranked: tuple[str, str] | None, offered: list[dict], files: list[str],
                     claims: list[tuple[int, Edge]], edges: list[Edge],
                     nodes_by_id: dict[str, Node], layout: dict[str, str]) -> str:
        """The contested row's sentence (batch 15b): what decides it, and one clause per
        claim an `[[apart]]` block matched (D15b-73, D15b-85) - so a refusal is never a
        silent no-op - and, for a name-joined claim, the block that would refuse it
        (D15b-65)."""
        from tools.graph.links.namejoin import apart_matches, apart_offer
        logical = _logical(service)
        cite = (f"`{decl.get('file')}:{decl.get('line')}`"
                if decl is not None and decl.get("file") else "the workspace config")
        head = f"{counted(len(packages), len(files))} claim {service}"
        blocks = [b for b in config.get("apart") or [] if isinstance(b, dict)]

        def block_of(edge: Edge) -> dict:
            return next((b for b in blocks if apart_matches(b, edge, nodes_by_id)), {})

        def where_block(edge: Edge) -> str:
            block = block_of(edge)
            return f"{block.get('file', '')}:{block.get('line', 0)}"

        live = [key for key, group in packages.items() if not is_held(group)]
        if not live:
            # D15b-101: nothing on the node can be ranked (ruling 19), so the row offers no
            # choice and says why - this REPLACES both offers, so neither ever renders an
            # empty list (D15b-109).
            held_cites = sorted({where_block(e) for _, e in claims
                                 if apart_state(e) == "held"})
            why = (f"{head}, and every package claiming this node is declared apart from "
                   f"`{logical}` ({', '.join(f'`{c}`' for c in held_cites)}) and held here, its "
                   f"own component's node holding another package's claim, so nothing here can "
                   f"be ranked canonical; if one of them is this service, remove its "
                   f"`[[apart]]` block")
            if decl is not None and ranked is not None:
                why += (f"; the workspace's block for this service ({cite}) ranks "
                        f"`{ranked[0]}` above the other packages of `{ranked[1]}`, but cannot "
                        f"make any of them canonical here, since every package here is held")
            elif decl is not None:
                why += (f"; the workspace's block for this service ({cite}) cannot decide it "
                        f"either, since every package here is held")
        else:
            options = " | ".join(choice_text(c) for c in offered)
            if decl is not None and ranked is not None:
                # D15b-133: said wherever the block ranks held claims, so the one block per
                # service is never changed by someone not told what it still does.
                why = (f"{head}, and the workspace's block for this service ({cite}) decides "
                       f"none of these packages, but ranks `{ranked[0]}` above `{ranked[1]}`'s "
                       f"other held packages; changing it to one of the choices below ends "
                       f"that ranking. Change it to one of: {options}")
            elif decl is not None:
                why = (f"{head}, and the workspace's block for this service ({cite}) decides "
                       f"none of these packages; change it to one of: {options}")
            elif blocks_for(config, service):
                # T623: blocks name this service, but none designates one of these packages -
                # each decides other nodes or nothing - so a block of its own decides this
                # node, and changing one of them would undo what it decides.
                named = blocks_for(config, service)
                cites = ", ".join(f"`{b.get('file')}:{b.get('line')}`" if b.get("file")
                                  else "the workspace config" for b in named)
                why = (f"{head}, and no [[canonical]] block for this service designates one of "
                       f"these packages (the {len(named)} naming it, at {cites}, designate "
                       f"others or nothing). Add one naming `service` and one of: {options}")
            else:
                why = (f"{head}, and the workspace config does not say which is canonical. "
                       f"Add a [[canonical]] block naming `service` and one of: {options}")
            why += (" - an owner that reads several of these packages decides none of them, "
                    "so it is offered with the path that picks one"
                    if any(c.get("path") for c in offered) else "")
            why += (". Nothing here can decide it: the packages are equally real, and a guess "
                    "would attach configuration edges to the wrong service while looking "
                    "entirely correct")
        for _, edge in sorted(claims, key=lambda c: (where_of(c[1], nodes_by_id), c[1].line)):
            state = apart_state(edge)
            if not state:
                continue
            file, block = where_of(edge, nodes_by_id), where_block(edge)
            note = f' ("{edge.props["apart_note"]}")' if edge.props.get("apart_note") else ""
            if state == "held":
                own, others, _ = held_on(edge, edges, nodes_by_id, layout)
                why += (f". `{file}` is declared apart (`{block}`){note} but stays on this "
                        f"node: its component's own node `{own}` holds the claims of "
                        f"{', '.join(f'`{o}`' for o in others)}, and a refusal never joins it "
                        f"there. Declared apart from this service, it cannot be ranked "
                        f"canonical here, so no choice below names it")
            elif state == "kept":
                why += (f". `{file}` is declared apart (`{block}`){note}; the block changes "
                        f"nothing here, since "
                        + ("it is placed by its own `build:`"
                           if edge.props.get("mapped_by") == "build"
                           else "it already sits on its component's own node"))
            else:
                why += (f". `{file}` is declared apart (`{block}`){note} and moved to its "
                        f"component's own node, with the claims of that component the block "
                        f"also matches; they contest here")
        joined = sorted({component_of(e, nodes_by_id) for _, e in claims
                         if e.props.get("joined_by") and not apart_state(e)} - {""})
        for deployer in joined:
            why += ". Or, " + apart_offer(logical, deployer, edges, nodes_by_id, layout)
        return why

    def _duplicates(self, res: ExtractResult, config: dict) -> None:
        """Two `[[canonical]]` blocks whose `service` names one service once normalised
        (D534-34): `my api` and `my-api` are one id. The installer refuses a repeated
        spelling but cannot normalise - it imports nothing - so the first block decides
        here, as every reader of the blocks takes the first, and the later one is named."""
        first: dict[tuple[str, str, str], tuple[int, dict]] = {}
        for i, block in enumerate(config.get("canonical") or [], start=1):
            if not isinstance(block, dict):
                continue
            written = str(block.get("service", ""))
            key = _same_block(block)
            if key not in first:
                first[key] = (i, block)
                continue
            # Cited at the ignored block's own line, now that the installer records it
            # (D15b-50); a fact about the config alone, never withheld by narrowing (D15b-56).
            kept_at, kept = first[key]
            where = (f" at {block.get('file')}:{block.get('line')}"
                     if block.get("file") else "")
            kept_where = (f" at {kept.get('file')}:{kept.get('line')}"
                          if kept.get("file") else "")
            res.unresolved.append({
                "extractor": self.name, "kind": "canonical-duplicate", "component": "",
                "service": key[0], "ignored": written,
                "source_file": str(block.get("file") or ""), "line": int(block.get("line") or 0),
                "why": (f"[[canonical]] block #{i}{where} (service = \"{written}\") names the "
                        f"same service, owner and path as block #{kept_at}{kept_where} once "
                        f"spellings are normalised, so it decides nothing: block #{kept_at} is "
                        f"the one every reader takes. Remove one of them")})


def _same_block(block: dict) -> tuple[str, str, str]:
    """What makes two blocks one once spellings are normalised (T623): the service as its id
    normalises it, the owner, and the path as `claimed_path` reads it. Blocks differing in
    owner or path are two blocks, each deciding the nodes it designates."""
    owner = str(block.get("owner") or "").strip()
    return (norm_id(str(block.get("service", ""))), owner,
            _canonical.claimed_path(block, owner) or "")


def apart_state(edge: Edge) -> str:
    """What an `[[apart]]` block did to this claim (`moved`, `held`, `kept`), "" if none."""
    return str(edge.props.get("apart") or "")


def _logical(service_id: str) -> str:
    """`svc:orders-api@deploy` → `orders-api` — the spelling a developer writes."""
    return service_id.split(":", 1)[-1].split("@", 1)[0]


def drafted(rows: list[dict]) -> list[str]:
    """The contested services `propose` drafts a block for, one per row - each node with a
    choice to offer (batch 15b, D15b-108) - by the row's own `service`, so two nodes of one
    name are two, as the draft's header counts them (review B15b-4: counted by name, the
    line said "1 of 2" over a draft holding both). A node every package of which is held
    offers none - its row says why (ruling 19) - and is never drafted."""
    return sorted(str(r["service"]) for r in rows
                  if r.get("kind") == "contested-service" and r.get("choices"))


def changed(rows: list[dict]) -> list[str]:
    """The drafted services whose node a block already designates and does not decide (T619):
    drafted as a change to that block, never as a second one, which would overlap it."""
    return sorted(str(r["service"]) for r in rows
                  if r.get("kind") == "contested-service" and r.get("choices")
                  and r.get("block"))


def propose(rows: list[dict]) -> str:
    """Contested-service rows → a paste-ready draft of `[[canonical]]` blocks.

    Drafted, never decided: every `owner`/`path` is left blank, and the blocks are
    grouped by the owners one answer can fill — the unit the real decision is made at.
    Deterministic for the same rows, so the file is diffable across builds. Only the
    row's `choices` are offered (D534-39), and they alone word the groups: never the row's
    `components`, which name held packages too - the fallback that drafted the very owners
    ruling 19 forbids (D15b-101). A service with no choice is left out, and the header says
    so (D15b-128).
    """
    every = sorted((r for r in rows if r.get("kind") == "contested-service"),
                   key=lambda r: _logical(r["service"]))
    contested = [r for r in every if r.get("choices")]
    # Each named by its row's `service`, which is how its row is found - two nodes of one
    # name are two (review B15b-4).
    left = [str(r["service"]) for r in every if not r.get("choices")]
    # T619: a node a block designates and does not decide is drafted as a change to that
    # block; only a node no block designates gets a new one (decision 41).
    changes = [r for r in contested if r.get("block")]
    groups: dict[tuple, list[dict]] = {}
    for r in contested:
        if r.get("block"):
            continue
        groups.setdefault(tuple(sorted({c["owner"] for c in r["choices"]})), []).append(r)

    lines = [
        "# canonical.proposed.toml — drafted by the graph build, decided by you.",
        "#",
        f"# {len(contested)} service(s) are each declared by more than one deployment "
        f"package, and",
        "# nothing in the files can say which declaration is authoritative. Every block",
        "# below carries one BLANK decision, in one of three forms: an `owner` to pick",
        "# from the list beside it; an owner pre-filled (one component holds every",
        "# package, so that much is fact) and a `path` to pick; or `owner` and `path` both",
        "# blank with the `# choice:` lines that decide - copy ONE choice into them. Move",
        "# the block into your workspace config and rebuild. Blanks cannot decide",
        "# silently: a blank owner is refused loudly, a blank path stays reported. This",
        "# file is REGENERATED per build: move blocks out, never edit them here.",
    ] if groups else [
        # Review B21-11: changes alone - comments - so nothing here is a block to fill.
        "# canonical.proposed.toml — drafted by the graph build, decided by you.",
        "#",
        f"# {len(contested)} service(s) are each declared by more than one deployment "
        f"package, and",
        "# nothing in the files can say which declaration is authoritative. Each already",
        "# has a block that designates its packages and decides none, so each is drafted",
        "# below as a CHANGE to that block, in comments: make it in your workspace config",
        "# and rebuild. This file is REGENERATED per build: never edit it here.",
    ]
    if changes and groups:
        one = len(changes) == 1
        lines += ["#", f"# {len(changes)} of them already {'has' if one else 'have'} a block "
                       f"that designates {'its' if one else 'their'} packages and decides "
                       f"none: {'it is' if one else 'each is'} drafted at the end as a CHANGE "
                       f"to that block, in comments - a second block would overlap it, and "
                       f"the first decides."]
    if left:
        lines += ["#", f"# {len(left)} more could not be drafted - every package claiming "
                       f"{'it' if len(left) == 1 else 'them'} is declared apart and held, so "
                       f"nothing there can be ranked - see "
                       f"{'its' if len(left) == 1 else 'their'} contested-service rows: "
                       f"{', '.join(f'`{name}`' for name in left)}"]
    for key in sorted(groups, key=lambda k: (-len(groups[k]), k)):
        rows_ = groups[key]
        lines += ["", f"# ── {len(rows_)} service(s) whose rankable packages are exactly: "
                      f"{', '.join(key)}"]
        # The group hint only where one answer CAN fill the group: an owner list, never a
        # per-service path (D534-51).
        if len(key) > 1 and all(not any(c.get("path") for c in (r.get("choices") or []))
                                for r in rows_):
            lines.append("# If one of these trees is authoritative for all of them, one "
                         "answer fills the group.")
        for r in rows_:
            claims = r.get("claims") or []
            shown = " · ".join(claims[:3]) + (f" · {len(claims) - 3} more"
                                              if len(claims) > 3 else "")
            lines += ["", "[[canonical]]", f'service = "{_logical(r["service"])}"']
            lines += _drafted(r["choices"])
            lines.append(f"# claims: {shown}")
    for r in changes:
        claims = r.get("claims") or []
        shown = " · ".join(claims[:3]) + (f" · {len(claims) - 3} more"
                                          if len(claims) > 3 else "")
        lines += ["", f"# ── change the block at {r['block']} (service = "
                      f"\"{_logical(r['service'])}\") to ONE of:"]
        lines += [f"#   {choice_text(c)}" for c in r["choices"]]
        lines.append(f"# claims: {shown}")
    return "\n".join(lines) + "\n"


def _drafted(offered: list[dict]) -> list[str]:
    """The blank decision for one block, from its choices alone: owner-only choices as an
    owner list; one owner holding every package as that owner (fact) and a path slot;
    anything mixed as the list of choices, each of which decides (D534-39)."""
    owners = [c["owner"] for c in offered if not c.get("path")]
    if len(owners) == len(offered):
        return [f'owner = ""            # one of: {" | ".join(owners)}']
    if len({c["owner"] for c in offered}) == 1 and all(c.get("path") for c in offered):
        return [f'owner = "{offered[0]["owner"]}"',
                'path = ""             # one of: '
                + " | ".join(f'"{c["path"]}"' for c in offered)]
    return ['owner = ""            # fill in ONE of the choices below',
            'path = ""             # only where the choice names a path',
            *(f"# choice: {choice_text(c)}" for c in offered)]


LINK_PASSES = (CanonicalPass(),)
