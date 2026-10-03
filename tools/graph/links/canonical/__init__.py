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
from tools.graph.core.model import Edge, ExtractResult, Node, norm_id
from tools.graph.extractors import _canonical

#: The edge type that carries a claim on a service.
CLAIM_TYPE = "DG_DEPLOYS"

#: What a losing claim's confidence is multiplied by. Visible, and ranked below the winner.
DAMPING = 0.5


#: One normalising reading of the component list, shared with compose and the join so the
#: three cannot disagree about where a component is (D534-42). Re-exported here because
#: the package helpers below are its consumers.
workspace_layout = _canonical.workspace_layout


def decl_for(config: dict, service_id: str) -> dict | None:
    """The `[[canonical]]` block covering this service node, if the workspace wrote one.

    Matched on the logical name inside the id rather than on the id itself: a developer
    writes `service = "orders-api"`, not the internal identity string, and requiring the
    latter would make the file unwritable without reading this source. Normalised as the
    id is (D534-24): `my api` and `my-api` name one service, and the FIRST block decides.
    """
    logical = service_id.split(":", 1)[-1].split("@", 1)[0]
    for block in config.get("canonical", []) or []:
        if norm_id(str(block.get("service", ""))) == logical:
            return block
    return None


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
    compose's default override names its base (T588), a file of the same component."""
    node = nodes_by_id.get(edge.src)
    package = node.props.get("package") if node is not None else None
    return package if isinstance(package, str) else ""


def package_of(edge: Edge, nodes_by_id: dict[str, Node], layout: dict[str, str]) -> str:
    """The package a claim belongs to: one FILE is one package (D534-18), unless its
    deployment node names the file whose package it is part of (T588: compose's default
    pair is one package, named by its base).

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
    return posixpath.normpath(posixpath.join(layout[component], named or edge.source_file))


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
    package.

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


def select_package(packages: dict[str, list[tuple[int, Edge]]], decl: dict | None,
                   nodes_by_id: dict[str, Node]) -> tuple[str | None, str]:
    """Which PACKAGE the declaration selects, and how. `None` means it did not decide.

    Decided per package, so one package's claims on a service (a `web` and a `worker`
    built from one image) win or lose together (D9-21). A package read by two components
    matches an `owner` naming EITHER of them (D534-30) - and an `owner` that reads two of
    the service's packages (a component holding two of them, such as a compose base and
    its prod file) selects NEITHER, because it names no one package: it takes a `path` as
    well (D534-39). `choices` says which owners and paths decide, so no draft or row offers
    one that does not."""
    if decl is None:
        return None, ""

    # `path` first: it is the narrower statement, and the only one that can separate two
    # packages living in ONE component — which `owner` cannot, since both would name it.
    # Matched by the extract layer's own rule (T530): a bare `startswith` let
    # `charts/orders` match `charts/orders-legacy/` as well, and two matches decide nothing.
    if path := (decl.get("path") or "").strip().strip("/"):
        matches = [src for src, claims in packages.items()
                   if any(_canonical.covers(decl, component_of(e, nodes_by_id), e.source_file)
                          for _, e in claims)]
        if len(matches) == 1:
            return matches[0], f"path={path}"
        return None, ""

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
                continue            # one package's claims are not a contest (D9-21)
            decl = decl_for(ctx.config, service)
            winner, how = select_package(packages, decl, nodes_by_id)
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
                    "why": (f"{counted(len(packages), len(files))} claim {service}, and the "
                            f"workspace config does not say which is canonical. Add a "
                            f"[[canonical]] block naming `service` and one of: "
                            f"{' | '.join(choice_text(c) for c in offered)}"
                            + (" - an owner that reads several of these packages decides "
                               "none of them, so it is offered with the path that picks one"
                               if any(c.get("path") for c in offered) else "")
                            + ". Nothing here can decide it: the packages are equally real, "
                              "and a guess would attach configuration edges to the wrong "
                              "service while looking entirely correct"),
                })
                continue

            won = where_of(standing[winner], nodes_by_id)
            for i, edge in claims:
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
        return res

    def _duplicates(self, res: ExtractResult, config: dict) -> None:
        """Two `[[canonical]]` blocks whose `service` names one service once normalised
        (D534-34): `my api` and `my-api` are one id. The installer refuses a repeated
        spelling but cannot normalise - it imports nothing - so the first block decides
        here, as every reader of the blocks takes the first, and the later one is named."""
        first: dict[str, int] = {}
        for i, block in enumerate(config.get("canonical") or [], start=1):
            if not isinstance(block, dict):
                continue
            written = str(block.get("service", ""))
            key = norm_id(written)
            if key not in first:
                first[key] = i
                continue
            res.unresolved.append({
                "extractor": self.name, "kind": "canonical-duplicate", "component": "",
                "service": key, "ignored": written, "source_file": "", "line": 0,
                "why": (f"[[canonical]] block #{i} (service = \"{written}\") names the same "
                        f"service as block #{first[key]} once spellings are normalised, so "
                        f"it decides nothing: block #{first[key]} is the one every reader "
                        f"takes. Remove one of them")})


def _logical(service_id: str) -> str:
    """`svc:orders-api@deploy` → `orders-api` — the spelling a developer writes."""
    return service_id.split(":", 1)[-1].split("@", 1)[0]


def propose(rows: list[dict]) -> str:
    """Contested-service rows → a paste-ready draft of `[[canonical]]` blocks.

    Drafted, never decided: every `owner`/`path` is left blank, and the blocks are
    grouped by WHO is contesting — the unit the real decision is made at. Deterministic
    for the same rows, so the file is diffable across builds. Where a row carries
    `choices` (D534-39), only choices that decide are offered: an owner that reads several
    of the packages appears with the path that picks one.
    """
    contested = sorted((r for r in rows if r.get("kind") == "contested-service"),
                       key=lambda r: _logical(r["service"]))
    groups: dict[tuple, list[dict]] = {}
    for r in contested:
        groups.setdefault(tuple(r.get("components") or ("?",)), []).append(r)

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
    ]
    for key in sorted(groups, key=lambda k: (-len(groups[k]), k)):
        rows_ = groups[key]
        lines += ["", f"# ── {len(rows_)} service(s) contested by exactly: "
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
            lines += _drafted(r.get("choices"), key)
            lines.append(f"# claims: {shown}")
    return "\n".join(lines) + "\n"


def _drafted(offered: list[dict] | None, key: tuple) -> list[str]:
    """The blank decision for one block: owner-only choices as an owner list; one owner
    holding every package as that owner (fact) and a path slot; anything mixed as the
    list of choices, each of which decides (D534-39)."""
    if offered:
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
    if len(key) > 1:
        return [f'owner = ""            # one of: {" | ".join(key)}']
    # Both claims live in ONE component, so `owner` is the report restated (fact,
    # pre-filled) and cannot separate them — only a path prefix can, and the claims below
    # are the candidates.
    return [f'owner = "{key[0]}"',
            'path = ""             # the authoritative package\'s directory prefix']


LINK_PASSES = (CanonicalPass(),)
