"""Estate-agnostic node/edge meta-model.

Every edge carries its own provenance: which file and line produced it, which extractor,
how confident, and whether the fact was read directly or inferred. That is what lets a
context pack cite `source_file:line` for every row a human is asked to trust.

Nothing here names a language, framework, or estate — see tests/test_core_genericity.py.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any

SCHEMA_VERSION = 1

#: Graph namespace applied to every node label, so the domain layer never collides with
#: an optional symbol layer (e.g. CodeGraphContext) sharing the same database.
NAMESPACE = "DG"


class Provenance(str, Enum):
    """How a fact was obtained. Taxonomy convention borrowed from graphify (no code)."""

    EXTRACTED = "EXTRACTED"    # read directly from a file (a literal, a key, a mapping)
    INFERRED = "INFERRED"      # derived by joining two extracted facts
    AMBIGUOUS = "AMBIGUOUS"    # pattern/heuristic match; may be wrong, kept but dampened


_ID_SAFE = re.compile(r"[^a-zA-Z0-9._@/{}:-]+")

#: An edge type becomes a Cypher relationship type, and relationship types cannot be
#: parameterized — the store has to interpolate them. Constraining the type here is what
#: makes that interpolation safe, so the rule lives with the data rather than in a comment
#: at the injection site.
_REL_TYPE_SAFE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def norm_id(value: str) -> str:
    """Normalize an id fragment: collapse unsafe runs to '-', strip, case-preserving.

    Normalization is lossy for anything outside the safe set, and losing *everything* is the
    dangerous case: `norm_id("服务")` collapses to `""`, so two unrelated non-ASCII names
    would produce one id and silently merge into a single node. Any non-ASCII input
    therefore carries a short digest of the original, which keeps distinct names distinct.
    Pure-ASCII ids — every id the estate actually produces today — are untouched, so the
    committed snapshot does not churn.
    """
    raw = value.strip()
    normalized = _ID_SAFE.sub("-", raw).strip("-")
    if raw.isascii():
        return normalized
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]
    return f"{normalized}-{digest}" if normalized else f"u-{digest}"


def is_safe_rel_type(rel_type: str) -> bool:
    """Whether a type is safe to interpolate into Cypher as a relationship type."""
    return bool(_REL_TYPE_SAFE.match(rel_type))


@dataclass(frozen=True, slots=True)
class Node:
    """A domain entity. `type` is profile-defined (svc, ep, topic, table, cfg, ...)."""

    id: str
    type: str
    props: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id or not self.type:
            raise ValueError(f"node needs both id and type, got id={self.id!r} type={self.type!r}")

    @property
    def label(self) -> str:
        return f"{NAMESPACE}_{self.type}"

    def to_jsonl(self) -> str:
        return json.dumps({"kind": "node", **asdict(self)}, sort_keys=True, ensure_ascii=False)


@dataclass(frozen=True, slots=True)
class Edge:
    """A typed relationship with mandatory provenance.

    The MERGE key is (src, type, dst, source_file, line): the same relationship asserted by
    two different files stays two rows, so a human can see every place a coupling is
    declared rather than one collapsed summary.
    """

    src: str
    type: str
    dst: str
    source_file: str
    line: int
    extractor: str
    confidence: float = 1.0
    provenance: Provenance = Provenance.EXTRACTED
    canonical: bool = True
    run_id: str = ""
    props: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be in [0,1], got {self.confidence}")
        if self.line < 0:
            raise ValueError(f"line must be >= 0, got {self.line}")
        for name in ("src", "type", "dst", "source_file", "extractor"):
            if not getattr(self, name):
                raise ValueError(f"edge field '{name}' must be non-empty")
        if not is_safe_rel_type(self.type):
            raise ValueError(
                f"edge type must match {_REL_TYPE_SAFE.pattern} (it is interpolated into "
                f"Cypher as a relationship type and cannot be parameterized), got {self.type!r}"
            )

    @property
    def rel_type(self) -> str:
        return f"{NAMESPACE}_{self.type}"

    @property
    def merge_key(self) -> tuple[str, str, str, str, int]:
        return (self.src, self.type, self.dst, self.source_file, self.line)

    def to_jsonl(self) -> str:
        """Serialize for the committed snapshot.

        `run_id` is deliberately omitted: it identifies the *build*, not the estate, so
        including it would make every rebuild differ from the snapshot and turn the
        `rebuild == snapshot` regression check into permanent noise. The live build still
        carries it in memory and stamps it onto the stored relationship.
        """
        d = asdict(self)
        d["provenance"] = self.provenance.value
        d.pop("run_id", None)
        return json.dumps({"kind": "edge", **d}, sort_keys=True, ensure_ascii=False)


@dataclass
class ExtractResult:
    """What one extractor produced, plus what it could not resolve.

    `unresolved` is a first-class output, not a log line: an extractor that silently drops
    what it cannot resolve makes the graph look complete when it is not. Validation reports
    read this to compute coverage.
    """

    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    unresolved: list[dict[str, Any]] = field(default_factory=list)

    def extend(self, other: "ExtractResult") -> "ExtractResult":
        self.nodes.extend(other.nodes)
        self.edges.extend(other.edges)
        self.unresolved.extend(other.unresolved)
        return self

    def __len__(self) -> int:
        return len(self.nodes) + len(self.edges)
