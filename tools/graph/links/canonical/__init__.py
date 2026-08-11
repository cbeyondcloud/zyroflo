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
    if path := (decl.get("path") or "").strip().strip("/"):
        matches = [i for i, e in claims if e.source_file.strip("/").startswith(path)]
        if len(matches) == 1:
            return matches[0], f"path={path}"
        return None, ""

    owner = (decl.get("owner") or "").strip()
    if owner:
        matches = [i for i, e in claims
                   if nodes_by_id.get(e.src, Node(id="x", type="x")).props.get("component")
                   == owner]
        if len(matches) == 1:
            return matches[0], f"owner={owner}"
    return None, ""



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


LINK_PASSES = (CanonicalPass(),)
