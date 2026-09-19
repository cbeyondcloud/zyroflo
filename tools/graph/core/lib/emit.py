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

# Annotated as an ordinary typed module constant. `_declared()` in
# `test_unresolved_emission_sites.py` now reads the annotated `ast.AnnAssign` form as well
# as a plain `ast.Assign`, so this table no longer has to drop its type to be seen as
# declared (T412 closed that gap; this comment previously warned against re-annotating).
#
#: What an `unknown-record-kind` row promises beyond `UNRESOLVED_COMMON` — nothing; the
#: common fields already say which shard, which line, and what kind was found there.
UNRESOLVED_FIELDS: dict[str, tuple[str, ...]] = {
    "unknown-record-kind": (),
}


class ShardSchemaError(ValueError):
    """A shard's header declares no `schema_version` this build understands, carries no
    header at all, or carries a duplicate one (T111).

    A `ValueError`, so a caller that already catches the model's row refusals catches this
    too. The subclass exists so a caller that wants to treat THIS one cause differently from
    any other refusal in `read_jsonl` can name it precisely — `snapshot.load` does, folding
    it beside a digest-mismatched shard (both mean "do not trust these bytes as this
    build's graph") rather than let it crash a multi-component build over one shard.
    """


class ShardRowError(ValueError):
    """A node or edge row's JSON parsed, but could not be turned into a model object (T111
    round 4).

    Deliberately narrow: wraps only the act of interpreting ONE already-parsed row —
    `rows.check_row`, `Provenance(...)`, the `Node`/`Edge` constructors — not the rest of
    `read_jsonl`. Three of the ways that step can fail are BARE exceptions by DESIGN
    elsewhere in `core/`, not by accident: `rows.RowFieldError` and the model's own
    `__post_init__` checks are deliberately plain `ValueError` (`rows.py`'s own docstring:
    "a caller that catches the model's refusal must catch this one too"), and a row
    carrying a key the model does not accept raises a bare `TypeError` from the dataclass's
    own `__init__`. A caller cannot safely list those types one by one and catch them,
    because an unrelated bug elsewhere that happens to raise the SAME bare `ValueError` or
    `TypeError` would be caught right along with them — measured: one nonsense line turned
    every one of seven good shards into a printed "does not match its own recorded digest"
    and a recommendation to rebuild and re-publish, which would have overwritten them.
    Re-raising under a name this module owns, from this one narrow scope, is what lets a
    caller catch exactly the data refusals and nothing a bug could impersonate.
    """


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

    Three more were like it (T111 / W11 A2-4). The header's `schema_version` was written by
    every shard and read by nothing, so a shard from a schema this build cannot interpret
    parsed anyway, one field at a time, until something downstream broke on a shape nobody
    checked for — now it is refused by name (`ShardSchemaError`), and a shard carrying NO
    header at all is refused the same way rather than read as though it had none to check.
    And the `kind` dispatch below had no `else`, so any record that was neither `node`,
    `edge` nor `header` fell through in silence; that is exactly the "graph that looks
    complete but isn't" CLAUDE.md warns is worse than one that admits its gaps, so it is now
    counted into `unresolved` instead, the same accounting `write_unresolved` already gives
    a reference an extractor could not resolve.
    """
    from ..model import Provenance, unresolved_row
    from .rows import check_row

    result = ExtractResult()
    if not path.exists():
        return result
    shard_extractor = ""
    header_seen = False
    # Numbered, because the refusal has to send a person to a row they can open.
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        rec = json.loads(line)
        kind = rec.pop("kind", None)
        if kind == "node":
            try:
                check_row("node", rec, where=f"{path}:{number}")
                result.nodes.append(Node(**rec))
            except (ValueError, TypeError) as exc:
                raise ShardRowError(f"{path}:{number}: {exc}") from exc
        elif kind == "edge":
            try:
                check_row("edge", rec, where=f"{path}:{number}")
                rec["provenance"] = Provenance(rec["provenance"])
                result.edges.append(Edge(**rec))
            except (ValueError, TypeError) as exc:
                raise ShardRowError(f"{path}:{number}: {exc}") from exc
        elif kind == "header":
            if header_seen:
                # DECISION (round 3): refuse rather than let the second header silently
                # overwrite the first's declared extractor/schema_version — the same
                # "zero silent drops" reasoning already applied to a shard with NO header
                # (T111), extended to the mirror case. `write_jsonl` emits exactly one,
                # unconditionally, at the top; two is never something a real build wrote,
                # and a reader that quietly kept "whichever header comes last" would be
                # inventing a fact (which declaration governs this shard) nobody made.
                raise ShardSchemaError(
                    f"{path}:{number}: shard carries a SECOND header record — a "
                    f"well-formed shard (`write_jsonl`) writes exactly one — rebuild "
                    f"(tools/graph/build.py) rather than trusting whichever header a "
                    f"reader happens to keep last")
            header_seen = True
            shard_extractor = str(rec.get("extractor") or "")
            # Absent and explicit-null are NOT the same claim, although `.get(..., None)`
            # would read them identically: a header that never mentions `schema_version`
            # has not declared one, while `"schema_version": null` has declared it as null.
            # Worded so each refusal is true of the input that produced it.
            if "schema_version" not in rec:
                raise ShardSchemaError(
                    f"{path}:{number}: shard header declares no schema_version at all — "
                    f"this build only reads {SCHEMA_VERSION!r} — rebuild "
                    f"(tools/graph/build.py) rather than reading a shard from before "
                    f"schema versioning existed")
            version = rec["schema_version"]
            if version != SCHEMA_VERSION:
                raise ShardSchemaError(
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
    if not header_seen:
        # The version check above lives INSIDE the branch that only runs when a header IS
        # present, so the one shard whose schema is most obviously unknown — one that never
        # said what it was at all — used to be the one case that check never saw. Every real
        # writer (`write_jsonl`) emits a header unconditionally, even for zero rows, so a
        # shard reaching here with none is synthetic, damaged, or pre-dates this format.
        raise ShardSchemaError(
            f"{path}: shard carries no header record, so no schema_version was ever "
            f"declared — this build only reads {SCHEMA_VERSION!r} — rebuild "
            f"(tools/graph/build.py) rather than reading a shard missing its header")
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
