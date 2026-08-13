"""Three further golden chains, distinct edge classes (item 14).

Item 4's chain walks calls, exposure, messaging and storage; these walk the classes it
does not — module dependency, test coverage, deployment and configuration — and the
disjointness is itself asserted: the extractors under test here overlap item 4's six
nowhere, so each of these chains fails for a reason no existing golden test could catch.

Assertions are hand-written per hop against `EXPECTED.md`'s item-14 tables, never a
snapshot: a blob gets regenerated on failure by reflex, a hand-written assertion does not.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import tools.graph.build as B
from tools.graph.tests.chain_further import (
    ALL_HOPS, CHAIN_A, CHAIN_B, CHAIN_C, DEPLOY_LEGACY, ITEM_4_EXTRACTORS, MOD_SHARED,
    TEST_ID, find)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "orderflow"


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("further-golden")
    report = B.build(FIXTURE / "workspace.toml", out, run_id="golden-further")
    edges = [json.loads(line)
             for line in (out / "graph.jsonl").read_text(encoding="utf-8").splitlines()
             if '"kind": "edge"' in line]
    return report, edges


@pytest.mark.parametrize("chain, hop, type_, src, dst, file, line", ALL_HOPS,
                         ids=[f"{h[0]}{h[1]}" for h in ALL_HOPS])
def test_every_hop_resolves_where_the_answer_key_says(built, chain, hop, type_, src, dst,
                                                      file, line):
    _, edges = built
    hits = find(edges, type_, src, dst)
    assert len(hits) == 1, f"hop {chain}{hop}: expected exactly one {type_} {src} -> {dst}"
    assert hits[0]["source_file"].endswith(file), (
        f"hop {chain}{hop} is declared in {hits[0]['source_file']}, not {file}")
    assert hits[0]["line"] == line, (
        f"EXPECTED.md cites {file}:{line} for hop {chain}{hop}, "
        f"the edge says {hits[0]['line']}")


def test_the_three_chains_use_none_of_the_kill_gates_extractors(built):
    """The item is "distinct edge classes", asserted structurally: if a hop here were
    emitted by one of item 4's six extractors, it would be re-walking a mechanism the
    kill gate already exercises and this file would be measuring redundancy."""
    _, edges = built
    used = {find(edges, t, s, d)[0]["extractor"] for _, _, t, s, d, _, _ in ALL_HOPS}
    assert len(used) >= 4, f"three chains from {used} is fewer mechanisms than claimed"
    assert not (used & ITEM_4_EXTRACTORS), (
        f"{used & ITEM_4_EXTRACTORS} already owns a hop of item 4's chain")


# ── chain A: the join is one module node, and the version agreement rides it ──
def test_all_three_manifests_meet_at_one_module_node(built):
    """The traversal "if orders-shared changes, who rebuilds?" crosses the module node.
    Split that node — a groupId typo, a version baked into identity — and the question
    stops one hop short of its answer, silently."""
    _, edges = built
    touching = {e["src"] for e in edges
                if e["dst"] == MOD_SHARED and e["type"] in ("DG_DEPENDS_ON", "DG_BUILT_FROM")}
    assert len(touching) == 3, f"expected three services at {MOD_SHARED}, found {touching}"


def test_both_consumers_pin_the_version_the_library_builds(built):
    """The version chain: 2.1.0 declared on both dependency edges, marked `declared`
    rather than `inherited` — the agreement is the claim, and a blank version silently
    inheriting from nowhere would read exactly like a pin."""
    _, edges = built
    for _, hop, type_, src, dst, _, _ in CHAIN_A[:2]:
        props = find(edges, type_, src, dst)[0]["props"]
        assert props["version"] == "2.1.0"
        assert props["version_source"] == "declared"


def test_no_component_depends_on_itself(built):
    """The subtraction trap, pinned: a manifest names its own coordinates before anyone
    else's, and a workspace scan in this project's history reported eight components
    consuming their own submodules exactly this way."""
    _, edges = built
    self_deps = [e for e in edges if e["type"] == "DG_DEPENDS_ON"
                 and e["src"].removeprefix("svc:").split("@")[0]
                 == e["dst"].rsplit(":", 1)[-1]]
    assert not self_deps, f"a component reports itself as its own dependency: {self_deps}"


# ── chain B: the parse tree is the boundary ───────────────────────────────────
def test_the_suite_covers_its_component_and_the_literal_names_the_endpoint(built):
    """B1 at full confidence — the suite covers its component by convention. B2 at 0.8
    with the literal in its props — a spec-shaped claim, weaker than a call on purpose."""
    _, edges = built
    b1 = find(edges, "DG_TESTS", TEST_ID, CHAIN_B[0][4])[0]
    assert b1["confidence"] == 1.0
    b2 = find(edges, "DG_TESTS", TEST_ID, "ep:/orders/v1/list")[0]
    assert b2["confidence"] == 0.8
    assert b2["props"]["literal"] == "/orders/v1/list"


def test_the_comment_mention_produced_nothing(built):
    """The file mentions the path twice; the parse tree keeps one. An edge cited at the
    comment's line would mean the extractor is reading text, not code — the exact
    regex-miss class the source-fact layer exists to retire."""
    _, edges = built
    from_test = [e for e in edges if e["src"] == TEST_ID]
    assert len(from_test) == 2, f"the test file emits exactly B1 and B2, not {from_test}"
    assert not [e for e in from_test if e["line"] == 6], "the comment became an edge"


def test_a_test_is_never_a_caller(built):
    """D5, promoted from decoy to chain invariant: no DG_CALLS from a test node, ever.
    A phantom caller appears in every blast radius forever."""
    _, edges = built
    assert not [e for e in edges
                if e["type"] == "DG_CALLS" and e["src"].startswith("test:")]


def test_the_spec_joins_the_endpoint_the_chain_exposes(built):
    """B2's endpoint node is hop 1/2's endpoint node — one id, one node. A second node
    for the same path would split every impact query on this endpoint."""
    _, edges = built
    exposers = [e for e in edges
                if e["type"] == "DG_EXPOSES" and e["dst"] == "ep:/orders/v1/list"]
    assert exposers, "the endpoint the spec names is the one the chain exposes"


# ── chain C: canonical resolution decides which chart's claim is live ─────────
def test_the_canonical_chart_deploys_at_full_confidence(built):
    """C1 rides D4's adjudication: the workspace's [[canonical]] block names the owner,
    so the canonical tree's claim stays 1.0 and the legacy tree's is damped, not
    dropped — a stale deployment nobody noticed is a finding, not noise."""
    _, edges = built
    c1 = find(edges, "DG_DEPLOYS", CHAIN_C[0][3], CHAIN_C[0][4])[0]
    assert c1["confidence"] == 1.0
    legacy = find(edges, "DG_DEPLOYS", DEPLOY_LEGACY, CHAIN_C[0][4])[0]
    assert legacy["confidence"] == 0.5, "the losing claim is damped, never dropped"


def test_the_configmap_publishes_the_address_the_service_is_reached_by(built):
    """C2 carries the key as declared and the address as published — the host map other
    services resolve against, which is what makes a stale configmap dangerous."""
    _, edges = built
    c2 = find(edges, "DG_CONFIGURES", CHAIN_C[1][3], CHAIN_C[1][4])[0]
    assert c2["props"]["declared_as"] == "ORDERS_API_HOST"
    assert c2["props"]["value"] == "http://orders-api:8080"
