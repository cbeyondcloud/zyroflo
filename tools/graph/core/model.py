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


#: What EVERY unresolved row owes its reader, whatever its kind. Each answers a question a
#: human has to be able to ask of a validation report without opening the code: who reported
#: it, about which component, of what kind, in which file and line, and why it matters.
#: `line` may be 0 and `source_file` may be empty where the finding is about a component
#: rather than a place in it — absent is different from "does not apply", and only the first
#: is a defect.
UNRESOLVED_COMMON: tuple[str, ...] = (
    "extractor", "component", "kind", "source_file", "line", "why")


def unresolved_row(extractor: str, component: str, kind: str, source_file: str, line: int,
                   why: str, **extra: Any) -> dict[str, Any]:
    """Build an unresolved row carrying everything `UNRESOLVED_COMMON` requires.

    A constructor rather than a convention, for the same reason `run_id` is stamped centrally
    in the pipeline rather than trusted to each extractor: a field that thirty-nine call
    sites must each remember is a field some of them will forget, and the forgetting is
    invisible until someone reads the report and finds a row that names nothing.
    """
    return {"extractor": extractor, "component": component, "kind": kind,
            "source_file": source_file, "line": line, "why": why, **extra}


def check_unresolved(rows: list[dict[str, Any]], declared: dict[str, tuple[str, ...]],
                     extractor: str) -> list[str]:
    """Every way `rows` fails the contract, as messages naming the row and the missing field.

    **The contract is split deliberately.** `UNRESOLVED_COMMON` is enforced here because it
    is true of every row in any workspace. The per-`kind` extras come from the emitting
    extractor's own `UNRESOLVED_FIELDS`, and **not** from a table in core — a central list of
    every kind would make core learn the whole extractor vocabulary, so adding a stack would
    force a core edit, which is the exact boundary failure the M1 kill gate exists to catch.
    Core owns the mechanism; each extractor owns its own words.

    Returns messages rather than raising: a row missing a field is a defect in the harness,
    but discovering it one row at a time would hide the other two hundred, and aborting a
    long build over metadata would cost an adopter the graph they were actually waiting for.
    The caller collects, reports all of them, and exits non-zero.
    """
    problems: list[str] = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            problems.append(f"{extractor}: unresolved[{i}] is {type(row).__name__}, not a dict")
            continue
        kind = row.get("kind")
        required = tuple(UNRESOLVED_COMMON) + tuple(declared.get(str(kind), ()))
        for fieldname in required:
            # Present-but-empty is legal (an empty `source_file` is a real state); absent, or
            # None where a value was promised, is not. A row whose whole purpose is to name
            # what is missing must not itself be missing the name.
            if fieldname not in row or row[fieldname] is None:
                problems.append(
                    f"{extractor}: unresolved[{i}] kind={kind!r} is missing {fieldname!r} — "
                    f"a row of this kind promises it, so a reader cannot act on this one")
    return problems


#: The optional field naming a row's SHAPE within its kind. Absent by default: most kinds
#: have exactly one shape, and inventing a vocabulary for them would be ceremony. A kind that
#: genuinely has shapes declares them in its module's `UNRESOLVED_PATTERNS`, and every row of
#: that kind must then carry one from the declared set — because **"this kind has one shape"
#: and "nobody has classified this kind yet" must not be the same value.** They read
#: identically in a grouped report, and the second silently claims the thoroughness of the
#: first. That is this project's cardinal failure by another name, and the reason a pattern
#: is declared rather than merely permitted.
PATTERN_FIELD = "pattern"


