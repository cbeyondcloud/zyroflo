"""A shard row that is MISSING a promised field is refused, at both readers, alike.

The two JSONL readers had drifted into two answers to one question. An edge row with no
`provenance` came back as `Provenance.EXTRACTED` — the *highest-trust* value in the
taxonomy — at `core/store/load.py` and at `core/lib/emit.py` both, while an explicitly
null one raised; and `load.py` turned an absent or null `line` into `0`, which is a
legal, meaningful, file-level claim, while its sibling `source_file` was refused. So a
field lost anywhere between the extractor and the reader was read back as the strongest
possible assertion about the estate, and nothing said so.

That is CLAUDE.md's cardinal failure with the graph's own provenance as the payload: *a
graph that looks complete but isn't is worse than one that admits its gaps, because a
human will act on it.* A context pack cites `source_file:line` and a provenance for every
row a human is asked to trust; an invented one cites nothing and reads identically.

**These tests name no reader's internals.** They assert what a caller of either reader
observes, which is what lets the same test hold whichever module the shared rule lives
in — and is why the composition test below catches one reader quietly opting out.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.graph.core.lib.emit import read_jsonl, write_jsonl  # noqa: E402
from tools.graph.core.model import Edge, ExtractResult, Node, Provenance  # noqa: E402
from tools.graph.core.store.load import rows_to_model  # noqa: E402

#: A complete edge row, exactly as `Edge.to_jsonl` writes one. Every test below removes
#: or nulls ONE field of it, so the row is otherwise beyond reproach and the refusal can
#: only be about the field under test.
GOOD_EDGE = {"src": "svc:a", "type": "CALLS", "dst": "svc:b", "source_file": "a/b.txt",
             "line": 12, "extractor": "demo", "confidence": 0.9,
             "provenance": "EXTRACTED", "canonical": True, "props": {}}

GOOD_NODE = {"id": "svc:a", "type": "svc", "props": {"component": "a"}}


def _shard(tmp_path: Path, *rows: dict) -> Path:
    """A shard file whose records are `rows`, header first, written as the writer writes."""
    path = tmp_path / "graph.jsonl"
    lines = [json.dumps({"kind": "header", "schema_version": 1, "extractor": "demo"})]
    lines += [json.dumps(r) for r in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _read_via_emit(tmp_path: Path, *rows: dict) -> ExtractResult:
    return read_jsonl(_shard(tmp_path, *rows))


def _read_via_store(tmp_path: Path, *rows: dict):
    """The store loader's real composition: `impact.load_graph` splitting, then the model.

    Going through `load_graph` rather than hand-splitting is deliberate — it is the ONE
    reader `load.py`'s docstring names, so a refusal proven here is the refusal a caller
    of `store.load.load()` gets.
    """
    from tools.graph.impact import load_graph

    _shard(tmp_path, *rows)
    node_rows, edge_rows = load_graph(tmp_path)
    return rows_to_model(node_rows, edge_rows)


# ── the defect: absent is not a value ──────────────────────────────────────────


@pytest.mark.parametrize("field", ["provenance", "line"])
def test_an_edge_row_missing_a_promised_field_is_refused_by_the_shard_reader(
        tmp_path: Path, field: str):
    """Absent must not become the highest-trust value, nor a meaningful line 0."""
    row = {"kind": "edge", **{k: v for k, v in GOOD_EDGE.items() if k != field}}
    with pytest.raises(ValueError) as exc:
        _read_via_emit(tmp_path, row)
    message = str(exc.value)
    assert repr(field) in message, f"the refusal must NAME the missing field: {message}"
    assert "edge" in message, f"the refusal must name the row's kind: {message}"
    assert "graph.jsonl:2" in message, \
        f"the refusal must locate the row in the shard, by line: {message}"


@pytest.mark.parametrize("field", ["provenance", "line"])
def test_an_edge_row_missing_a_promised_field_is_refused_by_the_store_loader(
        tmp_path: Path, field: str):
    """The same row, the same refusal, at the other reader — or the two have drifted."""
    row = {"kind": "edge", **{k: v for k, v in GOOD_EDGE.items() if k != field}}
    with pytest.raises(ValueError) as exc:
        _read_via_store(tmp_path, row)
    message = str(exc.value)
    assert repr(field) in message, f"the refusal must NAME the missing field: {message}"
    assert "edge" in message, f"the refusal must name the row's kind: {message}"


@pytest.mark.parametrize("field", ["provenance", "line"])
def test_an_explicitly_null_promised_field_is_still_refused_at_both_readers(
        tmp_path: Path, field: str):
    """The CONTROL for the fix's direction.

    Null already raised — `Provenance(None)` and `None < 0` both blow up — but by accident
    and with a message about types. Absent is now treated exactly like null, so null must
    keep raising, and both must arrive at the same named refusal rather than a traceback
    about `NoneType`.
    """
    row = {"kind": "edge", **GOOD_EDGE, field: None}
    for reader in (_read_via_emit, _read_via_store):
        with pytest.raises(ValueError) as exc:
            reader(tmp_path, row)
        message = str(exc.value)
        assert repr(field) in message, \
            f"{reader.__name__} must NAME the null field, not its type: {message}"


# ── the control: a kind that never promised the field ──────────────────────────


def test_a_node_row_loads_although_it_carries_neither_line_nor_provenance(tmp_path: Path):
    """`Node` has three fields — id, type, props. It never promised the other two.

    Refusing a node for lacking `line` would be the mirror of the defect: treating "does
    not apply" as a defect, having just stopped treating a defect as a value.
    """
    result = _read_via_emit(tmp_path, {"kind": "node", **GOOD_NODE})
    assert [n.id for n in result.nodes] == ["svc:a"]

    nodes, edges = _read_via_store(tmp_path, {"kind": "node", **GOOD_NODE})
    assert [n.id for n in nodes] == ["svc:a"] and edges == []


def test_an_edge_whose_line_is_genuinely_zero_still_loads(tmp_path: Path):
    """0 is a claim about the file rather than a place in it, and it is a legal one.

    This is the reason an absent `line` could not simply keep defaulting to 0: the value
    the reader invented is a value the model gives a meaning.
    """
    row = {"kind": "edge", **GOOD_EDGE, "line": 0}
    assert _read_via_emit(tmp_path, row).edges[0].line == 0
    assert _read_via_store(tmp_path, row)[1][0].line == 0


# ── the weld: one rule, two readers ────────────────────────────────────────────


def test_the_real_writers_output_is_read_identically_by_both_readers(tmp_path: Path):
    """Composition. The writer's own bytes, through each reader, must agree.

    Two readers restating one rule is how these two drifted in the first place, so the
    guard is not "each reader refuses" but "both readers answer the same about the same
    bytes" — which fails the moment one of them keeps a private copy of the rule.
    """
    edge = Edge(src="svc:a", type="CALLS", dst="svc:b", source_file="a/b.txt", line=12,
                extractor="demo", confidence=0.9, provenance=Provenance.INFERRED)
    written = tmp_path / "shard" / "graph.jsonl"
    write_jsonl(written, ExtractResult(nodes=[Node(id="svc:a", type="svc")], edges=[edge]),
                extractor="demo")

    via_emit = read_jsonl(written)
    from tools.graph.impact import load_graph
    node_rows, edge_rows = load_graph(written.parent)
    nodes, edges = rows_to_model(node_rows, edge_rows)

    assert via_emit.edges == edges == [edge], \
        "the writer's own output must survive BOTH readers unchanged"
    assert [n.id for n in via_emit.nodes] == [n.id for n in nodes] == ["svc:a"]


@pytest.mark.parametrize("field", ["provenance", "line"])
def test_neither_reader_accepts_what_the_other_refuses(tmp_path: Path, field: str):
    """The weld itself: take the writer's own row, drop one promised field, and require
    that BOTH readers refuse it. A reader that keeps its own copy of the rule — or skips
    the shared one — fails here even when its own test file is green."""
    edge = Edge(src="svc:a", type="CALLS", dst="svc:b", source_file="a/b.txt", line=12,
                extractor="demo", provenance=Provenance.INFERRED)
    row = json.loads(edge.to_jsonl())
    assert field in row, "the writer promises this field on every edge row it writes"
    row.pop(field)

    refusals = {}
    for name, reader in (("emit.read_jsonl", _read_via_emit),
                         ("store.load.rows_to_model", _read_via_store)):
        with pytest.raises(ValueError) as exc:
            reader(tmp_path, row)
        refusals[name] = str(exc.value)

    for name, message in refusals.items():
        assert repr(field) in message, f"{name} did not name {field!r}: {message}"
