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

#: What an `unknown-record-kind` row promises beyond `UNRESOLVED_COMMON` — nothing; the
#: common fields already say which shard, which line, and what kind was found there.
UNRESOLVED_FIELDS: dict[str, tuple[str, ...]] = {
    "unknown-record-kind": (),
}


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
    """Read back a JSONL shard: validate its header, and count what it cannot interpret.

    A row missing a field its kind promised is REFUSED, by `rows.check_row` — the same
    rule `store/load.py` applies, imported rather than restated. This module used to
    default an absent `provenance` to `EXTRACTED`, which is the taxonomy's highest-trust
    value: a field lost in transit came back as the strongest claim the graph can make.

    Two more were like it (T111 / W11 A2-4). The header's `schema_version` was written by
    every shard and read by nothing, so a shard from a schema this build cannot interpret
    parsed anyway, one field at a time, until something downstream broke on a shape nobody
    checked for — now it is refused by name. And the `kind` dispatch below had no `else`,
    so any record that was neither `node`, `edge` nor `header` fell through in silence;
    that is exactly the "graph that looks complete but isn't" CLAUDE.md warns is worse
    than one that admits its gaps, so it is now counted into `unresolved` instead, the
    same accounting `write_unresolved` already gives a reference an extractor could not
    resolve.
    """
    from ..model import Provenance, unresolved_row
    from .rows import check_row

    result = ExtractResult()
    if not path.exists():
        return result
    shard_extractor = ""
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
        elif kind == "header":
            shard_extractor = str(rec.get("extractor") or "")
            version = rec.get("schema_version")
            if version != SCHEMA_VERSION:
                raise ValueError(
                    f"{path}:{number}: shard header declares schema_version={version!r}, "
                    f"this build only reads {SCHEMA_VERSION!r} — rebuild "
                    f"(tools/graph/build.py) rather than reading a shard written by a "
                    f"schema version this reader does not understand")
        else:
            # Zero silent drops: a kind this reader does not recognise is counted, not
            # skipped past. `line` and `source_file` send a human to the exact row.
            result.unresolved.append(unresolved_row(
                "emit.read_jsonl", shard_extractor, "unknown-record-kind", str(path),
                number,
                f"record kind {kind!r} is neither node, edge nor header, and this reader "
                f"has no other way to interpret it"))
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