@dataclass(frozen=True, slots=True)
class UnresolvedGroup:
    """One systematic finding, and every site that exhibits it.

    `rows` is the whole membership, never a sample. **Grouping is a way of reading the
    report, not a way of shortening it** — the count is what makes it adjudicable and the
    rows are what keep it actionable, and a group that dropped its members would have
    replaced 2,700 rows nobody reads with one line nobody can act on.
    """

    extractor: str
    kind: str
    pattern: str
    rows: tuple[dict[str, Any], ...]

    @property
    def count(self) -> int:
        return len(self.rows)

    @property
    def components(self) -> tuple[str, ...]:
        """Which components exhibit it — a pattern in one is a different fact from one in ten."""
        return tuple(sorted({str(r.get("component") or "") for r in self.rows} - {""}))

    @property
    def graded(self) -> bool:
        """Whether a pattern vocabulary applied, or this is a whole kind in one bucket.

        Read by the report so an ungraded group is labelled rather than presented as a
        finding: "everything of this kind" looks exactly like "one pattern" once it is a row
        in a summary table, and only one of them has been thought about.
        """
        return bool(self.pattern)


def group_unresolved(rows: list[dict[str, Any]]) -> list[UnresolvedGroup]:
    """Collapse rows into groups by (extractor, kind, pattern), largest first.

    The grouping key is deliberately **vocabulary-free**. Core does not know what a
    `key-conflict` is or which shapes one can take; it knows only that a row may name its own
    shape, and that rows naming the same shape are one finding. The vocabulary belongs to the
    extractor that emits it, on exactly the reasoning that keeps `UNRESOLVED_FIELDS` out of
    core: a central table of every pattern would make adding a stack a core edit.

    Ties break on the group key so a rebuild produces byte-identical output — a report that
    reorders itself between runs cannot be diffed, and a validation report nobody can diff is
    one nobody watches.
    """
    buckets: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        key = (str(row.get("extractor") or ""), str(row.get("kind") or ""),
               str(row.get(PATTERN_FIELD) or ""))
        buckets.setdefault(key, []).append(row)
    groups = [UnresolvedGroup(extractor=e, kind=k, pattern=p, rows=tuple(rs))
              for (e, k, p), rs in buckets.items()]
    return sorted(groups, key=lambda g: (-g.count, g.extractor, g.kind, g.pattern))


def check_patterns(rows: list[dict[str, Any]], declared: dict[str, tuple[str, ...]],
                   extractor: str) -> list[str]:
    """Every way `rows` misuses its module's declared pattern vocabulary.

    Two failures, and the second is the one worth the code. A row of a declared kind that
    carries **no** pattern would group under `""` beside the properly classified ones,
    producing a phantom "everything else" bucket that looks like a finding. A row carrying a
    pattern **outside** the declared set is a typo that silently becomes its own group of
    one — which is how a vocabulary rots into free text, one plausible spelling at a time.

    Returns messages rather than raising, and for the same reason as `check_unresolved`: the
    caller collects them all, reports them, and exits non-zero.
    """
    problems: list[str] = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            continue                    # `check_unresolved` owns that complaint; not twice
        kind = str(row.get("kind"))
        allowed = declared.get(kind)
        if allowed is None:
            continue                    # no vocabulary for this kind — grouped by kind alone
        value = row.get(PATTERN_FIELD)
        if not value:
            problems.append(
                f"{extractor}: unresolved[{i}] kind={kind!r} declares a pattern vocabulary "
                f"{sorted(allowed)} and this row names none, so it would group as a bucket "
                f"of leftovers that reads like a finding")
        elif value not in allowed:
            problems.append(
                f"{extractor}: unresolved[{i}] kind={kind!r} has pattern {value!r}, which is "
                f"not in the declared vocabulary {sorted(allowed)} — an undeclared pattern "
                f"becomes its own group of one and the vocabulary rots into free text")
    return problems


@dataclass
class ExtractResult:
    """What one extractor produced, plus what it could not resolve.

    `unresolved` is a first-class output, not a log line: an extractor that silently drops
    what it cannot resolve makes the graph look complete when it is not. Validation reports
    read this to compute coverage.

    Build rows with `unresolved_row`, and declare any field your `kind` promises beyond
    `UNRESOLVED_COMMON` in the extractor's `UNRESOLVED_FIELDS`; the pipeline checks both.
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
