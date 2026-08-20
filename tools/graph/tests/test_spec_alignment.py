"""Spec alignment: the exposed path is the spec path plus its one context segment.

Item 93. Specs write paths context-free; controllers bake the context path in — so the
two sides of "is this endpoint specified?" meet only when someone applies the segment.
Measured before designed: 195 of 471 spec-only paths on a real estate equal an exposed
path minus its ONE leading segment, and the reverse direction measured zero. The rule
is deliberately narrow: one segment, unique match, and the alignment ADDS a join — it
never rewrites the spec's own claim, and an ambiguous suffix is a recorded row, because
a wrong spec-to-endpoint join would grade the wrong service's contract as covered.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.graph import build as B  # noqa: E402
from tools.graph.core.model import Edge, Node  # noqa: E402
from tools.graph.links import specmatch  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "orderflow"


def expose(path: str, svc: str = "svc:a@a") -> Edge:
    return Edge(src=svc, type="DG_EXPOSES", dst=f"ep:{path}", source_file="C.java",
                line=1, extractor="t")


def specify(path: str, src: str = "cfg:a/openapi.yaml", line: int = 6) -> Edge:
    return Edge(src=src, type="DG_SPECIFIES", dst=f"ep:{path}",
                source_file="openapi.yaml", line=line, extractor="t")


def run(edges: list[Edge]):
    nodes = [Node(id=e.dst, type="ep") for e in edges]
    return specmatch.SpecAlignPass().link(None, nodes, list(edges))


def aligned(res):
    return [e for e in res.edges if e.type == "DG_SPECIFIES"]


def test_one_segment_unique_suffix_aligns(tmp_path):
    res = run([expose("/orders/v1/detail"), specify("/v1/detail", line=18)])
    (edge,) = aligned(res)
    assert edge.dst == "ep:/orders/v1/detail"
    assert edge.src == "cfg:a/openapi.yaml"
    assert edge.line == 18 and edge.source_file == "openapi.yaml"
    assert edge.confidence == 0.8
    assert edge.props["via"] == "context-suffix"
    assert edge.props["segment"] == "/orders"
    assert edge.props["aligned_from"] == "ep:/v1/detail"


def test_two_extra_segments_do_not_align():
    res = run([expose("/x/y/v1/detail"), specify("/v1/detail")])
    assert not aligned(res) and not res.unresolved, \
        "one segment is the measured rule; two is a different claim nobody registered"


def test_an_ambiguous_suffix_is_a_row_never_a_guess():
    res = run([expose("/a/v1/list"), expose("/b/v1/list"), specify("/v1/list")])
    assert not aligned(res)
    (row,) = res.unresolved
    assert row["kind"] == "ambiguous-spec-alignment"
    assert row["candidates"] == 2


def test_a_spec_only_path_with_no_suffix_match_is_untouched():
    res = run([expose("/orders/v1/list"), specify("/v1/history")])
    assert not aligned(res) and not res.unresolved


def test_an_already_matched_spec_gains_nothing():
    res = run([expose("/v1/list"), specify("/v1/list")])
    assert not aligned(res), "alignment exists for the unmatched; a matched spec is done"


def test_every_spec_edge_to_the_same_path_aligns(tmp_path):
    res = run([expose("/orders/v1/detail"),
               specify("/v1/detail", src="cfg:a/openapi.yaml", line=6),
               specify("/v1/detail", src="cfg:b/other.yaml", line=9)])
    assert sorted(e.src for e in aligned(res)) == ["cfg:a/openapi.yaml",
                                                   "cfg:b/other.yaml"]


# -- the fixture, held to EXPECTED.md ------------------------------------------------------


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("s93")
    B.build(FIXTURE / "workspace.toml", out, run_id="s93")
    rows = [json.loads(l) for l
            in (out / "graph.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    return [r for r in rows if "src" in r]


def test_s1_the_fixture_alignment_lands_as_claimed(built):
    (edge,) = [e for e in built if e["type"] == "DG_SPECIFIES"
               and e["dst"] == "ep:/orders/v1/detail/{}"]
    assert edge["extractor"] == "spec-align"
    assert edge["source_file"].endswith("openapi.yaml") and edge["line"] == 18
    assert edge["confidence"] == 0.8 and edge["props"]["segment"] == "/orders"


def test_the_unserved_decoy_stays_unaligned(built):
    assert {e["type"] for e in built if e["dst"] == "ep:/orders/v1/{}/history"} == \
        {"DG_SPECIFIES"}
    assert not [e for e in built if e.get("props", {}).get("aligned_from")
                == "ep:/orders/v1/{}/history"], \
        "aligning the specified-and-unserved decoy would erase the fixture's proof " \
        "that a spec's promise is not an exposure"


def test_the_spec_only_claim_survives_beside_the_alignment(built):
    originals = [e for e in built if e["dst"] == "ep:/v1/detail/{}"]
    assert originals and all(e["type"] == "DG_SPECIFIES" for e in originals), \
        "the spec really declares that string; alignment adds a join, never a rewrite"
