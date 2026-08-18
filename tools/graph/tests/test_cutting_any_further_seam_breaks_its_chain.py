"""Cut each further chain's seam and require exactly that chain to die (item 14).

The golden test alone cannot distinguish a correct extractor from one hard-coded to match
the fixture — both produce a snapshot matching themselves. These cuts are the proof: each
severs the one MECHANISM its chain resolves by, in a copy under `tmp_path`, and the chain
must fail while every other hop — the other two chains and all six of item 4's — survives
untouched. A cut that kills more than its own chain is over-linking; one that kills
nothing was never a seam.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from tools.graph.tests._mutate import build_mutated as _build_mutated
from tools.graph.tests.chain import CHAIN as ITEM_4_CHAIN
from tools.graph.tests.chain_further import (
    ALL_HOPS, MOD_SHARED, TEST_FILE, TEST_ID, present)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "orderflow"


def build_mutated(tmp_path: Path, *args, **kwargs):
    return _build_mutated(FIXTURE, tmp_path, *args, **kwargs)


def hop4_present(edges, hop) -> bool:
    _, type_, src, dst, _, _ = hop
    return bool([e for e in edges
                 if e["type"] == type_ and e["src"] == src and e["dst"] == dst])


#: (chain, hop, the mechanism being severed, file, old, new). Each cut renames one value
#: in place — never deletes a file, never changes a line count — so every surviving hop
#: keeps its cited line.
CUTS = [
    ("A", 3, "manifest identity: the library's own artifactId stops matching the name "
             "its consumers declare, so the module node splits",
     "orders-shared/pom.xml",
     "<artifactId>orders-shared</artifactId>", "<artifactId>orders-shred00</artifactId>"),
    ("B", 2, "the parse tree: the string literal stops naming the chain's endpoint "
             "(the comment still does, and a comment is not a claim)",
     f"orders-api/{TEST_FILE}",
     '"/orders/v1/list"', '"/orders/v9/list"'),
    ("C", 1, "chart precedence: service.name stops naming the deployed service",
     "deploy-charts/charts/orderflow/values.yaml",
     "name: orders-api", "name: orders-gate1"),
    ("C", 2, "the host map: the configmap key changes spelling, so the relaxed key "
             "resolves to a different envkey node",
     "deploy-charts/charts/orderflow/templates/configmap.yaml",
     "ORDERS_API_HOST", "ORDERS_API_HOSTX"),
]


@pytest.mark.parametrize("chain, hop, why, rel, old, new", CUTS,
                         ids=[f"{c[0]}{c[1]}" for c in CUTS])
def test_cutting_a_seam_kills_its_hop_and_nothing_else(tmp_path, chain, hop, why, rel,
                                                       old, new):
    report, edges, _ = build_mutated(tmp_path, rel, old, new)
    assert report["status"] == "ok", "a cut must degrade the graph, not the build"
    for h in ALL_HOPS:
        if (h[0], h[1]) == (chain, hop):
            assert not present(edges, h), f"{chain}{hop} survived its own cut: {why}"
        else:
            assert present(edges, h), f"cutting {chain}{hop} also killed {h[0]}{h[1]}"
    for h in ITEM_4_CHAIN:
        assert hop4_present(edges, h), f"cutting {chain}{hop} reached item 4's hop {h[0]}"


def test_a_component_made_to_depend_on_itself_reports_no_dependency_at_all(tmp_path):
    """The subtraction trap, exercised: rewrite orders-api's dependency to name its own
    coordinates. The correct answer is NO edge — not a self-loop, which is how a
    workspace scan in this project's history reported eight components consuming their
    own submodules."""
    _, edges, _ = build_mutated(
        tmp_path, "orders-api/pom.xml",
        "<artifactId>orders-shared</artifactId>", "<artifactId>orders-api</artifactId>")
    from_api = [e for e in edges if e["type"] == "DG_DEPENDS_ON"
                and e["src"].startswith("svc:orders-api")]
    assert not from_api, f"a self-dependency became an edge: {from_api}"
    still = [e for e in edges if e["type"] == "DG_DEPENDS_ON" and e["dst"] == MOD_SHARED]
    # Two since item 9b: orders-worker's pom AND orders-notifier's gradle script. The
    # maven mutation must not disturb the other build system's edge — the cut is local
    # to the manifest it rewrote.
    assert len(still) == 2, "the other consumers' dependencies must survive"
    assert {e["extractor"] for e in still} == {"maven-modules", "gradle-modules"}


def test_the_comment_mention_is_not_a_seam(tmp_path):
    """The control that makes cut B mean something: rewrite the path where it appears in
    the COMMENT and the graph must not move at all. If this changes anything, the
    extractor is reading text rather than a parse tree, and cut B was measuring the
    wrong boundary."""
    _, mutated, _ = build_mutated(
        tmp_path, f"orders-api/{TEST_FILE}",
        "mentions /orders/v1/list twice", "mentions /orders/v9/list twice")
    _, untouched, _ = build_mutated(tmp_path / "control")
    key = lambda e: (e["type"], e["src"], e["dst"], e["source_file"], e["line"])
    assert sorted(map(key, mutated)) == sorted(map(key, untouched)), \
        "rewriting a comment changed the graph"


def test_the_literal_cut_does_not_invent_a_caller(tmp_path):
    """After cut B the literal names /orders/v9/list — an endpoint nobody exposes. The
    spec edge may point at the new stub; what must not appear is a DG_CALLS from the
    test, there or anywhere."""
    _, edges, _ = build_mutated(
        tmp_path, f"orders-api/{TEST_FILE}", '"/orders/v1/list"', '"/orders/v9/list"')
    assert not [e for e in edges
                if e["type"] == "DG_CALLS" and e["src"].startswith("test:")]
    assert [e for e in edges if e["src"] == TEST_ID and e["dst"] == "ep:/orders/v9/list"], \
        "the literal still names SOME endpoint; losing the edge entirely means the cut " \
        "severed the extractor, not the seam"
