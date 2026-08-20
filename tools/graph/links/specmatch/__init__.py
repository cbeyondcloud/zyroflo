"""Spec alignment: join a context-free spec path to the exposed path that serves it.

Item 93. A spec writes `/v1/detail/{id}`; the controller serves it as
`/orders/v1/detail/{}` because the context path is baked into exposure — so the two
sides of "is this endpoint specified?" never meet at the id level, and the coverage
metric grades honest contracts as missing. Measured before designed: on a real estate
195 of 471 spec-only paths equal an exposed path minus its ONE leading segment, and the
reverse direction (spec minus a segment matching an exposed path) measured ZERO — so
only this direction exists here.

The rule is deliberately narrow, because a wrong spec-to-endpoint join would grade the
wrong service's contract as covered:

* **one segment** — the exposed path is exactly one path segment plus the spec path;
* **unique** — exactly one exposed endpoint matches; two candidates are a recorded
  `ambiguous-spec-alignment` row, never a choice;
* **additive** — the emitted edge joins the spec to the EXPOSED endpoint at confidence
  0.8 (the segment is inferred from shape, not read from a file); the spec's original
  edge and node remain untouched, because the spec really declares that string.

A link pass rather than an extractor: the spec usually lives in a different component
than the service it describes, so no single extractor can see both sides.
"""
from __future__ import annotations

from typing import Any

from tools.graph.core.model import Edge, ExtractResult, Node, Provenance

#: What each `kind` this module emits promises beyond the common fields.
UNRESOLVED_FIELDS = {
    "ambiguous-spec-alignment": ("spec_path", "candidates"),
}


def _one_segment_prefix(exposed_path: str, spec_path: str) -> str | None:
    """`/orders` when exposed == /orders + spec, None otherwise."""
    if not exposed_path.endswith(spec_path) or exposed_path == spec_path:
        return None
    prefix = exposed_path[: -len(spec_path)]
    if prefix.startswith("/") and prefix.count("/") == 1 and len(prefix) > 1:
        return prefix
    return None


class SpecAlignPass:
    name = "spec-align"

    def link(self, ctx: Any, nodes: list[Node], edges: list[Edge]) -> ExtractResult:
        res = ExtractResult()
        exposed = sorted({e.dst for e in edges if e.type == "DG_EXPOSES"})
        spec_edges = [e for e in edges if e.type == "DG_SPECIFIES"]
        spec_only = sorted({e.dst for e in spec_edges} - set(exposed))

        for spec_ep in spec_only:
            spec_path = spec_ep.split(":", 1)[1]
            candidates = [ep for ep in exposed
                          if _one_segment_prefix(ep.split(":", 1)[1], spec_path)]
            if not candidates:
                continue
            if len(candidates) > 1:
                first = next(e for e in spec_edges if e.dst == spec_ep)
                res.unresolved.append({
                    "extractor": self.name, "component": "", "kind":
                    "ambiguous-spec-alignment", "source_file": first.source_file,
                    "line": first.line, "spec_path": spec_path,
                    "candidates": len(candidates),
                    "why": (f"{len(candidates)} exposed endpoints are one segment plus "
                            f"this spec path, and choosing one would grade the wrong "
                            f"service's contract as covered")})
                continue
            target = candidates[0]
            segment = _one_segment_prefix(target.split(":", 1)[1], spec_path)
            for spec_edge in sorted((e for e in spec_edges if e.dst == spec_ep),
                                    key=lambda e: (e.src, e.source_file, e.line)):
                res.edges.append(Edge(
                    src=spec_edge.src, type="DG_SPECIFIES", dst=target,
                    source_file=spec_edge.source_file, line=spec_edge.line,
                    extractor=self.name, confidence=0.8,
                    provenance=Provenance.INFERRED,
                    props={"via": "context-suffix", "segment": segment,
                           "aligned_from": spec_ep}))
        return res


LINK_PASSES = (SpecAlignPass(),)
