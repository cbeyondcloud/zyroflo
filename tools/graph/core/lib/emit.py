"""Deterministic JSONL emission.

Determinism is a feature, not tidiness: the committed graph snapshot is diffed in harness
PRs and `rebuild == snapshot` doubles as an extractor regression test. Both require that
the same estate always serializes byte-identically.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from ..model import Edge, ExtractResult, Node, SCHEMA_VERSION


def write_jsonl(path: Path, result: ExtractResult, *, extractor: str = "") -> int:
    """Write nodes then edges as sorted JSONL. Returns the number of records written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = sorted({n.to_jsonl() for n in result.nodes}) + sorted({e.to_jsonl() for e in result.edges})
    header = json.dumps(
        {"kind": "header", "schema_version": SCHEMA_VERSION, "extractor": extractor},
        sort_keys=True,
    )
    path.write_text("\n".join([header, *lines]) + "\n", encoding="utf-8")
    return len(lines)


def read_jsonl(path: Path) -> ExtractResult:
    """Read back a JSONL shard, ignoring the header record.

    A row missing a field its kind promised is REFUSED, by `rows.check_row` — the same
    rule `store/load.py` applies, imported rather than restated. This module used to
    default an absent `provenance` to `EXTRACTED`, which is the taxonomy's highest-trust
    value: a field lost in transit came back as the strongest claim the graph can make.
    """
    from ..model import Provenance
    from .rows import check_row

    result = ExtractResult()
    if not path.exists():
        return result
    # Numbered, because the refusal has to send a person to a row they can open.
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        rec = json.loads(line)
        kind = rec.pop("kind", None)
        if kind == "node":
            check_row("node", rec, where=f"{path}:{number}")
            result.nodes.append(Node(**rec))
        elif kind == "edge":
            check_row("edge", rec, where=f"{path}:{number}")
            rec["provenance"] = Provenance(rec["provenance"])
            result.edges.append(Edge(**rec))
    return result


def write_unresolved(path: Path, rows: Iterable[dict]) -> int:
    """Persist what an extractor could not resolve, so coverage is measurable.

    Unresolved references are the honest counterpart to the graph: they are what a 'zero
    silent drops' claim is checked against.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = sorted({json.dumps(r, sort_keys=True, ensure_ascii=False) for r in rows})
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return len(lines)
