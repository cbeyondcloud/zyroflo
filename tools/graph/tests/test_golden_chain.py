"""M0 KILL GATE, half one: the golden chain, from a clean build.

Six hops across three components, each resolved by a different mechanism, every one asserted
against `EXPECTED.md` — a hand-written answer key committed **before any extractor existed**.
That ordering is the whole point. An expectation produced by running the tool asserts only
that the tool is unchanged; it cannot tell a correct extractor from a confidently wrong one,
because both produce a snapshot matching themselves.

**This file alone is not the gate.** It says the chain resolves; it does not say the chain is
being *read*. A golden test that still passes when the fixture is wrong is not reading the
fixture, and that claim is only falsifiable through
`test_cutting_any_seam_breaks_the_chain.py` — cut any seam and the chain must fail, for that
seam's own reason. The two files are one gate and neither half means much alone.

**And the fixture cannot prove recall.** Its author chose what reality looks like here. Item
76 — a build against a developer's own workspace, output to `.tmp/`, with a dated record — is
the separate and equally required other half. Saying either substitutes for the other is the
failure this arrangement exists to prevent.

Assertions are hand-written per hop rather than a blob snapshot, deliberately: a blob gets
regenerated on failure by reflex, and a hand-written assertion does not.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.graph import build as B  # noqa: E402
from tools.graph.core.lib import ids  # noqa: E402
from tools.graph.tests.chain import CHAIN, CHAIN_OWNERS, INERT_COMPONENT, find  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "orderflow"


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """A clean build of the committed fixture — no cache, no prior state, no shortcuts."""
    out = tmp_path_factory.mktemp("golden")
    report = B.build(FIXTURE / "workspace.toml", out, run_id="golden01")
    edges = [json.loads(line)
             for line in (out / "graph.jsonl").read_text(encoding="utf-8").splitlines()
             if '"kind": "edge"' in line]
    unresolved = [json.loads(line) for line in
                  (out / "unresolved.jsonl").read_text(encoding="utf-8").splitlines() if line]
    return report, edges, unresolved


# ── the chain ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("hop, type_, src, dst, file, line", CHAIN,
                         ids=[f"hop{h[0]}" for h in CHAIN])
def test_every_hop_resolves_where_the_answer_key_says(built, hop, type_, src, dst, file, line):
    _, edges, _ = built
    hits = find(edges, type_, src, dst)
    assert len(hits) == 1, f"hop {hop}: expected exactly one {type_} {src} -> {dst}"
    assert hits[0]["source_file"].endswith(file)
    assert hits[0]["line"] == line, f"EXPECTED.md cites {file}:{line} for hop {hop}"


def test_each_hop_is_resolved_by_a_different_mechanism(built):
    """The reason the chain is six hops and not one long one. Each is a different *kind* of
    indirection — a URL table, a concatenated mapping, a property placeholder, a second
    property with a different name, a mapping file, a migration — so cutting any one of them
    fails for its own reason rather than for a shared one."""
    _, edges, _ = built
    by_key = {(t, s, d): find(edges, t, s, d)[0] for _, t, s, d, _, _ in CHAIN}
    extractors = [e["extractor"] for e in by_key.values()]
    assert len(set(extractors)) == 6, f"hops share an extractor: {extractors}"


def test_the_chain_crosses_component_boundaries(built):
    """A chain that stays inside one component proves nothing about a cross-component graph,
    and a fixture small enough to be re-read has to work to leave one.

    Attributed by declaring component, not by path: `source_file` is component-relative and
    carries no component name, so counting path prefixes would measure directory layout.
    """
    _, edges, _ = built
    owners = [find(edges, t, s, d)[0]["props"].get("component")
              or find(edges, t, s, d)[0]["src"].split("@")[-1]
              for _, t, s, d, _, _ in CHAIN]
    assert owners == CHAIN_OWNERS
    assert sum(1 for a, b in zip(owners, owners[1:]) if a != b) == 2


def test_the_producer_and_consumer_are_paired_on_value_not_on_key(built):
    """`EXPECTED.md`'s own point: the two property names deliberately differ. An extractor
    pairing them by key finds nothing here and reports a topic with a producer and no
    consumer — which reads as a real architectural finding rather than as a bug."""
    _, edges, _ = built
    produced = find(edges, "DG_PRODUCES", ids.component_service_id("orders-api"),
                    "topic:order-events-v1")[0]
    consumed = find(edges, "DG_CONSUMES", ids.component_service_id("orders-worker"),
                    "topic:order-events-v1")[0]
    assert produced["props"]["property"] != consumed["props"]["property"]


# ── the decoys: precision, not only recall ────────────────────────────────────


def test_d1_two_spellings_of_one_path_are_one_endpoint(built):
    _, edges, _ = built
    calls = [e for e in edges if e["type"] == "DG_CALLS"
             and e["dst"] == "ep:/orders/v1/detail/{}" and e["src"].startswith("ui:")]
    assert sorted((e["source_file"].rsplit("/", 1)[-1], e["line"]) for e in calls) == [
        ("OrderDetail.jsx", 4), ("urls.js", 5)]


def test_d4_the_contested_service_is_resolved_from_the_workspace_declaration(built):
    _, edges, _ = built
    claims = {e["source_file"]: e for e in edges if e["type"] == "DG_DEPLOYS"}
    winner, loser = claims["charts/orderflow/values.yaml"], claims["legacy/orders-api/values.yaml"]
    assert (winner["canonical"], winner["confidence"]) == (True, 1.0)
    assert (loser["canonical"], loser["confidence"]) == (False, 0.5), "damped, not dropped"


def test_d5_a_path_named_in_a_test_never_becomes_a_call(built):
    _, edges, _ = built
    assert not [e for e in edges if e["type"] == "DG_CALLS" and "Test" in e["source_file"]]
    tested = [e for e in edges
              if e["type"] == "DG_TESTS" and e["dst"] == "ep:/orders/v1/list"]
    assert len(tested) == 1 and tested[0]["line"] == 15, "the literal, not the comment"


def test_there_are_exactly_three_endpoints(built):
    """Every extra one would be an entity nothing serves, arriving in blast radiuses as a
    dependency nobody can find — which is worse than a missing edge, because someone will go
    looking for it."""
    _, edges, _ = built
    assert {e["dst"] for e in edges if e["dst"].startswith("ep:")} == {
        "ep:/orders/v1/list", "ep:/orders/v1/detail/{}", "ep:/inventory/v1/reserve"}


def test_a_call_to_something_the_workspace_does_not_own_is_still_an_edge(built):
    """"We call something we do not own" is exactly the fact blast radius must not lose."""
    _, edges, _ = built
    assert find(edges, "DG_CALLS", ids.component_service_id("orders-api"),
                "ep:/inventory/v1/reserve")


# ── zero silent drops ─────────────────────────────────────────────────────────


def test_unresolved_holds_exactly_the_three_that_were_pre_registered(built):
    """Three — D6, D7, D8 — declared from before any extractor existed. A fourth would mean
    something landed by accident rather than by design; a third missing would mean a drop."""
    _, _, unresolved = built
    assert len(unresolved) == 3
    assert {r["kind"] for r in unresolved} == {"unparsable", "unproduced-topic", "placeholder"}


@pytest.mark.parametrize("kind, file, line", [
    ("unparsable", "broken-mapping.yml", 0),        # D6 — the parse error has no line
    ("unproduced-topic", "orphan-mapping.yml", 6),  # D7
    ("placeholder", "application.properties", 14),  # D8
])
def test_each_unresolved_entry_cites_where_it_came_from(built, kind, file, line):
    _, _, unresolved = built
    row = next(r for r in unresolved if r["kind"] == kind)
    assert row["source_file"].endswith(file) and row["line"] == line
    assert row["why"], "an unresolved row with no reason is a log line, not a finding"


def test_d6_the_malformed_file_did_not_abort_the_build(built):
    """One bad file in one component must not cost a workspace its whole graph, and skipping
    it silently drops real edges with no trace. Neither is acceptable."""
    report, edges, _ = built
    assert report["status"] == "ok" and edges


# ── the build itself ──────────────────────────────────────────────────────────


def test_the_build_is_clean(built):
    report, _, _ = built
    assert report["status"] == "ok"
    assert report["warnings"] == [], f"unexpected warnings: {report['warnings']}"
    assert report["components_unclaimed"] == []


def test_every_component_is_read_by_something(built):
    report, _, _ = built
    assert all(c["claimed_by"] for c in report["components"])


def test_the_profile_is_the_one_the_workspace_selected(built):
    report, _, _ = built
    assert report["workspace"]["profile"].endswith("orderflow")


def test_the_build_is_deterministic(tmp_path_factory):
    """`rebuild == snapshot` doubles as an extractor regression check, so the same workspace
    must serialize byte-identically however many times it is built — and under a different
    run id, since build metadata must stay out of the serialized record."""
    a, b = tmp_path_factory.mktemp("a"), tmp_path_factory.mktemp("b")
    B.build(FIXTURE / "workspace.toml", a, run_id="one")
    B.build(FIXTURE / "workspace.toml", b, run_id="two")
    assert (a / "graph.jsonl").read_bytes() == (b / "graph.jsonl").read_bytes()
    assert (a / "unresolved.jsonl").read_bytes() == (b / "unresolved.jsonl").read_bytes()


def test_the_inert_component_reaches_nothing(built):
    """P5 at the graph level: every edge the negative control takes part in stays inside it.
    If an inert component connects to anything, the extractor is inventing links from
    proximity — the failure that makes a graph plausible and useless."""
    _, edges, _ = built
    for e in edges:
        if INERT_COMPONENT in e["src"] and INERT_COMPONENT not in e["dst"]:
            pytest.fail(f"the inert component reaches {e['dst']} via {e['type']}")
