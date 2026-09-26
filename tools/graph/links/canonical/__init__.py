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

from dataclasses import replace
from typing import Any

from tools.graph.core.model import Edge, ExtractResult, Node
from tools.graph.extractors import _canonical

#: The edge type that carries a claim on a service.
CLAIM_TYPE = "DG_DEPLOYS"

#: What a losing claim's confidence is multiplied by. Visible, and ranked below the winner.
DAMPING = 0.5


def _decl_for(config: dict, service_id: str) -> dict | None:
    """The `[[canonical]]` block covering this service node, if the workspace wrote one.

    Matched on the logical name inside the id rather than on the id itself: a developer
    writes `service = "orders-api"`, not the internal identity string, and requiring the
    latter would make the file unwritable without reading this source.
    """
    logical = service_id.split(":", 1)[-1].split("@", 1)[0]
    for block in config.get("canonical", []) or []:
        if block.get("service") == logical:
            return block
    return None


def _winner(claims: list[tuple[int, Edge]], decl: dict | None,
            nodes_by_id: dict[str, Node]) -> tuple[int | None, str]:
    """Which claim the declaration selects, and how. `None` means it did not decide."""
    if decl is None:
        return None, ""

    # `path` first: it is the narrower statement, and the only one that can separate two
    # packages living in ONE component — which `owner` cannot, since both would name it.
    # Matched by the extract layer's own rule (T530): a bare `startswith` let
    # `charts/orders` match `charts/orders-legacy/` as well, and two matches decide nothing.
    if path := (decl.get("path") or "").strip().strip("/"):
        matches = [i for i, e in claims
                   if _canonical.covers(decl, _component_of(e, nodes_by_id), e.source_file)]
        if len(matches) == 1:
            return matches[0], f"path={path}"
        return None, ""

    owner = (decl.get("owner") or "").strip()
    if owner:
        matches = [i for i, e in claims if _component_of(e, nodes_by_id) == owner]
        if len(matches) == 1:
            return matches[0], f"owner={owner}"
    return None, ""


def _component_of(edge: Edge, nodes_by_id: dict[str, Node]) -> str:
    """The component a claim was made from: its deployment node's own `component`."""
    return str(nodes_by_id.get(edge.src, Node(id="x", type="x")).props.get("component", ""))



#: What each `kind` this module emits promises its reader beyond `UNRESOLVED_COMMON`.
#: Declared HERE rather than in core: a central table would make core learn every
#: extractor's vocabulary, so adding a stack would force a core edit. The pipeline
#: checks these after every run and fails the build when a row does not carry them.
UNRESOLVED_FIELDS = {
    "contested-service": ("service", "claims", "components"),
}

class CanonicalPass:
    """Resolve contested services from the workspace's own declaration."""

    name = "mark-canonical"

    def link(self, ctx: Any, nodes: list[Node], edges: list[Edge]) -> ExtractResult:
        res = ExtractResult()
        nodes_by_id = {n.id: n for n in nodes}

        by_service: dict[str, list[tuple[int, Edge]]] = {}
        for i, edge in enumerate(edges):
            if edge.type == CLAIM_TYPE:
                by_service.setdefault(edge.dst, []).append((i, edge))

        for service, claims in sorted(by_service.items()):
            if len(claims) < 2:
                continue
            decl = _decl_for(ctx.config, service)
            winner, how = _winner(claims, decl, nodes_by_id)
            if winner is None:
                # `component` is empty ON PURPOSE and `components` carries the answer. This
                # finding is about a collision BETWEEN components, so naming one of them
                # would file a shared problem against a single owner and imply a decision
                # this pass has just refused to make. Empty is a real state here; absent
                # was not, and 206 rows in a real workspace carried neither — so a reader
                # could see that some service was contested and not by whom.
                claimants = sorted({
                    str(nodes_by_id.get(e.src, Node(id="x", type="x")).props.get("component")
                        or "") for _, e in claims} - {""})
                res.unresolved.append({
                    "extractor": self.name, "kind": "contested-service",
                    "component": "", "components": claimants,
                    "service": service,
                    "claims": sorted(f"{e.source_file}:{e.line}" for _, e in claims),
                    "source_file": sorted(e.source_file for _, e in claims)[0], "line": 0,
                    "why": (f"{len(claims)} deployment packages claim {service}, and the "
                            f"workspace config does not say which is canonical. Add a "
                            f"[[canonical]] block naming `service` and either `owner` (which "
                            f"component) or `path` (which package, when one component holds "
                            f"several). Nothing here can decide it: the packages are equally "
                            f"real, and a guess would attach configuration edges to the wrong "
                            f"service while looking entirely correct"),
                })
                continue

            for i, edge in claims:
                if i == winner:
                    edges[i] = replace(edge, canonical=True,
                                       props={**edge.props, "canonical_by": how})
                else:
                    # Damped, not dropped. The superseded variant stays in the graph because
                    # a variant about to break is exactly what blast radius exists to find.
                    edges[i] = replace(edge, canonical=False,
                                       confidence=round(edge.confidence * DAMPING, 4),
                                       props={**edge.props, "canonical_by": how,
                                              "superseded_by": edges[winner].source_file})
        return res


def _logical(service_id: str) -> str:
    """`svc:orders-api@deploy` → `orders-api` — the spelling a developer writes."""
    return service_id.split(":", 1)[-1].split("@", 1)[0]


def propose(rows: list[dict]) -> str:
    """Contested-service rows → a paste-ready draft of `[[canonical]]` blocks.

    Drafted, never decided: every `owner`/`path` is left blank, and the blocks are
    grouped by WHO is contesting — the unit the real decision is made at. Deterministic
    for the same rows, so the file is diffable across builds.
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
        "# below carries one BLANK decision: fill `owner` (which component) or `path`",
        "# (which package, when one component holds several — its owner is pre-filled",
        "# because that much is fact), move the block into your workspace config, and",
        "# rebuild. Blanks cannot decide silently: a blank owner is refused loudly, a",
        "# blank path stays reported. This file is REGENERATED per build: move blocks",
        "# out, never edit them here.",
    ]
    for key in sorted(groups, key=lambda k: (-len(groups[k]), k)):
        rows_ = groups[key]
        lines += ["", f"# ── {len(rows_)} service(s) contested by exactly: "
                      f"{', '.join(key)}"]
        if len(key) > 1:
            lines.append("# If one of these trees is authoritative for all of them, one "
                         "answer fills the group.")
        for r in rows_:
            claims = r.get("claims") or []
            shown = " · ".join(claims[:3]) + (f" · {len(claims) - 3} more"
                                              if len(claims) > 3 else "")
            lines += ["", "[[canonical]]", f'service = "{_logical(r["service"])}"']
            if len(key) > 1:
                lines.append(f'owner = ""            # one of: {" | ".join(key)}')
            else:
                # Both claims live in ONE component, so `owner` is the report restated
                # (fact, pre-filled) and cannot separate them — only a path prefix can,
                # and the claims below are the candidates.
                lines.append(f'owner = "{key[0]}"')
                lines.append('path = ""             # the authoritative package\'s '
                             'directory prefix')
            lines.append(f"# claims: {shown}")
    return "\n".join(lines) + "\n"


LINK_PASSES = (CanonicalPass(),)
